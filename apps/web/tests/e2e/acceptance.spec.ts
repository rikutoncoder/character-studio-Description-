import { expect, test, type Page } from '@playwright/test';
import { WIDTH, HEIGHT } from '../../src/lib/geometry';
import { strokePixels } from '../../src/lib/raster';

const pixels = WIDTH * HEIGHT;
const zeroMask = Buffer.alloc(pixels).toString('base64');
const zeroGuide = Buffer.alloc(pixels * 4).toString('base64');
const basePng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64');

function strokePath() {
  return [[-16, -12], [0, 0], [16, 12]].map(([dx, dy]) => ({ x: WIDTH / 2 + 0.25 + dx, y: HEIGHT / 2 + 0.25 + dy }));
}

type Wire = {
  schemaVersion: 1; id: string; projectId: string; baseVersionId: string; baseAssetId: string;
  revision: number; width: number; height: number; mask: string; colorGuide: string;
  silhouetteGuide: string; featherInwardPx: number;
  layers: Record<string, { visible: boolean; opacity: number }>;
};

function blank(id: string): Wire {
  return {
    schemaVersion: 1, id, projectId: 'test-project', baseVersionId: 'test-version', baseAssetId: 'test-asset',
    revision: 1, width: WIDTH, height: HEIGHT, mask: zeroMask, colorGuide: zeroGuide,
    silhouetteGuide: zeroGuide, featherInwardPx: 3,
    layers: {
      base: { visible: true, opacity: 1 }, mask: { visible: true, opacity: 0.5 },
      colorGuide: { visible: true, opacity: 0.8 }, silhouetteGuide: { visible: true, opacity: 0.8 },
    },
  };
}

async function mockBackend(page: Page, id: string) {
  const snapshots = new Map<string, Wire>([[id, blank(id)]]);
  const writes: Array<{ id: string; expectedRevision: number }> = [];
  let forceError = false;
  await page.route('**/api/m2/assets/**', route => route.fulfill({ status: 200, contentType: 'image/png', body: basePng }));
  await page.route('**/api/m2/workspaces/**', async route => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split('/');
    const workspaceId = parts[4];
    const method = route.request().method();
    if (method === 'POST' && parts[5] === 'fork') {
      const source = snapshots.get(workspaceId)!;
      const newId = '00000000-0000-0000-0000-000000000099';
      const fork: Wire = { ...source, id: newId, revision: 1 };
      snapshots.set(newId, fork);
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(fork) });
      return;
    }
    const current = snapshots.get(workspaceId);
    if (!current) {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
      return;
    }
    if (method === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(current) });
      return;
    }
    const payload = JSON.parse(route.request().postData() ?? '{}') as Wire & { expectedRevision: number };
    writes.push({ id: workspaceId, expectedRevision: payload.expectedRevision });
    if (forceError) {
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'TEMPORARY' } }) });
    } else if (payload.expectedRevision !== current.revision) {
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'WORKSPACE_CONFLICT', currentRevision: current.revision } }) });
    } else {
      const saved: Wire = { ...payload, id: workspaceId, revision: current.revision + 1 };
      snapshots.set(workspaceId, saved);
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
    }
  });
  return { snapshots, writes, setError(value: boolean) { forceError = value; } };
}

async function drawAtCenter(page: Page, pan = false, zoom = false) {
  const canvas = page.getByTestId('workspace-canvas');
  await expect(canvas.locator('canvas').first()).toBeAttached();
  await expect.poll(async () => Number(await canvas.getAttribute('data-view-scale'))).toBeLessThan(1);
  const box = (await canvas.boundingBox())!;
  const center = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  if (zoom) {
    await canvas.evaluate((element, p) => {
      const fire = (type: string, id: number, x: number) => element.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: id, pointerType: 'touch', clientX: x, clientY: p.y }));
      fire('pointerdown', 20, p.x - 30);
      fire('pointerdown', 21, p.x + 30);
      fire('pointermove', 20, p.x - 60);
      fire('pointermove', 21, p.x + 60);
      fire('pointerup', 20, p.x - 60);
      fire('pointerup', 21, p.x + 60);
    }, center);
    await expect(canvas).toContainText('%');
  }
  let delta = { x: 0, y: 0 };
  if (pan) {
    delta = { x: 36, y: -24 };
    await page.getByRole('button', { name: '移動' }).click();
    await page.mouse.move(center.x, center.y);
    await page.mouse.down();
    await page.mouse.move(center.x + delta.x, center.y + delta.y, { steps: 5 });
    await page.mouse.up();
    await page.getByRole('button', { name: '描く' }).click();
  }
  await canvas.evaluate((element, dimensions) => {
    const rect = element.getBoundingClientRect();
    const scale = Number(element.getAttribute('data-view-scale'));
    const x = Number(element.getAttribute('data-view-x'));
    const y = Number(element.getAttribute('data-view-y'));
    const path = [[-16, -12], [0, 0], [16, 12]];
    path.forEach(([dx, dy], index) => {
      const clientX = rect.left + x + (dimensions.width / 2 + 0.25 + dx) * scale;
      const clientY = rect.top + y + (dimensions.height / 2 + 0.25 + dy) * scale;
      const options = { bubbles: true, pointerId: 71, pointerType: 'pen', clientX, clientY };
      element.dispatchEvent(new PointerEvent(index === 0 ? 'pointerdown' : 'pointermove', options));
      if (index === path.length - 1) element.dispatchEvent(new PointerEvent('pointerup', options));
    });
  }, { width: WIDTH, height: HEIGHT });
}

