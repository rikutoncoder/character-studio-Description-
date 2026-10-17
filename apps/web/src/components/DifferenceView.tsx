'use client';

import { useEffect, useRef, useState } from 'react';

const WIDTH = 832;
const HEIGHT = 1216;

async function loadImage(url: string): Promise<HTMLImageElement> {
  const image = new Image();
  image.src = url;
  await image.decode();
  if (image.naturalWidth !== WIDTH || image.naturalHeight !== HEIGHT) {
    throw new Error('比較画像のサイズが832×1216ではありません');
  }
  return image;
}

function pixels(image: HTMLImageElement): Uint8ClampedArray {
  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('比較用Canvasを開けません');
  context.drawImage(image, 0, 0);
  return context.getImageData(0, 0, WIDTH, HEIGHT).data;
}

export default function DifferenceView({ sourceId, strictId, maskId }: {
  sourceId: string; strictId: string; maskId: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    (async () => {
      const [source, strict, mask] = await Promise.all(
        [sourceId, strictId, maskId].map(id => loadImage(`/api/m2/assets/${id}/content`)),
      );
      if (!active || !canvas.current) return;
      const a = pixels(source), b = pixels(strict), m = pixels(mask);
      const output = new Uint8ClampedArray(WIDTH * HEIGHT * 4);
      for (let y = 0; y < HEIGHT; y++) for (let x = 0; x < WIDTH; x++) {
        const i = (y * WIDTH + x) * 4;
        const edited = m[i] === 255;
        const border = edited && (
          x === 0 || y === 0 || x === WIDTH - 1 || y === HEIGHT - 1 ||
          m[i - 4] !== 255 || m[i + 4] !== 255 ||
          m[i - WIDTH * 4] !== 255 || m[i + WIDTH * 4] !== 255
        );
        if (border) {
          output[i] = 255; output[i + 1] = 220; output[i + 2] = 0; output[i + 3] = 255;
        } else if (a[i] !== b[i] || a[i + 1] !== b[i + 1] || a[i + 2] !== b[i + 2] || a[i + 3] !== b[i + 3]) {
          output[i] = 255; output[i + 1] = 40; output[i + 2] = 120; output[i + 3] = 200;
        }
      }
      const context = canvas.current.getContext('2d');
      if (!context) return;
      context.putImageData(new ImageData(output, WIDTH, HEIGHT), 0, 0);
    })().catch(failure => { if (active) setError((failure as Error).message); });
    return () => { active = false; };
  }, [sourceId, strictId, maskId]);
  return <figure>
    <div className="relative">
      <img src={`/api/m2/assets/${sourceId}/content`} alt="元画像に差分を重ねた背景" className="w-full" />
      <canvas ref={canvas} width={WIDTH} height={HEIGHT} aria-label="変更画素とマスク境界" className="absolute inset-0 h-full w-full" />
    </div>
    <figcaption className="mt-1 text-sm">差分：赤＝変更画素、黄＝マスク境界</figcaption>
    {error && <p role="alert" className="text-amber-300">{error}</p>}
  </figure>;
}
