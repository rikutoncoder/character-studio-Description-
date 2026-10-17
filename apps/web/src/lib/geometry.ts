export const WIDTH = 832;
export const HEIGHT = 1216;
export type Point = { x: number; y: number };
export type Viewport = { scale: number; x: number; y: number };

export function fitScale(width: number, height: number): number {
  return Math.min(width / WIDTH, height / HEIGHT);
}
export function imageToScreen(point: Point, view: Viewport): Point {
  return { x: view.x + point.x * view.scale, y: view.y + point.y * view.scale };
}
export function screenToImage(point: Point, view: Viewport): Point {
  return { x: (point.x - view.x) / view.scale, y: (point.y - view.y) / view.scale };
}
export function fittedView(width: number, height: number): Viewport {
  const scale = fitScale(width, height);
  return { scale, x: (width - WIDTH * scale) / 2, y: (height - HEIGHT * scale) / 2 };
}
export function zoomAt(view: Viewport, anchor: Point, factor: number, minScale: number): Viewport {
  const scale = Math.max(minScale, Math.min(16, view.scale * factor));
  const image = screenToImage(anchor, view);
  return { scale, x: anchor.x - image.x * scale, y: anchor.y - image.y * scale };
}

// Conversion deliberately uses CSS pixels, not device pixels. DPR affects display only.
export function clientToImage(client: Point, rect: Pick<DOMRect, 'left' | 'top'>, view: Viewport): Point {
  return screenToImage({ x: client.x - rect.left, y: client.y - rect.top }, view);
}