function maskSummary(mask: Buffer) {
  let count = 0, minX = WIDTH, minY = HEIGHT, maxX = -1, maxY = -1;
  for (let i = 0; i < mask.length; i++) if (mask[i]) {
    count++;
    const x = i % WIDTH, y = Math.floor(i / WIDTH);
    minX = Math.min(minX, x); minY = Math.min(minY, y);
    maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
  }
  return { count, minX, minY, maxX, maxY };
}

test('same image-coordinate stroke persists through save and reload across viewport, zoom, pan and DPR', async ({ page }, testInfo) => {
  const id = '00000000-0000-0000-0000-000000000041';
  const backend = await mockBackend(page, id);
  await page.goto(`/workspace/${id}`);
  await expect(page.getByTestId('workspace-canvas')).toBeVisible();
  const variant = testInfo.project.name;
  await drawAtCenter(page, variant !== 'desktop-chromium', variant !== 'desktop-chromium');
  await expect.poll(() => backend.snapshots.get(id)!.revision, { timeout: 15000 }).toBe(2);
  const savedMask = Buffer.from(backend.snapshots.get(id)!.mask, 'base64');
  const expected = Buffer.alloc(pixels);
  for (const pixel of strokePixels(strokePath(), 24)) expected[pixel] = 255;
  expect(savedMask.equals(expected), JSON.stringify({ actual: maskSummary(savedMask), expected: maskSummary(expected), view: await page.getByTestId('workspace-canvas').innerText() })).toBe(true);
  await page.reload();
  await expect(page.getByRole('status')).toContainText('完了');
  expect(Buffer.from(backend.snapshots.get(id)!.mask, 'base64').equals(expected)).toBe(true);
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
  expect(backend.writes).toEqual([{ id, expectedRevision: 1 }]);
});

test('both guides are erased with mask and restored together by undo and redo', async ({ page }) => {
  const id = '00000000-0000-0000-0000-000000000042';
  const backend = await mockBackend(page, id);
  await page.goto(`/workspace/${id}`);
  await drawAtCenter(page);
  await expect(page.getByRole('button', { name: 'Undo' })).toBeEnabled();
  await page.getByRole('button', { name: '仮着色ガイド' }).click();
  await drawAtCenter(page);
  await page.getByRole('button', { name: 'シルエットガイド' }).click();
  await drawAtCenter(page);
  await page.getByRole('button', { name: 'マスク', exact: true }).click();
  await page.getByRole('button', { name: '消しゴム' }).click();
  await drawAtCenter(page);
  const centerIndex = (HEIGHT / 2) * WIDTH + WIDTH / 2;
  await page.getByRole('button', { name: 'Undo' }).click();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => backend.snapshots.get(id)!.revision, { timeout: 15000 }).toBeGreaterThan(1);
  let saved = backend.snapshots.get(id)!;
  expect(Buffer.from(saved.mask, 'base64')[centerIndex]).toBe(255);
  expect(Buffer.from(saved.colorGuide, 'base64')[centerIndex * 4 + 3]).toBe(255);
  expect(Buffer.from(saved.silhouetteGuide, 'base64')[centerIndex * 4 + 3]).toBe(255);
  await page.getByRole('button', { name: 'Redo' }).click();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => backend.snapshots.get(id)!.revision, { timeout: 15000 }).toBeGreaterThan(saved.revision);
  saved = backend.snapshots.get(id)!;
  expect(Buffer.from(saved.mask, 'base64')[centerIndex]).toBe(0);
  expect(Buffer.from(saved.colorGuide, 'base64')[centerIndex * 4 + 3]).toBe(0);
  expect(Buffer.from(saved.silhouetteGuide, 'base64')[centerIndex * 4 + 3]).toBe(0);
});

