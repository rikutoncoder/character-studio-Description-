import { expect, test } from '@playwright/test';

const workspaceId = '00000000-0000-0000-0000-000000000101';
const jobId = '00000000-0000-0000-0000-000000000102';
const candidateId = '00000000-0000-0000-0000-000000000103';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64');

test('generation setup, strict-only adoption, and conflict message', async ({ page }) => {
  const requests: Record<string, unknown>[] = [];
  await page.route('**/api/m2/workspaces/**', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      id: workspaceId, projectId: 'project', baseVersionId: 'version',
      baseAssetId: 'asset', revision: 2, schemaVersion: 1, width: 832, height: 1216,
      mask: '', colorGuide: '', silhouetteGuide: '', featherInwardPx: 3, layers: {},
    }),
  }));
  await page.route('**/api/m2/assets/**', route => route.fulfill({ status: 200, contentType: 'image/png', body: png }));
  await page.route('**/api/generation-jobs/**/events', route => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }));
  await page.route('**/api/generation-jobs', route => {
    requests.push(JSON.parse(route.request().postData() ?? '{}'));
    return route.fulfill({ status: 202, contentType: 'application/json',
      body: JSON.stringify({ id: jobId, state: 'SUCCEEDED', candidateId, attempt: { number: 1, seed: 42, error_code: null }, queuePosition: 0 }) });
  });
  await page.route('**/api/candidates/**', route => route.fulfill(route.request().url().endsWith('/adopt') ? {
    status: 409, contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'GENERATION_CONFLICT', message: 'Project head changed' } }),
  } : {
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      id: candidateId, state: 'REVIEW_PENDING', sourceVersionId: 'version',
      sourceAssetId: 'source', rawAssetId: 'raw', strictAssetId: 'strict',
      maskAssetId: 'mask', outsideChangedPixels: 0, alphaChangedPixels: 0,
    }),
  }));
  await page.goto('/generation/' + workspaceId);
  await page.getByRole('textbox', { name: '作りたいもの' }).fill('blue jacket');
  await page.getByRole('button', { name: '生成を開始' }).click();
  await expect(page.getByText('生成の状態: SUCCEEDED')).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ workspaceId, expectedRevision: 2, operationType: 'COLOR_CHANGE' });
  await expect(page.getByText('AI生出力（採用不可）')).toBeVisible();
  await expect(page.getByRole('button', { name: '厳密保持結果を採用' })).toHaveCount(1);
  page.on('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '厳密保持結果を採用' }).click();
  await expect(page.getByText('Project head changed')).toBeVisible();
});

test('a status refresh does not repeatedly recreate the event stream', async ({ page }) => {
  let subscriptions = 0;
  await page.route('**/api/m2/workspaces/**', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ id: workspaceId, projectId: 'project', baseVersionId: 'version',
      baseAssetId: 'asset', revision: 2, schemaVersion: 1, width: 832, height: 1216,
      mask: '', colorGuide: '', silhouetteGuide: '', featherInwardPx: 3, layers: {} }),
  }));
  await page.route('**/api/generation-jobs/**', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ id: jobId, state: 'RUNNING', candidateId: null,
      attempt: { number: 1, seed: 42, error_code: null }, queuePosition: 0 }),
  }));
  await page.route('**/api/generation-jobs/**/events', route => {
    subscriptions++;
    return route.fulfill({ status: 200, contentType: 'text/event-stream',
      body: 'id: 1\nevent: job.status.changed\ndata: {"state":"RUNNING"}\n\n' });
  });
  await page.goto(`/generation/${workspaceId}?job=${jobId}`);
  await expect(page.getByText('生成の状態: RUNNING')).toBeVisible();
  await expect.poll(() => subscriptions).toBeGreaterThan(0);
  await page.waitForTimeout(1500);
  expect(subscriptions).toBeLessThanOrEqual(2);
});

test('event stream reconnect restores the current job state', async ({ page }) => {
  let state = 'RUNNING';
  let subscriptions = 0;
  const lastEventIds: string[] = [];
  await page.route('**/api/m2/workspaces/**', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ id: workspaceId, projectId: 'project', baseVersionId: 'version',
      baseAssetId: 'asset', revision: 2, schemaVersion: 1, width: 832, height: 1216,
      mask: '', colorGuide: '', silhouetteGuide: '', featherInwardPx: 3, layers: {} }),
  }));
  await page.route('**/api/generation-jobs/**', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ id: jobId, state, candidateId: null,
      attempt: { number: 1, seed: 42, error_code: null }, queuePosition: 0 }),
  }));
  await page.route('**/api/generation-jobs/**/events', route => {
    subscriptions++;
    lastEventIds.push(route.request().headers()['last-event-id'] ?? '');
    if (subscriptions > 1) state = 'SUCCEEDED';
    return route.fulfill({ status: 200, contentType: 'text/event-stream',
      body: `retry: 100\nid: ${subscriptions}\nevent: job.status.changed\ndata: {"state":"${state}"}\n\n` });
  });
  await page.goto(`/generation/${workspaceId}?job=${jobId}`);
  await expect(page.getByText('生成の状態: SUCCEEDED')).toBeVisible({ timeout: 10000 });
  expect(subscriptions).toBeGreaterThanOrEqual(2);
  expect(lastEventIds[1]).toBe('1');
});
