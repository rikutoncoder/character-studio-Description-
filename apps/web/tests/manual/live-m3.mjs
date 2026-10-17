// Real local M3 browser smoke test against an isolated Python runtime.
// M3_E2E_URL and M3_E2E_WORKSPACE are required. This starts one real ComfyUI job.
import { chromium } from '@playwright/test';

const base = process.env.M3_E2E_URL;
const workspaceId = process.env.M3_E2E_WORKSPACE;
const resumedJobId = process.env.M3_E2E_JOB;
if (!base || !workspaceId) throw new Error('Set M3_E2E_URL and M3_E2E_WORKSPACE');

const browser = await chromium.launch();
const page = await browser.newPage();
const started = Date.now();
try {
  await page.goto(`${base}/generation/${workspaceId}${resumedJobId ? `?job=${resumedJobId}` : ''}`);
  if (!resumedJobId) {
    await page.getByRole('textbox', { name: '作りたいもの' }).fill(
      'blue fitted bodice, blue chest clothing, same face, same hair, same pose, high quality anime illustration',
    );
    await page.getByRole('textbox', { name: '避けたいもの' }).fill(
      'different face, different hair, changed hands, changed background, extra limbs',
    );
    await page.getByRole('button', { name: '生成を開始' }).click();
    await page.waitForTimeout(1000);
    const alert = (await page.getByRole('alert').allTextContents()).filter(text => text.trim());
    if (alert.length) throw new Error(`Generation form error: ${alert.join(' | ')}`);
    await page.waitForURL(/\?job=[a-f0-9-]{36}$/, { timeout: 30_000 });
  }
  const jobId = resumedJobId ?? new URL(page.url()).searchParams.get('job');
  console.log(JSON.stringify({ event: 'job-created', jobId, seconds: (Date.now() - started) / 1000 }));
  await page.getByText('生成の状態: SUCCEEDED').waitFor({ timeout: 900_000 });
  const jobResponse = await page.request.get(`${base}/api/generation-jobs/${jobId}`);
  const job = await jobResponse.json();
  const candidateResponse = await page.request.get(`${base}/api/candidates/${job.candidateId}`);
  const candidate = await candidateResponse.json();
  if (candidate.outsideChangedPixels !== 0 || candidate.alphaChangedPixels !== 0) {
    throw new Error(`Strict candidate failed: ${JSON.stringify(candidate)}`);
  }
  for (const label of ['元画像', 'AI生出力', '厳密保持結果']) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await page.getByRole('img', { name: `拡大表示: ${label}` }).waitFor();
  }
  await page.screenshot({ path: '/private/tmp/character-studio-m3-e2e-result.png', fullPage: true });
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '厳密保持結果を採用' }).click();
  await page.getByText('新版として採用済みです。').waitFor({ timeout: 30_000 });
  const adopted = await (await page.request.get(`${base}/api/candidates/${job.candidateId}`)).json();
  if (adopted.state !== 'ADOPTED' || !adopted.adoptedVersionId) {
    throw new Error(`Adoption failed: ${JSON.stringify(adopted)}`);
  }
  console.log(JSON.stringify({ event: 'adopted', jobId, candidateId: job.candidateId,
    versionId: adopted.adoptedVersionId, outsideChangedPixels: candidate.outsideChangedPixels,
    alphaChangedPixels: candidate.alphaChangedPixels, seconds: (Date.now() - started) / 1000 }));
} finally {
  await browser.close();
}