test('second pointer cancels drawing until every pointer is released', async ({ page }) => {
  const id = '00000000-0000-0000-0000-000000000043';
  const backend = await mockBackend(page, id);
  await page.goto(`/workspace/${id}`);
  const canvas = page.getByTestId('workspace-canvas');
  await expect(canvas.locator('canvas').first()).toBeAttached();
  const box = (await canvas.boundingBox())!;
  const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await page.mouse.move(point.x, point.y);
  await page.mouse.down();
  await canvas.evaluate((element, p) => element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 99, pointerType: 'touch', clientX: p.x + 40, clientY: p.y })), point);
  await canvas.evaluate((element, p) => element.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 99, pointerType: 'touch', clientX: p.x + 80, clientY: p.y + 20 })), point);
  await page.mouse.up();
  await canvas.evaluate((element, p) => element.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 99, pointerType: 'touch', clientX: p.x + 100, clientY: p.y + 20 })), point);
  await canvas.evaluate((element, p) => element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 99, pointerType: 'touch', clientX: p.x + 100, clientY: p.y + 20 })), point);
  await page.waitForTimeout(850);
  expect(backend.snapshots.get(id)!.revision).toBe(1);
  expect(Buffer.from(backend.snapshots.get(id)!.mask, 'base64').equals(Buffer.alloc(pixels))).toBe(true);
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
});

test('IndexedDB draft is offered after failed autosave and can be restored', async ({ page }) => {
  const id = '00000000-0000-0000-0000-000000000044';
  const backend = await mockBackend(page, id);
  backend.setError(true);
  await page.goto(`/workspace/${id}`);
  await drawAtCenter(page);
  await expect(page.getByRole('status')).toContainText('エラー', { timeout: 15000 });
  const backup = await page.evaluate(async workspaceId => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('character-studio-m2-recovery', 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return new Promise<{ mask: string; expectedRevision: number } | undefined>((resolve, reject) => {
      const request = db.transaction('drafts').objectStore('drafts').get(workspaceId);
      request.onsuccess = () => { resolve(request.result); db.close(); };
      request.onerror = () => reject(request.error);
    });
  }, id);
  expect(backup?.expectedRevision).toBe(1);
  expect(Buffer.from(backup!.mask, 'base64').some(value => value === 255)).toBe(true);
  backend.setError(false);
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect.poll(() => backend.snapshots.get(id)!.revision, { timeout: 15000 }).toBe(2);
  expect(backend.snapshots.get(id)!.mask).toBe(backup!.mask);
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
});

test('409 reload discards only with confirmation; fork preserves unsaved pixels', async ({ page }) => {
  const id = '00000000-0000-0000-0000-000000000045';
  const backend = await mockBackend(page, id);
  await page.goto(`/workspace/${id}`);
  await drawAtCenter(page);
  const localMask = Buffer.alloc(pixels);
  for (const pixel of strokePixels(strokePath(), 24)) localMask[pixel] = 255;
  const external = { ...backend.snapshots.get(id)!, revision: 2, mask: zeroMask };
  backend.snapshots.set(id, external);
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('競合');
  await expect(page.getByRole('button', { name: 'Undo' })).toBeEnabled();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '最新版を読み直す' }).click();
  await expect(page.getByRole('status')).toContainText('競合');
  await page.getByRole('button', { name: '別Workspaceとして保存' }).click();
  const forkId = '00000000-0000-0000-0000-000000000099';
  await expect(page).toHaveURL(new RegExp(`/workspace/${forkId}$`));
  expect(Buffer.from(backend.snapshots.get(forkId)!.mask, 'base64').equals(localMask)).toBe(true);
  expect(backend.snapshots.get(id)!.mask).toBe(zeroMask);

  // A separate conflict exercises the other user-selected resolution.
  const secondId = '00000000-0000-0000-0000-000000000046';
  backend.snapshots.set(secondId, blank(secondId));
  await page.goto(`/workspace/${secondId}`);
  await drawAtCenter(page);
  backend.snapshots.set(secondId, { ...backend.snapshots.get(secondId)!, revision: 2 });
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('競合');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '最新版を読み直す' }).click();
  await expect(page.getByRole('status')).toContainText('完了');
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
  expect(backend.snapshots.get(secondId)!.revision).toBe(2);
});

