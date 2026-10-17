'use client';
import { useMemo } from 'react';
import { Stage, Layer, Group, Image as KonvaImage, Rect } from 'react-konva';
import type { Buffers } from '@/lib/raster';
import type { Layers } from '@/lib/history';
import type { Viewport } from '@/lib/geometry';
import { WIDTH, HEIGHT } from '@/lib/geometry';

function canvasFor(raw: Uint8Array, mask = false): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const context = canvas.getContext('2d')!;
  const image = context.createImageData(WIDTH, HEIGHT);
  if (mask) {
    for (let i = 0; i < raw.length; i++) {
      image.data[i * 4] = 255;
      image.data[i * 4 + 1] = 67;
      image.data[i * 4 + 2] = 126;
      image.data[i * 4 + 3] = raw[i];
    }
  } else image.data.set(raw);
  context.putImageData(image, 0, 0);
  return canvas;
}

export default function CanvasView({ width, height, view, image, buffers, layers }: {
  width: number; height: number; view: Viewport; image: HTMLImageElement; buffers: Buffers; layers: Layers;
}) {
  const mask = useMemo(() => canvasFor(buffers.mask, true), [buffers.mask]);
  const color = useMemo(() => canvasFor(buffers.colorGuide), [buffers.colorGuide]);
  const silhouette = useMemo(() => canvasFor(buffers.silhouetteGuide), [buffers.silhouetteGuide]);
  return <Stage width={width} height={height} listening={false}>
    <Layer listening={false}><Group x={view.x} y={view.y} scaleX={view.scale} scaleY={view.scale}>
      <Rect width={WIDTH} height={HEIGHT} fill="#ffffff" />
      {layers.base.visible && <KonvaImage image={image} width={WIDTH} height={HEIGHT} opacity={layers.base.opacity} />}
      {layers.mask.visible && <KonvaImage image={mask} width={WIDTH} height={HEIGHT} opacity={layers.mask.opacity} />}
      {layers.colorGuide.visible && <KonvaImage image={color} width={WIDTH} height={HEIGHT} opacity={layers.colorGuide.opacity} />}
      {layers.silhouetteGuide.visible && <KonvaImage image={silhouette} width={WIDTH} height={HEIGHT} opacity={layers.silhouetteGuide.opacity} />}
    </Group></Layer>
  </Stage>;
}
