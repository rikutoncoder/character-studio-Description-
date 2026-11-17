'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/workspace';

type Version = {
  id: string; projectId: string; parentVersionId: string | null;
  restoredFromVersionId: string | null; canonicalAssetId: string;
  ordinal: number; createdAt: string; operationType: 'IMPORT' | 'GENERATION' | 'RESTORE';
  generationType: string | null; prompt: string | null; presetJson: string | null;
};
type History = { projectId: string; projectName: string; headVersionId: string | null; versions: Version[] };
type PendingRestore = { versionId: string; projectId: string; expectedHeadVersionId: string; key: string };

function pendingKey(projectId: string) { return `character-studio-restore-${projectId}`; }
function label(version: Version) {
  if (version.operationType === 'RESTORE') return '過去版から復元';
  if (version.operationType === 'GENERATION') return version.generationType === 'STRUCTURE_ADD' ? '衣装追加' : '色変更';
  return '画像読み込み';
}
function prettyPreset(value: string) {
  try { return JSON.stringify(JSON.parse(value), null, 2); }
  catch { return value; }
}

export default function VersionHistory({ projectId }: { projectId: string }) {
  const [history, setHistory] = useState<History | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingRestore | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const refresh = useCallback(async () => {
    const result = await api<History>(`/projects/${projectId}/versions`);
    setHistory(result);
    setSelectedId(previous => previous && result.versions.some(item => item.id === previous)
      ? previous : result.versions[0]?.id ?? null);
  }, [projectId]);

  useEffect(() => {
    const stored = sessionStorage.getItem(pendingKey(projectId));
    if (stored) {
      try {
        const value = JSON.parse(stored) as PendingRestore;
        if (value.projectId === projectId && typeof value.key === 'string') setPending(value);
      } catch { sessionStorage.removeItem(pendingKey(projectId)); }
    }
    refresh().catch(failure => setError((failure as Error).message));
  }, [projectId, refresh]);

  async function sendRestore(request: PendingRestore) {
    setBusy(true); setError(''); setMessage('');
    try {
      const restored = await api<Version>(`/versions/${request.versionId}/restore`, {
        method: 'POST', headers: { 'Idempotency-Key': request.key },
        body: JSON.stringify({ projectId: request.projectId,
          expectedHeadVersionId: request.expectedHeadVersionId }),
      });
      sessionStorage.removeItem(pendingKey(projectId));
      setPending(null);
      await refresh();
      setSelectedId(restored.id);
      setMessage(`版 ${restored.ordinal} を新しく作成しました。以前の版は残っています。`);
    } catch (failure) {
      const response = failure as Error & { status?: number; code?: string };
      if (response.status === 409) {
        sessionStorage.removeItem(pendingKey(projectId));
        setPending(null);
        setError('別の操作で現在の版が変わりました。最新版を確認してから、必要なら復元をやり直してください。');
        await refresh().catch(() => {});
      } else {
        setError('復元の結果を確認できませんでした。「前回の復元を確認」を押すと、同じ依頼を安全に再試行します。');
      }
    } finally { setBusy(false); }
  }

  function restore(version: Version) {
    if (!history?.headVersionId || busy || pending || sessionStorage.getItem(pendingKey(projectId))) return;
    if (!window.confirm(`版 ${version.ordinal} の画像を、現在版の次に新しい版として復元しますか？ 既存の版は消えません。`)) return;
    const request = { versionId: version.id, projectId,
      expectedHeadVersionId: history.headVersionId, key: crypto.randomUUID() };
    sessionStorage.setItem(pendingKey(projectId), JSON.stringify(request));
    setPending(request);
    void sendRestore(request);
  }

  const selected = history?.versions.find(item => item.id === selectedId);
  return <main className="mx-auto max-w-6xl p-4 md:p-8">
    <a href="/" className="text-cyan-300 underline">← プロジェクト一覧へ</a>
    <h1 className="mt-4 text-2xl font-semibold">版履歴{history ? ` — ${history.projectName}` : ''}</h1>
    <p className="mt-2 text-sm text-slate-300">過去の版はいつでも見られます。復元すると、選んだ画像を使う新しい版が追加されます。</p>
    {error && <p role="alert" className="mt-4 rounded bg-red-900 p-3">{error}</p>}
    {message && <p role="status" className="mt-4 rounded bg-emerald-900 p-3">{message}</p>}
    {pending && <button disabled={busy} className="mt-4 rounded bg-amber-700 px-4 py-2 disabled:opacity-50"
      onClick={() => void sendRestore(pending)}>前回の復元を確認</button>}
    <div className="mt-6 grid gap-6 md:grid-cols-[minmax(240px,340px)_1fr]">
      <nav aria-label="版の一覧" className="space-y-2">
        {history?.versions.map(version => <button key={version.id} onClick={() => setSelectedId(version.id)}
          aria-current={selectedId === version.id ? 'true' : undefined}
          className={`block w-full rounded border p-3 text-left ${selectedId === version.id ? 'border-cyan-300 bg-slate-700' : 'border-slate-600 bg-slate-800'}`}>
          <span className="font-medium">版 {version.ordinal}</span>
          {version.id === history.headVersionId && <span className="ml-2 rounded bg-emerald-800 px-2 text-xs">現在版</span>}
          <span className="mt-1 block text-sm">{label(version)} · {new Date(version.createdAt).toLocaleString('ja-JP')}</span>
        </button>)}
      </nav>
      {selected && <section aria-label="選択した版" className="rounded border border-slate-600 bg-slate-800 p-4">
        <h2 className="text-xl font-medium">版 {selected.ordinal} {selected.id === history?.headVersionId ? '（現在版）' : ''}</h2>
        <img className="mt-4 max-h-[65vh] w-full object-contain" alt={`版 ${selected.ordinal} の画像`}
          src={`/api/m2/assets/${selected.canonicalAssetId}/content`} />
        <dl className="mt-4 space-y-1 text-sm text-slate-300">
          <div>操作: {label(selected)}</div>
          <div>親の版: {selected.parentVersionId ? `版 ${history?.versions.find(item => item.id === selected.parentVersionId)?.ordinal ?? '?'}` : 'なし'}</div>
          {selected.restoredFromVersionId && <div>復元元: 版 {history?.versions.find(item => item.id === selected.restoredFromVersionId)?.ordinal ?? '?'}</div>}
          {selected.prompt && <div>生成指示: {selected.prompt}</div>}
          {selected.presetJson && <details><summary>生成条件</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap">{prettyPreset(selected.presetJson)}</pre></details>}
        </dl>
        {selected.id !== history?.headVersionId && <button disabled={busy || !!pending}
          className="mt-5 rounded bg-cyan-700 px-4 py-3 disabled:opacity-50" onClick={() => restore(selected)}>
          この版を基に新版を作成
        </button>}
      </section>}
    </div>
  </main>;
}
