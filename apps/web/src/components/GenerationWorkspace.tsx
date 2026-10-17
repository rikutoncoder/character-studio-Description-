'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type WorkspaceWire } from '@/lib/workspace';
import DifferenceView from './DifferenceView';

type Job = {
  id: string; state: string; attempt: { number: number; seed: number; error_code: string | null } | null;
  candidateId: string | null; queuePosition: number;
};
type Candidate = {
  id: string; state: string; sourceVersionId: string; sourceAssetId: string;
  rawAssetId: string; strictAssetId: string; maskAssetId: string;
  outsideChangedPixels: number; alphaChangedPixels: number; adoptedVersionId: string | null;
};

async function m3<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, cache: 'no-store', headers: { 'Content-Type': 'application/json', ...init?.headers } });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message ?? value.error?.code ?? `HTTP ${response.status}`);
  return value as T;
}

const asset = (id: string) => `/api/m2/assets/${id}/content`;

export default function GenerationWorkspace({ workspaceId }: { workspaceId: string }) {
  const [workspace, setWorkspace] = useState<WorkspaceWire | null>(null);
  const [operation, setOperation] = useState<'COLOR_CHANGE' | 'STRUCTURE_ADD'>('COLOR_CHANGE');
  const [prompt, setPrompt] = useState('');
  const [negative, setNegative] = useState('');
  const [denoise, setDenoise] = useState(0.3);
  const [steps, setSteps] = useState(25);
  const [cfg, setCfg] = useState(7);
  const [seed, setSeed] = useState(42);
  const [job, setJob] = useState<Job | null>(null);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [compare, setCompare] = useState<'source' | 'raw' | 'strict'>('strict');

  useEffect(() => {
    api<WorkspaceWire>(`/workspaces/${workspaceId}`).then(setWorkspace).catch(error => setError(error.message));
  }, [workspaceId]);

  const refreshJob = useCallback(async (id: string) => {
    const next = await m3<Job>(`/api/generation-jobs/${id}`);
    setJob(next);
    if (next.candidateId) {
      setCandidate(await m3<Candidate>(`/api/candidates/${next.candidateId}`));
    }
  }, []);

  useEffect(() => {
    const resumed = new URLSearchParams(window.location.search).get('job');
    if (resumed && /^[a-f0-9-]{36}$/.test(resumed)) {
      refreshJob(resumed).catch(error => setError(error.message));
    }
  }, [refreshJob]);

  useEffect(() => {
    if (!job || ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'WORKER_LOST'].includes(job.state)) return;
    const events = new EventSource(`/api/generation-jobs/${job.id}/events`);
    const update = () => refreshJob(job.id).catch(error => setError(error.message));
    for (const name of ['job.status.changed', 'candidate.ready', 'job.attempt.failed']) {
      events.addEventListener(name, update);
    }
    const timer = setInterval(update, 3000);
    return () => { events.close(); clearInterval(timer); };
  }, [job?.id, job?.state, refreshJob]);

  async function start() {
    if (!workspace) return;
    setBusy(true); setError('');
    try {
      const key = crypto.randomUUID();
      const created = await m3<Job>('/api/generation-jobs', {
        method: 'POST', headers: { 'Idempotency-Key': key },
        body: JSON.stringify({
          workspaceId, expectedRevision: workspace.revision, operationType: operation,
          prompt, negativePrompt: negative, preset: { denoise, steps, cfg, seed },
        }),
      });
      setJob(created);
      setCandidate(created.candidateId ? await m3<Candidate>(`/api/candidates/${created.candidateId}`) : null);
      window.history.replaceState(null, '', `/generation/${workspaceId}?job=${created.id}`);
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  async function action(kind: 'cancel' | 'retry', newSeed?: number) {
    if (!job) return;
    setBusy(true); setError('');
    try {
      const next = await m3<Job>(`/api/generation-jobs/${job.id}/${kind}`, {
        method: 'POST', body: JSON.stringify(newSeed === undefined ? {} : { newSeed }),
      });
      setJob(next); setCandidate(null);
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  async function adopt() {
    if (!candidate || !workspace) return;
    if (!window.confirm('厳密保持結果を新しい確定版として採用しますか？元画像は残ります。')) return;
    setBusy(true); setError('');
    try {
      const adopted = await m3<Candidate>(`/api/candidates/${candidate.id}/adopt`, {
        method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ expectedHeadVersionId: workspace.baseVersionId }),
      });
      setCandidate(adopted);
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  async function reject() {
    if (!candidate || !window.confirm('この候補を不採用にしますか？画像の記録は残ります。')) return;
    setBusy(true); setError('');
    try {
      setCandidate(await m3<Candidate>(`/api/candidates/${candidate.id}/reject`, { method: 'POST', body: '{}' }));
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  const range = operation === 'COLOR_CHANGE' ? '0.25〜0.35' : '0.50〜0.65';
  return <main className="mx-auto max-w-6xl p-4 md:p-8">
    <a href={`/workspace/${workspaceId}`} className="text-cyan-300 underline">← 画像編集Workspaceに戻る</a>
    <h1 className="mt-4 text-2xl font-semibold">生成・比較・採用</h1>
    <p className="mt-1 text-sm text-slate-300">生成開始時の保存済みWorkspaceを固定します。AIの生出力はそのまま採用できません。</p>
    {error && <p role="alert" className="mt-4 rounded bg-red-900 p-3">{error}</p>}
    <section className="mt-6 grid gap-4 rounded border border-slate-700 p-4 md:grid-cols-2">
      <div className="space-y-3">
        <label className="block">編集内容<select aria-label="編集内容" className="mt-1 w-full rounded bg-slate-800 p-2" value={operation} onChange={event => {
          const next = event.target.value as typeof operation;
          setOperation(next); setDenoise(next === 'COLOR_CHANGE' ? 0.3 : 0.55);
        }}><option value="COLOR_CHANGE">色を変える</option><option value="STRUCTURE_ADD">衣装の形を追加する</option></select></label>
        <label className="block">作りたいもの<textarea aria-label="作りたいもの" className="mt-1 min-h-24 w-full rounded bg-slate-800 p-2" value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="例: blue jacket, anime style" /></label>
        <label className="block">避けたいもの<textarea aria-label="避けたいもの" className="mt-1 w-full rounded bg-slate-800 p-2" value={negative} onChange={event => setNegative(event.target.value)} /></label>
      </div>
      <div className="space-y-3">
        <label className="block">変化の強さ（denoise）: {denoise.toFixed(2)}<input aria-label="変化の強さ" className="w-full" type="range" min={operation === 'COLOR_CHANGE' ? 0.25 : 0.5} max={operation === 'COLOR_CHANGE' ? 0.35 : 0.65} step="0.01" value={denoise} onChange={event => setDenoise(Number(event.target.value))} /></label>
        <p className="text-xs text-slate-400">この操作の検証済み範囲: {range}</p>
        <label className="block">ステップ数<input aria-label="ステップ数" className="mt-1 w-full rounded bg-slate-800 p-2" type="number" min="1" max="100" value={steps} onChange={event => setSteps(Number(event.target.value))} /></label>
        <label className="block">CFG<input aria-label="CFG" className="mt-1 w-full rounded bg-slate-800 p-2" type="number" min="1" max="20" step="0.5" value={cfg} onChange={event => setCfg(Number(event.target.value))} /></label>
        <label className="block">Seed<input aria-label="Seed" className="mt-1 w-full rounded bg-slate-800 p-2" type="number" min="0" value={seed} onChange={event => setSeed(Number(event.target.value))} /></label>
        <button disabled={busy || !workspace || !prompt.trim()} onClick={start} className="w-full rounded bg-cyan-700 px-4 py-3 disabled:opacity-40">生成を開始</button>
      </div>
    </section>
    {job && <section className="mt-6 rounded border border-slate-700 p-4" aria-live="polite">
      <h2 className="font-semibold">生成の状態: {job.state}</h2>
      <p className="text-sm text-slate-300">試行 {job.attempt?.number ?? 0} · 待ち順 {job.queuePosition || '実行中または完了'} · Job {job.id.slice(0, 8)}</p>
      {job.attempt?.error_code && <p className="text-amber-300">失敗理由: {job.attempt.error_code}</p>}
      <div className="mt-3 flex gap-2">
        {!['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'WORKER_LOST'].includes(job.state) && <button disabled={busy} onClick={() => action('cancel')} className="rounded bg-slate-700 px-3 py-2">取り消す</button>}
        {['FAILED', 'CANCELLED', 'TIMED_OUT', 'WORKER_LOST'].includes(job.state) && <><button disabled={busy} onClick={() => action('retry')} className="rounded bg-slate-700 px-3 py-2">同じ条件で再実行</button><button disabled={busy} onClick={() => action('retry', seed)} className="rounded bg-slate-700 px-3 py-2">上のSeedで再実行</button></>}
      </div>
    </section>}
    {candidate && <section className="mt-6">
      <h2 className="text-xl font-semibold">結果を比較</h2>
      <p className="text-sm text-slate-300">マスク外変更 {candidate.outsideChangedPixels}画素 · alpha変更 {candidate.alphaChangedPixels}画素 · 候補 {candidate.state}</p>
      <div className="mt-3 flex gap-2">{(['source', 'raw', 'strict'] as const).map(item => <button key={item} aria-pressed={compare === item} onClick={() => setCompare(item)} className={`rounded px-3 py-2 ${compare === item ? 'bg-cyan-700' : 'bg-slate-700'}`}>{{ source: '元画像', raw: 'AI生出力', strict: '厳密保持結果' }[item]}</button>)}</div>
      <img alt={`拡大表示: ${{ source: '元画像', raw: 'AI生出力', strict: '厳密保持結果' }[compare]}`} src={asset({ source: candidate.sourceAssetId, raw: candidate.rawAssetId, strict: candidate.strictAssetId }[compare])} className="mt-4 max-h-[70dvh] w-auto border border-slate-700" />
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {([
          ['元画像', candidate.sourceAssetId],
          ['AI生出力（採用不可）', candidate.rawAssetId],
          ['厳密保持結果', candidate.strictAssetId],
        ] as const).map(([label, id]) => <figure key={id} className="min-w-0"><img alt={label} src={asset(id)} className="w-full border border-slate-700" /><figcaption className="mt-1 text-sm">{label}</figcaption></figure>)}
        <DifferenceView sourceId={candidate.sourceAssetId} strictId={candidate.strictAssetId} maskId={candidate.maskAssetId} />
      </div>
      <p className="mt-4 text-sm">選択中: {{ source: '元画像', raw: 'AI生出力', strict: '厳密保持結果' }[compare]}。採用できるのは厳密保持結果のみです。</p>
      {candidate.state === 'REVIEW_PENDING' && <div className="mt-4 flex gap-2"><button disabled={busy} onClick={adopt} className="rounded bg-cyan-700 px-5 py-3">厳密保持結果を採用</button><button disabled={busy} onClick={reject} className="rounded bg-slate-700 px-5 py-3">不採用にする</button></div>}
      {candidate.state === 'ADOPTED' && <p className="mt-4 text-green-300">新版として採用済みです。</p>}
    </section>}
  </main>;
}
