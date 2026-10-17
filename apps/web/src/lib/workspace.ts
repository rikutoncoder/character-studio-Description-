import { HEIGHT, WIDTH } from './geometry';
import type { Buffers } from './raster';
import { assertBuffers } from './raster';
import type { Layers } from './history';

export type WorkspaceWire = {
  schemaVersion: 1; id: string; projectId: string; baseVersionId: string; baseAssetId: string;
  revision: number; width: number; height: number; mask: string; colorGuide: string;
  silhouetteGuide: string; featherInwardPx: number; layers: Layers;
};
export type WorkspaceDraft = { buffers: Buffers; layers: Layers; featherInwardPx: number; basedOnRevision: number };

export function encode(bytes: Uint8Array): string {
  const pieces: string[] = [];
  for (let i = 0; i < bytes.length; i += 0x8000) pieces.push(String.fromCharCode(...bytes.subarray(i, i + 0x8000)));
  return btoa(pieces.join(''));
}
export function decode(value: string, length: number): Uint8Array {
  const raw = atob(value);
  if (raw.length !== length) throw new Error('Workspace size mismatch');
  return Uint8Array.from(raw, char => char.charCodeAt(0));
}
export function fromWire(wire: WorkspaceWire): WorkspaceDraft {
  if (wire.schemaVersion !== 1 || wire.width !== WIDTH || wire.height !== HEIGHT) throw new Error('Unsupported Workspace');
  const buffers = { mask: decode(wire.mask, WIDTH * HEIGHT), colorGuide: decode(wire.colorGuide, WIDTH * HEIGHT * 4), silhouetteGuide: decode(wire.silhouetteGuide, WIDTH * HEIGHT * 4) };
  assertBuffers(buffers);
  return { buffers, layers: structuredClone(wire.layers), featherInwardPx: wire.featherInwardPx, basedOnRevision: wire.revision };
}
export function toWire(wire: WorkspaceWire, draft: WorkspaceDraft): WorkspaceWire & { expectedRevision: number } {
  assertBuffers(draft.buffers);
  return { ...wire, mask: encode(draft.buffers.mask), colorGuide: encode(draft.buffers.colorGuide), silhouetteGuide: encode(draft.buffers.silhouetteGuide), layers: draft.layers, featherInwardPx: draft.featherInwardPx, expectedRevision: draft.basedOnRevision };
}
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/m2${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers }, cache: 'no-store' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const failure = new Error(error.error?.message ?? `HTTP ${response.status}`) as Error & { status: number; code?: string };
    failure.status = response.status;
    failure.code = error.error?.code;
    throw failure;
  }
  return response.json();
}

const DB_NAME = 'character-studio-m2-recovery';
function openRecovery(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('drafts');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export async function recoveryGet(id: string): Promise<(WorkspaceWire & { expectedRevision: number }) | undefined> {
  const db = await openRecovery();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('drafts');
    const request = transaction.objectStore('drafts').get(id);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => db.close();
  });
}
export async function recoveryPut(id: string, draft: WorkspaceWire & { expectedRevision: number }): Promise<void> {
  const db = await openRecovery();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('drafts', 'readwrite');
    transaction.objectStore('drafts').put(draft, id);
    transaction.oncomplete = () => { db.close(); resolve(); };
    transaction.onerror = () => reject(transaction.error);
  });
}
export async function recoveryDelete(id: string): Promise<void> {
  const db = await openRecovery();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('drafts', 'readwrite');
    transaction.objectStore('drafts').delete(id);
    transaction.oncomplete = () => { db.close(); resolve(); };
    transaction.onerror = () => reject(transaction.error);
  });
}
