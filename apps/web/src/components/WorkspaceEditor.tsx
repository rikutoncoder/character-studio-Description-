'use client';

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import dynamic from 'next/dynamic';
import { useEditor } from '@/lib/editor-store';
import { clientToImage, fittedView, fitScale, zoomAt, type Point, type Viewport, WIDTH, HEIGHT } from '@/lib/geometry';
import { api, fromWire, recoveryDelete, recoveryGet, recoveryPut, toWire, type WorkspaceWire } from '@/lib/workspace';
import type { LayerName } from '@/lib/history';

const CanvasView = dynamic(() => import('./CanvasView'), { ssr: false });
const layerLabels: Record<LayerName, string> = { base: '元画像', mask: 'マスク', colorGuide: '仮着色ガイド', silhouetteGuide: 'シルエットガイド' };
type Pointer = { x: number; y: number };
type StrokePreview = { points: Point[]; layer: 'mask' | 'colorGuide' | 'silhouetteGuide'; erase: boolean; diameter: number; color: [number, number, number, number] };

export default function WorkspaceEditor({ id }: { id: string }) {
  const state = useEditor();
  const [view, setView] = useState<Viewport>({ scale: 1, x: 0, y: 0 });
  const viewRef = useRef(view);
  const [area, setArea] = useState({ width: 1, height: 1 });
  const [baseImage, setBaseImage] = useState<HTMLImageElement | null>(null);
  const [loading, setLoading] = useState(true);
  const [gestureMode, setGestureMode] = useState(false);
  const [panMode, setPanMode] = useState(false);
  const [preview, setPreview] = useState<StrokePreview | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const pointers = useRef(new Map<number, Pointer>());
  const stroke = useRef<Point[] | null>(null);
  const previewFrame = useRef<number | null>(null);
  const pinch = useRef<{ distance: number; midpoint: Point; view: Viewport } | null>(null);
  const saving = useRef(false);
  const loaded = useRef(false);
  const fitted = useRef(false);

  useEffect(() => () => {
    if (previewFrame.current !== null) cancelAnimationFrame(previewFrame.current);
  }, []);

  const clearPreview = () => {
    if (previewFrame.current !== null) cancelAnimationFrame(previewFrame.current);
    previewFrame.current = null;
    setPreview(null);
  };
  const schedulePreview = () => {
    if (previewFrame.current !== null) return;
    previewFrame.current = requestAnimationFrame(() => {
      previewFrame.current = null;
      if (stroke.current && state.layer !== 'base') {
        setPreview({ points: stroke.current.slice(), layer: state.layer, erase: state.tool === 'eraser', diameter: state.diameter, color: state.color });
      }
    });
  };

  const updateView = (next: Viewport) => { viewRef.current = next; setView(next); };
  const loadBase = useCallback((wire: WorkspaceWire) => {
    const image = new Image();
    image.onload = () => setBaseImage(image);
    image.src = `/api/m2/assets/${wire.baseAssetId}/content`;
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const wire = await api<WorkspaceWire>(`/workspaces/${id}`);
        const backup = await recoveryGet(id).catch(() => undefined);
        if (!active) return;
        if (backup && window.confirm('未保存の編集が見つかりました。復元しますか？')) {
          try { state.load(wire, { ...fromWire(backup), basedOnRevision: backup.expectedRevision }); }
          catch { state.load(wire); }
        } else {
          if (backup) await recoveryDelete(id);
          state.load(wire);
        }
        loadBase(wire);
        loaded.current = true;
      } catch (error) { state.markError((error as Error).message); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; loaded.current = false; };
  // The workspace ID changes only on navigation. Zustand actions are stable.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, loadBase]);

  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(entries => {
      const width = entries[0].contentRect.width;
      const height = entries[0].contentRect.height;
      setArea({ width, height });
      if (!fitted.current || viewRef.current.scale < fitScale(width, height)) {
        updateView(fittedView(width, height));
        fitted.current = true;
      }
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  // Fit only on the first measurement, preserving user pan and zoom afterward.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const target = event.target as HTMLElement | null;
      if (target?.matches('input,textarea,[contenteditable="true"]')) return;
      if (event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) useEditor.getState().redo(); else useEditor.getState().undo();
      } else if (event.key.toLowerCase() === 'y') {
        event.preventDefault();
        useEditor.getState().redo();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const saveNow = useCallback(async () => {
    if (saving.current) return;
    const current = useEditor.getState();
    if (!current.wire || current.saveState === 'saved' || current.saveState === 'conflict') return;
    saving.current = true;
    const savedChange = current.change;
    const payload = toWire(current.wire, { buffers: current.buffers, layers: current.layers, featherInwardPx: current.feather, basedOnRevision: current.wire.revision });
    try {
      await recoveryPut(id, payload);
      current.markSaving();
      const wire = await api<WorkspaceWire>(`/workspaces/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
      useEditor.getState().markSaved(wire, savedChange);
      if (useEditor.getState().change === savedChange) await recoveryDelete(id);
      else {
        const latest = useEditor.getState();
        await recoveryPut(id, toWire(wire, { buffers: latest.buffers, layers: latest.layers, featherInwardPx: latest.feather, basedOnRevision: wire.revision }));
        window.setTimeout(() => saveNow(), 750);
      }
    } catch (error) {
      const failure = error as Error & { status?: number };
      if (failure.status === 409) useEditor.getState().markConflict();
      else useEditor.getState().markError(failure.message);
    } finally { saving.current = false; }
  }, [id]);

  useEffect(() => {
    if (!loaded.current || state.saveState !== 'unsaved') return;
    const wire = state.wire;
    if (wire) {
      const payload = toWire(wire, { buffers: state.buffers, layers: state.layers, featherInwardPx: state.feather, basedOnRevision: wire.revision });
      recoveryPut(id, payload).catch(() => state.markError('端末内の復旧保存に失敗しました'));
    }
    const timer = window.setTimeout(() => saveNow(), 750);
    return () => window.clearTimeout(timer);
  // A committed editing operation starts a new autosave debounce.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.change, state.saveState, id, saveNow]);

  const local = (event: ReactPointerEvent): Point => {
    const rect = container.current!.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const imagePoint = (event: ReactPointerEvent): Point => clientToImage({ x: event.clientX, y: event.clientY }, container.current!.getBoundingClientRect(), viewRef.current);
  const pair = (): [Pointer, Pointer] | null => {
    const values = [...pointers.current.values()];
    return values.length >= 2 ? [values[0], values[1]] : null;
  };
  const measure = ([a, b]: [Pointer, Pointer]) => ({ distance: Math.hypot(b.x - a.x, b.y - a.y), midpoint: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } });
  const onPointerDown = (event: ReactPointerEvent) => {
    event.preventDefault();
    try { event.currentTarget.setPointerCapture(event.pointerId); }
    catch { /* Synthetic events and older WebKit may not capture; state still tracks the pointer. */ }
    pointers.current.set(event.pointerId, local(event));
    if (pointers.current.size >= 2) {
      stroke.current = null; // A second finger cancels, never commits, the first stroke.
      clearPreview();
      setGestureMode(true);
      const two = pair();
      if (two) pinch.current = { ...measure(two), view: viewRef.current };
      return;
    }
    if (!gestureMode && !panMode && state.layer !== 'base') {
      stroke.current = [imagePoint(event)];
      schedulePreview();
    }
  };
  const onPointerMove = (event: ReactPointerEvent) => {
    if (!pointers.current.has(event.pointerId)) return;
    const previous = pointers.current.get(event.pointerId)!;
    const next = local(event);
    pointers.current.set(event.pointerId, next);
    const two = pair();
    if (two && pinch.current) {
      const now = measure(two);
      const origin = pinch.current;
      const candidate = zoomAt(origin.view, origin.midpoint, now.distance / Math.max(1, origin.distance), fitScale(area.width, area.height));
      updateView({ ...candidate, x: candidate.x + now.midpoint.x - origin.midpoint.x, y: candidate.y + now.midpoint.y - origin.midpoint.y });
    } else if (panMode && pointers.current.size === 1) updateView({ ...viewRef.current, x: viewRef.current.x + next.x - previous.x, y: viewRef.current.y + next.y - previous.y });
    else if (stroke.current && pointers.current.size === 1 && !gestureMode) {
      stroke.current.push(imagePoint(event));
      schedulePreview();
    }
  };
  const finishPointer = (event: ReactPointerEvent, cancelled: boolean) => {
    if (!pointers.current.has(event.pointerId)) return;
    const shouldCommit = !cancelled && pointers.current.size === 1 && !gestureMode && stroke.current;
    if (shouldCommit) {
      stroke.current!.push(imagePoint(event));
      state.stroke({ layer: state.layer as 'mask' | 'colorGuide' | 'silhouetteGuide', erase: state.tool === 'eraser', points: stroke.current!, diameter: state.diameter, color: state.color });
    }
    stroke.current = null;
    clearPreview();
    pointers.current.delete(event.pointerId);
    pinch.current = null;
    if (pointers.current.size === 0) setGestureMode(false);
  };

  const reload = async () => {
    if (!window.confirm('未保存の内容を破棄し、共有Workspaceの最新版を読み直しますか？')) return;
    const wire = await api<WorkspaceWire>(`/workspaces/${id}`);
    await recoveryDelete(id);
    state.load(wire);
    loadBase(wire);
  };
  const fork = async () => {
    const current = useEditor.getState();
    if (!current.wire) return;
    try {
      const fresh = await api<WorkspaceWire>(`/workspaces/${id}/fork`, { method: 'POST', body: '{}' });
      const payload = toWire(fresh, { buffers: current.buffers, layers: current.layers, featherInwardPx: current.feather, basedOnRevision: fresh.revision });
      await api<WorkspaceWire>(`/workspaces/${fresh.id}`, { method: 'PUT', body: JSON.stringify(payload) });
      await recoveryDelete(id);
      location.assign(`/workspace/${fresh.id}`);
    } catch (error) { state.markError((error as Error).message); }
  };

  return <main className="flex h-dvh min-h-[600px] flex-col">
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-700 px-3 py-2">
      <div><a href="/" className="text-cyan-300">← プロジェクト一覧</a><h1 className="font-semibold">画像編集Workspace</h1></div>
      <div className="flex items-center gap-3"><span role="status" className={state.saveState === 'conflict' || state.saveState === 'error' ? 'text-amber-300' : 'text-slate-300'}>保存: {{ saved: '完了', unsaved: '未保存', saving: '保存中', conflict: '競合', error: 'エラー' }[state.saveState]}</span><button className="rounded bg-cyan-700 px-4 py-2" onClick={saveNow}>保存</button>{state.saveState === 'saved' && <a className="rounded bg-teal-700 px-4 py-2" href={`/generation/${id}`}>生成へ</a>}</div>
    </header>
    {state.error && <p role="alert" className="bg-red-900 p-2">{state.error}</p>}
    {state.saveState === 'conflict' && <div role="alert" className="flex flex-wrap items-center gap-3 bg-amber-900 p-2">別の端末で先に保存されました。未保存の編集は保持しています。<button className="underline" onClick={reload}>最新版を読み直す</button><button className="underline" onClick={fork}>別Workspaceとして保存</button></div>}
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <aside className="order-2 max-h-[42dvh] w-full overflow-y-auto border-t border-slate-700 bg-slate-900 p-3 md:order-1 md:max-h-none md:w-72 md:border-r md:border-t-0">
        <div className="flex flex-wrap gap-2"><button className={buttonStyle(!panMode)} onClick={() => setPanMode(false)}>描く</button><button className={buttonStyle(panMode)} onClick={() => setPanMode(true)}>移動</button><button className={buttonStyle(state.tool === 'eraser')} onClick={() => { state.setTool(state.tool === 'brush' ? 'eraser' : 'brush'); setPanMode(false); }}>消しゴム</button></div>
        <div className="mt-4 flex gap-2"><button className="rounded bg-slate-700 px-3 py-2 disabled:opacity-40" disabled={!state.history.canUndo} onClick={state.undo}>Undo</button><button className="rounded bg-slate-700 px-3 py-2 disabled:opacity-40" disabled={!state.history.canRedo} onClick={state.redo}>Redo</button></div>
        <label className="mt-5 block text-sm">ブラシ直径: {state.diameter}画像px<input aria-label="ブラシ直径" className="w-full" type="range" min="1" max="256" value={state.diameter} onChange={event => state.setDiameter(Number(event.target.value))} /></label>
        <div className="mt-2 flex gap-2">{[8, 24, 64].map(value => <button key={value} className="rounded bg-slate-700 px-3 py-1" onClick={() => state.setDiameter(value)}>{value}px</button>)}</div>
        <label className="mt-4 block text-sm">ガイド色 <input aria-label="ガイド色" type="color" value={`#${state.color.slice(0,3).map(v => v.toString(16).padStart(2,'0')).join('')}`} onChange={event => { const value = event.target.value; state.setColor([parseInt(value.slice(1,3),16),parseInt(value.slice(3,5),16),parseInt(value.slice(5,7),16),255]); }} /></label>
        <label className="mt-4 block text-sm">フェザー: {state.feather}px（マスク内側のみ）<input aria-label="フェザー" className="w-full" type="range" min="0" max="64" value={state.feather} onChange={event => state.setFeather(Number(event.target.value))} /></label>
        <h2 className="mt-5 font-semibold">レイヤー</h2>
        <p data-testid="active-layer" aria-live="polite" className="mt-1 rounded bg-slate-800 px-3 py-2 text-sm">選択中のレイヤー: <strong className="text-cyan-200">{layerLabels[state.layer]}</strong></p>
        {(Object.keys(layerLabels) as LayerName[]).map(layer => <div key={layer} className="mt-2 rounded bg-slate-800 p-2">
          <div className="flex items-center justify-between gap-2"><button type="button" aria-label={layerLabels[layer]} aria-pressed={state.layer === layer} className={`min-h-11 min-w-0 flex-1 rounded px-3 py-2 text-left ${state.layer === layer ? 'bg-cyan-700 font-semibold text-white' : 'bg-slate-700'}`} onClick={() => { state.setLayer(layer); setPanMode(layer === 'base'); }}>{layerLabels[layer]}{state.layer === layer && <span aria-hidden="true" className="ml-2">✓ 選択中</span>}</button><label className="text-xs"><input type="checkbox" checked={state.layers[layer].visible} onChange={event => state.setLayerSetting(layer, { visible: event.target.checked })} /> 表示</label></div>
          <label className="block text-xs">不透明度 {Math.round(state.layers[layer].opacity * 100)}%<input aria-label={`${layerLabels[layer]}の不透明度`} type="range" min="0" max="100" value={state.layers[layer].opacity * 100} onChange={event => state.setLayerSetting(layer, { opacity: Number(event.target.value) / 100 })} className="w-full" /></label>
        </div>)}
        <p className="mt-5 text-xs text-slate-400">一本指・Pencil・マウスで描画。二本指で移動と拡大縮小。ズームは画像座標に影響しません。</p>
      </aside>
      <div ref={container} data-testid="workspace-canvas" data-view-scale={view.scale} data-view-x={view.x} data-view-y={view.y} data-preview-points={preview?.points.length ?? 0} className="relative order-1 min-h-0 flex-1 overflow-hidden bg-slate-950 touch-none md:order-2" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={event => finishPointer(event, false)} onPointerCancel={event => finishPointer(event, true)} onWheel={event => { event.preventDefault(); updateView(zoomAt(viewRef.current, local(event as unknown as ReactPointerEvent), Math.exp(-event.deltaY * 0.001), fitScale(area.width, area.height))); }}>
        {!loading && baseImage && <CanvasView width={area.width} height={area.height} view={view} image={baseImage} buffers={state.buffers} layers={state.layers} preview={preview} />}
        {loading && <p className="absolute left-4 top-4">読み込み中…</p>}
        <span className="pointer-events-none absolute bottom-2 right-3 rounded bg-slate-900/80 px-2 text-xs">{Math.round(view.scale * 100)}% · 832×1216</span>
      </div>
    </div>
  </main>;
}

function buttonStyle(active: boolean) { return `rounded px-3 py-2 ${active ? 'bg-cyan-700' : 'bg-slate-700'}`; }
