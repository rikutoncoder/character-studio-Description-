// Browser → Next.js → real Python API conflict test, using disposable test data.
import { chromium } from '@playwright/test';

const base = process.env.M3_CONFLICT_URL;
const { M3_CONFLICT_WORKSPACE: workspaceId, M3_CONFLICT_JOB: jobId,
  M3_CONFLICT_CANDIDATE: candidateId, M3_CONFLICT_PROJECT: projectId,
  M3_CONFLICT_HEAD: expectedHead } = process.env;
if (![base, workspaceId, jobId, candidateId, projectId, expectedHead].every(Boolean)) {
  throw new Error('Set all M3_CONFLICT_* environment variables');
}

const browser = await chromium.launch();
const page = await browser.newPage();
try {
  await page.goto(`${base}/generation/${workspaceId}?job=${jobId}`);
  await page.getByText('生成の状態: SUCCEEDED').waitFor({ timeout: 15000 });
  await page.getByRole('button', { name: '厳密保持結果を採用' }).waitFor();
  page.once('dialog', dialog => dialog.accept());
  const responsePromise = page.waitForResponse(response =>
    response.url().endsWith(`/api/candidates/${candidateId}/adopt`));
  await page.getByRole('button', { name: '厳密保持結果を採用' }).click();
  const adoptionResponse = await responsePromise;
  if (adoptionResponse.status() !== 409) {
    throw new Error(`Expected HTTP 409, got ${adoptionResponse.status()}`);
  }
  await page.getByRole('alert').getByText('Project head changed; candidate is stale').waitFor();
  const candidate = await (await page.request.get(`${base}/api/candidates/${candidateId}`)).json();
  const projects = await (await page.request.get(`${base}/api/m2/projects`)).json();
  const project = projects.projects.find(item => item.id === projectId);
  if (candidate.state !== 'STALE' || project?.headVersionId !== expectedHead) {
    throw new Error(`409 changed confirmed data: ${JSON.stringify({ candidate, project })}`);
  }
  console.log(JSON.stringify({ status: adoptionResponse.status(), candidateState: candidate.state,
    unchangedHead: project.headVersionId }));
} finally {
  await browser.close();
}
