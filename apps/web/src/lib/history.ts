import type { Buffers, RasterPatch } from './raster';
import { replayPatch } from './raster';

export type LayerName = 'base' | 'mask' | 'colorGuide' | 'silhouetteGuide';
export type LayerSetting = { visible: boolean; opacity: number };
export type Layers = Record<LayerName, LayerSetting>;
export type MetaPatch = { kind: 'meta'; layer: LayerName; before: LayerSetting; after: LayerSetting; bytes: number };
export type EditPatch = RasterPatch | MetaPatch;

export class EditHistory {
  private past: EditPatch[] = [];
  private future: EditPatch[] = [];
  private size = 0;
  constructor(private readonly maxOperations = 100, private readonly maxBytes = 32 * 1024 * 1024) {}
  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
  push(patch: EditPatch) {
    if (patch.bytes === 0) return;
    this.future = [];
    this.past.push(patch);
    this.size += patch.bytes;
    while (this.past.length > this.maxOperations || this.size > this.maxBytes) {
      const removed = this.past.shift();
      if (removed) this.size -= removed.bytes;
    }
  }
  undo(buffers: Buffers, layers: Layers) {
    const patch = this.past.pop();
    if (!patch) return false;
    if (patch.kind === 'raster') replayPatch(buffers, patch, false);
    else layers[patch.layer] = { ...patch.before };
    this.size -= patch.bytes;
    this.future.push(patch);
    return true;
  }
  redo(buffers: Buffers, layers: Layers) {
    const patch = this.future.pop();
    if (!patch) return false;
    if (patch.kind === 'raster') replayPatch(buffers, patch, true);
    else layers[patch.layer] = { ...patch.after };
    this.past.push(patch);
    this.size += patch.bytes;
    return true;
  }
  clear() { this.past = []; this.future = []; this.size = 0; }
}