test('large stroke records UI latency, browser heap when available and recovery storage', async ({ page, browserName }, testInfo) => {
  test.setTimeout(120000);
  const id = '00000000-0000-0000-0000-000000000047';
  const backend = await mockBackend(page, id);
  await page.goto(`/workspace/${id}`);
  const canvas = page.getByTestId('workspace-canvas');
  await expect(canvas.locator('canvas').first()).toBeAttached();
  const range = page.getByRole('slider', { name: 'ブラシ直径' });
  await range.focus();
  await range.press('End');
  await expect(page.getByText('ブラシ直径: 256画像px')).toBeVisible();
  const cdp = browserName === 'chromium' ? await page.context().newCDPSession(page) : null;
  if (cdp) await cdp.send('Performance.enable');
  const heapBytes = async () => {
    if (!cdp) return null;
    const metrics = (await cdp.send('Performance.getMetrics')).metrics as Array<{ name: string; value: number }>;
    return metrics.find(metric => metric.name === 'JSHeapUsedSize')?.value ?? null;
  };
  const beforeHeap = await heapBytes();
  const beforeUsage = await page.evaluate(async () => (await navigator.storage.estimate()).usage ?? null);
  const points = await canvas.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const scale = Number(element.getAttribute('data-view-scale'));
    const x = Number(element.getAttribute('data-view-x'));
    const y = Number(element.getAttribute('data-view-y'));
    const map = (imageX: number, imageY: number) => ({ x: rect.left + x + imageX * scale, y: rect.top + y + imageY * scale });
    return { start: map(150, 150), end: map(700, 1050) };
  });
  await page.mouse.move(points.start.x, points.start.y);
  await page.mouse.down();
  await page.mouse.move(points.end.x, points.end.y, { steps: 60 });
  const releaseStarted = performance.now();
  await page.mouse.up();
  const pointerUpReturnMs = performance.now() - releaseStarted;
  const afterStrokeHeap = await heapBytes();
  await expect.poll(() => backend.snapshots.get(id)!.revision, { timeout: 60000 }).toBe(2);
  await expect(page.getByRole('status')).toContainText('完了');
  const savedElapsedMs = performance.now() - releaseStarted;
  const afterSaveHeap = await heapBytes();
  const savedMask = Buffer.from(backend.snapshots.get(id)!.mask, 'base64');
  const editedPixels = savedMask.reduce((count, value) => count + Number(value === 255), 0);
  expect(editedPixels).toBeGreaterThan(100000);

  backend.setError(true);
  await page.getByRole('button', { name: '消しゴム' }).click();
  await drawAtCenter(page);
  await expect(page.getByRole('status')).toContainText('エラー', { timeout: 15000 });
  const storage = await page.evaluate(async workspaceId => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('character-studio-m2-recovery', 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const draft = await new Promise<unknown>((resolve, reject) => {
      const request = db.transaction('drafts').objectStore('drafts').get(workspaceId);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    db.close();
    return { logicalBytes: new TextEncoder().encode(JSON.stringify(draft)).byteLength, estimatedUsageBytes: (await navigator.storage.estimate()).usage ?? null };
  }, id);
  const metrics = {
    environment: testInfo.project.name,
    imageSize: `${WIDTH}x${HEIGHT}`,
    brushDiameterImagePx: 256,
    pathImageCoordinates: [[150, 150], [700, 1050]],
    pointerMoveSteps: 60,
    editedPixels,
    pointerUpReturnMs: Math.round(pointerUpReturnMs),
    strokeToSavedMs: Math.round(savedElapsedMs),
    jsHeapBeforeMiB: beforeHeap === null ? null : +(beforeHeap / 1048576).toFixed(2),
    jsHeapAfterStrokeMiB: afterStrokeHeap === null ? null : +(afterStrokeHeap / 1048576).toFixed(2),
    jsHeapAfterSaveMiB: afterSaveHeap === null ? null : +(afterSaveHeap / 1048576).toFixed(2),
    indexedDbLogicalMiB: +(storage.logicalBytes / 1048576).toFixed(2),
    storageEstimateBeforeMiB: beforeUsage === null ? null : +(beforeUsage / 1048576).toFixed(2),
    storageEstimateAfterMiB: storage.estimatedUsageBytes === null ? null : +(storage.estimatedUsageBytes / 1048576).toFixed(2),
  };
  await testInfo.attach('large-stroke-metrics.json', { body: Buffer.from(JSON.stringify(metrics, null, 2)), contentType: 'application/json' });
  console.log(`M2_PERF ${JSON.stringify(metrics)}`);
});
