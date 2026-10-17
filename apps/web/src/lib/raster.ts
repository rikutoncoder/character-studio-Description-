import { HEIGHT, WIDTH, type Point } from './geometry';

export type RasterLayer = 'mask' | 'colorGuide' | 'silhouetteGuide';
export type Buffers = { mask: Uint8Array; colorGuide: Uint8Array; silhouetteGuide: Uint8Array };
export type BufferName = keyof Buffers;
export type PackedChanges = { indices: Uint32Array; before: Uint8Array; after: Uint8Array; stride: number };
export type RasterPatch = { kind: 'raster'; layers: Partial<Record<BufferName, PackedChanges>>; bytes: number };
export type Stroke = { layer: RasterLayer; erase: boolean; points: Point[]; diameter: number; color: [number, number, number, number] };

export function emptyBuffers(): Buffers {
  return { mask: new Uint8Array(WIDTH * HEIGHT), colorGuide: new Uint8Array(WIDTH * HEIGHT * 4), silhouetteGuide: new Uint8Array(WIDTH * HEIGHT * 4) };
}

function distanceSquared(point: Point, start: Point, end: Point): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = dx * dx + dy * dy;
  const t = length === 0 ? 0 : Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length));
  const ex = point.x - (start.x + t * dx);
  const ey = point.y - (start.y + t * dy);
  return ex * ex + ey * ey;
}

export function strokePixels(points: Point[], diameter: number): number[] {
  if (!points.length || diameter < 1 || diameter > 256) return [];
  const pixels = new Set<number>();
  const radius = diameter / 2;
  for (let segment = 0; segment < points.length; segment++) {
    const a = points[Math.max(0, segment - 1)];
    const b = points[segment];
    const left = Math.max(0, Math.floor(Math.min(a.x, b.x) - radius - 0.5));
    const right = Math.min(WIDTH - 1, Math.ceil(Math.max(a.x, b.x) + radius - 0.5));
    const top = Math.max(0, Math.floor(Math.min(a.y, b.y) - radius - 0.5));
    const bottom = Math.min(HEIGHT - 1, Math.ceil(Math.max(a.y, b.y) + radius - 0.5));
    for (let y = top; y <= bottom; y++) for (let x = left; x <= right; x++) {
      if (distanceSquared({ x: x + 0.5, y: y + 0.5 }, a, b) <= radius * radius) pixels.add(y * WIDTH + x);
    }
    if (diameter === 1) {
      // A one-pixel brush must still paint when the pointer lies on a pixel edge.
      const steps = Math.max(1, Math.ceil(Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y)) * 2));
      for (let step = 0; step <= steps; step++) {
        const t = step / steps;
        const x = Math.floor(a.x + (b.x - a.x) * t);
        const y = Math.floor(a.y + (b.y - a.y) * t);
        if (x >= 0 && x < WIDTH && y >= 0 && y < HEIGHT) pixels.add(y * WIDTH + x);
      }
    }
  }
  return [...pixels].sort((a, b) => a - b);
}

export function applyStroke(buffers: Buffers, stroke: Stroke): RasterPatch {
  const pixels = strokePixels(stroke.points, stroke.diameter);
  const temporary: Partial<Record<BufferName, PackedChanges & { count: number }>> = {};
  const record = (name: BufferName, pixel: number, value: number[]) => {
    const buffer = buffers[name];
    const stride = value.length;
    const offset = pixel * stride;
    if (value.every((channel, i) => buffer[offset + i] === channel)) return;
    let change = temporary[name];
    if (!change) {
      change = { indices: new Uint32Array(pixels.length), before: new Uint8Array(pixels.length * stride), after: new Uint8Array(pixels.length * stride), stride, count: 0 };
      temporary[name] = change;
    }
    const index = change.count++;
    change.indices[index] = pixel;
    change.before.set(buffer.subarray(offset, offset + stride), index * stride);
    change.after.set(value, index * stride);
    buffer.set(value, offset);
  };
  for (const pixel of pixels) {
    if (stroke.layer === 'mask') {
      const maskValue = stroke.erase ? 0 : 255;
      record('mask', pixel, [maskValue]);
      if (stroke.erase) {
        record('colorGuide', pixel, [0, 0, 0, 0]);
        record('silhouetteGuide', pixel, [0, 0, 0, 0]);
      }
    } else if (buffers.mask[pixel] === 255) {
      record(stroke.layer, pixel, stroke.erase ? [0, 0, 0, 0] : stroke.color);
    }
  }
  const layers: RasterPatch['layers'] = {};
  for (const name of Object.keys(temporary) as BufferName[]) {
    const change = temporary[name]!;
    layers[name] = { indices: change.indices.slice(0, change.count), before: change.before.slice(0, change.count * change.stride), after: change.after.slice(0, change.count * change.stride), stride: change.stride };
  }
  const bytes = Object.values(layers).reduce((total, changes) => total + changes.indices.byteLength + changes.before.byteLength + changes.after.byteLength, 0);
  return { kind: 'raster', layers, bytes };
}

export function replayPatch(buffers: Buffers, patch: RasterPatch, redo: boolean): void {
  for (const [name, changes] of Object.entries(patch.layers) as [BufferName, PackedChanges][]) {
    const source = redo ? changes.after : changes.before;
    for (let i = 0; i < changes.indices.length; i++) buffers[name].set(source.subarray(i * changes.stride, (i + 1) * changes.stride), changes.indices[i] * changes.stride);
  }
}

export function assertBuffers(buffers: Buffers): void {
  if (buffers.mask.length !== WIDTH * HEIGHT || buffers.colorGuide.length !== WIDTH * HEIGHT * 4 || buffers.silhouetteGuide.length !== WIDTH * HEIGHT * 4) throw new Error('Invalid buffer size');
  for (let i = 0; i < buffers.mask.length; i++) {
    if (buffers.mask[i] !== 0 && buffers.mask[i] !== 255) throw new Error('Mask is not binary');
    if (buffers.mask[i] === 0 && (buffers.colorGuide[i * 4 + 3] !== 0 || buffers.silhouetteGuide[i * 4 + 3] !== 0)) throw new Error('Guide escapes mask');
  }
}
