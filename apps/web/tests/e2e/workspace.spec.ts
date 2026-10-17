import { test, expect } from '@playwright/test';

test('home is responsive and workspace actions are reachable', async ({ page, isMobile }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Character Studio' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'プロジェクト名' })).toBeVisible();
  if (isMobile) expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('409 keeps the edit and presents both safe choices', async ({ page }) => {
  const pixels = 832 * 1216;
  const workspace = { schemaVersion: 1, id: '00000000-0000-0000-0000-000000000002', projectId: 'p', baseVersionId: 'v', baseAssetId: 'a', revision: 1, width: 832, height: 1216,
    mask: Buffer.alloc(pixels).toString('base64'), colorGuide: Buffer.alloc(pixels * 4).toString('base64'), silhouetteGuide: Buffer.alloc(pixels * 4).toString('base64'), featherInwardPx: 3,
    layers: { base: { visible: true, opacity: 1 }, mask: { visible: true, opacity: 0.5 }, colorGuide: { visible: true, opacity: 0.8 }, silhouetteGuide: { visible: true, opacity: 0.8 } } };
  await page.route(`**/api/m2/workspaces/${workspace.id}`, route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'WORKSPACE_CONFLICT', currentRevision: 2 } }) })
    : route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(workspace) }));
  await page.route('**/api/m2/assets/**', route => route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64') }));
  await page.goto(`/workspace/${workspace.id}`);
  const canvas = page.getByTestId('workspace-canvas');
  await expect(canvas.locator('canvas').first()).toBeAttached();
  await expect.poll(async () => Number(await canvas.getAttribute('data-view-scale'))).toBeLessThan(1);
  await canvas.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const point = { bubbles: true, pointerId: 77, pointerType: 'pen', clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
    element.dispatchEvent(new PointerEvent('pointerdown', point));
    element.dispatchEvent(new PointerEvent('pointerup', point));
  });
  await expect(page.getByRole('button', { name: 'Undo' })).toBeEnabled();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '別の端末で先に保存' })).toBeVisible();
  await expect(page.getByRole('button', { name: '最新版を読み直す' })).toBeVisible();
  await expect(page.getByRole('button', { name: '別Workspaceとして保存' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Undo' })).toBeEnabled();
});

test('workspace can draw, undo and redo at the viewport size', async ({ page }) => {
  const pixels = 832 * 1216;
  const zeros = Buffer.alloc(pixels).toString('base64');
  const guides = Buffer.alloc(pixels * 4).toString('base64');
  const workspace = { schemaVersion: 1, id: '00000000-0000-0000-0000-000000000001', projectId: 'p', baseVersionId: 'v', baseAssetId: 'a', revision: 1, width: 832, height: 1216, mask: zeros, colorGuide: guides, silhouetteGuide: guides, featherInwardPx: 3,
    layers: { base: { visible: true, opacity: 1 }, mask: { visible: true, opacity: 0.5 }, colorGuide: { visible: true, opacity: 0.8 }, silhouetteGuide: { visible: true, opacity: 0.8 } } };
  await page.route('**/api/m2/workspaces/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(workspace) }));
  await page.route('**/api/m2/assets/**', route => route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64') }));
  await page.goto(`/workspace/${workspace.id}`);
  await expect(page.getByText('画像編集Workspace')).toBeVisible();
  const canvas = page.getByTestId('workspace-canvas');
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down(); await page.mouse.up();
  await expect(page.getByRole('status')).toContainText(/未保存|保存中|完了/);
  await page.getByRole('button', { name: 'Undo' }).click();
  await page.getByRole('button', { name: 'Redo' }).click();
});

test('second touch cancels an uncommitted stroke', async ({ page }) => {
  const pixels = 832 * 1216;
  const workspace = { schemaVersion: 1, id: '00000000-0000-0000-0000-000000000003', projectId: 'p', baseVersionId: 'v', baseAssetId: 'a', revision: 1, width: 832, height: 1216,
    mask: Buffer.alloc(pixels).toString('base64'), colorGuide: Buffer.alloc(pixels * 4).toString('base64'), silhouetteGuide: Buffer.alloc(pixels * 4).toString('base64'), featherInwardPx: 3,
    layers: { base: { visible: true, opacity: 1 }, mask: { visible: true, opacity: 0.5 }, colorGuide: { visible: true, opacity: 0.8 }, silhouetteGuide: { visible: true, opacity: 0.8 } } };
  await page.route('**/api/m2/workspaces/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(workspace) }));
  await page.route('**/api/m2/assets/**', route => route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64') }));
  await page.goto(`/workspace/${workspace.id}`);
  const canvas = page.getByTestId('workspace-canvas');
  const box = await canvas.boundingBox();
  const x = box!.x + box!.width / 2;
  const y = box!.y + box!.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await canvas.evaluate((element, point) => element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 99, pointerType: 'touch', clientX: point.x + 30, clientY: point.y + 30 })), { x, y });
  await page.mouse.up();
  await canvas.evaluate((element, point) => element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 99, pointerType: 'touch', clientX: point.x + 30, clientY: point.y + 30 })), { x, y });
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
  await expect(page.getByRole('status')).toContainText('完了');
});
