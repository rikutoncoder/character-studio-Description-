import { expect, test } from '@playwright/test';

const projectId = '00000000-0000-0000-0000-000000000201';
const firstId = '00000000-0000-0000-0000-000000000202';
const secondId = '00000000-0000-0000-0000-000000000203';
const restoredId = '00000000-0000-0000-0000-000000000204';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64');

function version(id: string, ordinal: number, parentVersionId: string | null,
  restoredFromVersionId: string | null = null) {
  return { schemaVersion: 1, id, projectId, parentVersionId, restoredFromVersionId,
    canonicalAssetId: '00000000-0000-0000-0000-000000000205', ordinal,
    createdAt: '2026-11-17T00:00:00Z', operationType: restoredFromVersionId ? 'RESTORE' : 'IMPORT',
    generationType: null, prompt: null, presetJson: null };
}

test('restoring an earlier image adds a new current version while preserving history', async ({ page }) => {
  const first = version(firstId, 1, null);
  const second = version(secondId, 2, firstId);
  const third = version(restoredId, 3, secondId, firstId);
  let versions = [second, first];
  let head = secondId;
  const requests: Array<{ body: unknown; key: string }> = [];
  await page.route(`**/api/m2/projects/${projectId}/versions`, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ projectId, projectName: 'テスト', headVersionId: head, versions }),
  }));
  await page.route(`**/api/m2/versions/${firstId}/restore`, route => {
    requests.push({ body: JSON.parse(route.request().postData() ?? '{}'),
      key: route.request().headers()['idempotency-key'] });
    versions = [third, second, first]; head = restoredId;
    return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(third) });
  });
  await page.route('**/api/m2/assets/**/content', route => route.fulfill({
    status: 200, contentType: 'image/png', body: png,
  }));
  await page.goto(`/versions/${projectId}`);
  await expect(page.getByRole('heading', { name: '版履歴 — テスト' })).toBeVisible();
  await page.getByRole('navigation', { name: '版の一覧' }).getByRole('button', { name: /版 1/ }).click();
  await expect(page.getByRole('button', { name: 'この版を基に新版を作成' })).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'この版を基に新版を作成' }).click();
  await expect(page.getByRole('status')).toContainText('版 3 を新しく作成しました');
  await expect(page.getByRole('navigation', { name: '版の一覧' }).getByRole('button')).toHaveCount(3);
  await expect(page.getByRole('heading', { name: '版 3 （現在版）' })).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0].body).toEqual({ projectId, expectedHeadVersionId: secondId });
  expect(requests[0].key).toHaveLength(36);
});

test('head conflict keeps previous versions and asks for a fresh choice', async ({ page }) => {
  const first = version(firstId, 1, null);
  const second = version(secondId, 2, firstId);
  await page.route(`**/api/m2/projects/${projectId}/versions`, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ projectId, projectName: 'テスト', headVersionId: secondId,
      versions: [second, first] }),
  }));
  await page.route(`**/api/m2/versions/${firstId}/restore`, route => route.fulfill({
    status: 409, contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'VERSION_CONFLICT', currentHeadVersionId: restoredId } }),
  }));
  await page.route('**/api/m2/assets/**/content', route => route.fulfill({
    status: 200, contentType: 'image/png', body: png,
  }));
  await page.goto(`/versions/${projectId}`);
  await page.getByRole('navigation', { name: '版の一覧' }).getByRole('button', { name: /版 1/ }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'この版を基に新版を作成' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '現在の版が変わりました' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '版の一覧' }).getByRole('button')).toHaveCount(2);
});
