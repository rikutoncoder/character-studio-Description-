import { create } from 'zustand';
import { EditHistory, type LayerName, type Layers } from './history';
import { applyStroke, emptyBuffers, type Buffers, type Stroke } from './raster';
import type { WorkspaceDraft, WorkspaceWire } from './workspace';
import { fromWire } from './workspace';

type SaveState = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'error';
type EditorState = {
  wire: WorkspaceWire | null; buffers: Buffers; layers: Layers; feather: number;
  layer: LayerName; tool: 'brush' | 'eraser'; diameter: number; color: [number, number, number, number];
  history: EditHistory; change: number; saveState: SaveState; error: string | null;
  load: (wire: WorkspaceWire, draft?: WorkspaceDraft) => void;
  stroke: (stroke: Stroke) => void;
  setLayer: (layer: LayerName) => void;
  setTool: (tool: 'brush' | 'eraser') => void;
  setDiameter: (diameter: number) => void;
  setColor: (color: [number, number, number, number]) => void;
  setLayerSetting: (layer: LayerName, change: Partial<Layers[LayerName]>) => void;
  setFeather: (value: number) => void;
  undo: () => void; redo: () => void;
  markSaving: () => void; markSaved: (wire: WorkspaceWire, savedChange: number) => void;
  markConflict: () => void; markError: (message: string) => void;
};

const defaults: Layers = {
  base: { visible: true, opacity: 1 }, mask: { visible: true, opacity: 0.5 },
  colorGuide: { visible: true, opacity: 0.8 }, silhouetteGuide: { visible: true, opacity: 0.8 },
};

export const useEditor = create<EditorState>((set, get) => ({
  wire: null, buffers: emptyBuffers(), layers: structuredClone(defaults), feather: 3,
  layer: 'mask', tool: 'brush', diameter: 24, color: [20, 110, 255, 255],
  history: new EditHistory(), change: 0, saveState: 'saved', error: null,
  load: (wire, draft) => {
    const source = draft ?? fromWire(wire);
    set({ wire: { ...wire, revision: source.basedOnRevision }, buffers: source.buffers,
      layers: source.layers, feather: source.featherInwardPx, history: new EditHistory(),
      change: 0, saveState: draft ? 'unsaved' : 'saved', error: null });
  },
  stroke: stroke => {
    const state = get();
    const buffers = { mask: state.buffers.mask.slice(), colorGuide: state.buffers.colorGuide.slice(), silhouetteGuide: state.buffers.silhouetteGuide.slice() };
    const patch = applyStroke(buffers, stroke);
    if (!patch.bytes) return;
    state.history.push(patch);
    set({ buffers, change: state.change + 1, saveState: 'unsaved' });
  },
  setLayer: layer => set({ layer }),
  setTool: tool => set({ tool }),
  setDiameter: diameter => set({ diameter: Math.max(1, Math.min(256, Math.round(diameter))) }),
  setColor: color => set({ color }),
  setLayerSetting: (layer, change) => {
    const state = get();
    const before = state.layers[layer];
    const after = { ...before, ...change };
    if (before.visible === after.visible && before.opacity === after.opacity) return;
    state.history.push({ kind: 'meta', layer, before, after, bytes: 32 });
    set({ layers: { ...state.layers, [layer]: after }, change: state.change + 1, saveState: 'unsaved' });
  },
  setFeather: value => set(state => ({ feather: Math.max(0, Math.min(64, value)), change: state.change + 1, saveState: 'unsaved' })),
  undo: () => {
    const state = get();
    const buffers = { mask: state.buffers.mask.slice(), colorGuide: state.buffers.colorGuide.slice(), silhouetteGuide: state.buffers.silhouetteGuide.slice() };
    const layers = structuredClone(state.layers);
    if (state.history.undo(buffers, layers)) set({ buffers, layers, change: state.change + 1, saveState: 'unsaved' });
  },
  redo: () => {
    const state = get();
    const buffers = { mask: state.buffers.mask.slice(), colorGuide: state.buffers.colorGuide.slice(), silhouetteGuide: state.buffers.silhouetteGuide.slice() };
    const layers = structuredClone(state.layers);
    if (state.history.redo(buffers, layers)) set({ buffers, layers, change: state.change + 1, saveState: 'unsaved' });
  },
  markSaving: () => set({ saveState: 'saving' }),
  markSaved: (wire, savedChange) => set(state => ({ wire, saveState: state.change === savedChange ? 'saved' : 'unsaved', error: null })),
  markConflict: () => set({ saveState: 'conflict' }),
  markError: error => set({ saveState: 'error', error }),
}));
