import { describe, expect, it } from 'vitest';
import { applyStroke, emptyBuffers, replayPatch, strokePixels } from './raster';
import { EditHistory, type Layers } from './history';
import { fittedView, imageToScreen, screenToImage } from './geometry';

const layers: Layers = { base: { visible: true, opacity: 1 }, mask: { visible: true, opacity: 0.5 }, colorGuide: { visible: true, opacity: 0.8 }, silhouetteGuide: { visible: true, opacity: 0.8 } };
describe('deterministic raster and undo', () => {
  it('one-pixel brush paints at integer coordinates and clips at the image edge', () => {
    expect(strokePixels([{ x: 10, y: 10 }], 1)).toContain(10 * 832 + 10);
    expect(strokePixels([{ x: -1, y: 10 }], 1)).toEqual([]);
  });
  it('produces identical pixel sets across screen sizes and zoom without DPR dependence', () => {
    const points = [{ x: 200, y: 300 }, { x: 260, y: 350 }, { x: 300, y: 390 }];
    const expected = strokePixels(points, 24);
    for (const [w, h] of [[1440, 900], [390, 650]]) for (const zoom of [1, 3]) for (const dpr of [1, 2, 3]) {
      void dpr;
      const base = fittedView(w, h);
      const view = { ...base, scale: base.scale * zoom };
      const mapped = points.map(point => screenToImage(imageToScreen(point, view), view));
      expect(strokePixels(mapped, 24)).toEqual(expected);
    }
  });
  it('erasing mask erases guides in one reversible patch', () => {
    const buffers = emptyBuffers();
    const points = [{ x: 10, y: 10 }];
    const mask = applyStroke(buffers, { layer: 'mask', erase: false, points, diameter: 8, color: [0, 0, 0, 0] });
    const guide = applyStroke(buffers, { layer: 'colorGuide', erase: false, points, diameter: 8, color: [0, 0, 255, 255] });
    expect(guide.bytes).toBeGreaterThan(0);
    const erasure = applyStroke(buffers, { layer: 'mask', erase: true, points, diameter: 8, color: [0, 0, 0, 0] });
    expect(buffers.mask[10 * 832 + 10]).toBe(0);
    expect(buffers.colorGuide[(10 * 832 + 10) * 4 + 3]).toBe(0);
    replayPatch(buffers, erasure, false);
    expect(buffers.mask[10 * 832 + 10]).toBe(255);
    expect(buffers.colorGuide[(10 * 832 + 10) * 4 + 3]).toBe(255);
    replayPatch(buffers, guide, false);
    replayPatch(buffers, mask, false);
  });
  it('caps undo history and clears redo after new edit', () => {
    const buffers = emptyBuffers();
    const history = new EditHistory(2, 1000);
    for (let x = 0; x < 3; x++) history.push(applyStroke(buffers, { layer: 'mask', erase: false, points: [{ x: x + 0.5, y: 10.5 }], diameter: 1, color: [0,0,0,0] }));
    expect(history.undo(buffers, structuredClone(layers))).toBe(true);
    expect(history.canRedo).toBe(true);
    history.push(applyStroke(buffers, { layer: 'mask', erase: false, points: [{ x: 50.5, y: 50.5 }], diameter: 1, color: [0,0,0,0] }));
    expect(history.canRedo).toBe(false);
  });
});
