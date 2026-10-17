import { describe, expect, it } from 'vitest';
import { clientToImage, fittedView, imageToScreen, screenToImage, zoomAt } from './geometry';

describe('image coordinates', () => {
  it('round trips at different viewport sizes and zooms', () => {
    for (const [width, height] of [[1440, 900], [390, 650], [1024, 768]]) {
      const fit = fittedView(width, height);
      for (const factor of [1, 2, 16]) {
        const view = zoomAt(fit, { x: width / 2, y: height / 2 }, factor, fit.scale);
        const point = { x: 217.25, y: 505.75 };
        const screen = imageToScreen(point, view);
        const image = screenToImage(screen, view);
        expect(image.x).toBeCloseTo(point.x, 10);
        expect(image.y).toBeCloseTo(point.y, 10);
        for (const dpr of [1, 2, 3]) {
          // A browser's DPR does not enter this conversion.
          void dpr;
          expect(clientToImage({ x: screen.x + 40, y: screen.y + 80 }, { left: 40, top: 80 }, view)).toEqual(image);
        }
      }
    }
  });
  it('clamps zoom between fit and 1600 percent', () => {
    const fit = fittedView(390, 600);
    expect(zoomAt(fit, { x: 10, y: 10 }, 0.01, fit.scale).scale).toBe(fit.scale);
    expect(zoomAt(fit, { x: 10, y: 10 }, 1000, fit.scale).scale).toBe(16);
  });
});
