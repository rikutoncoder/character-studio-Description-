'use client';
import { useEffect, useState } from 'react';
import { api, type WorkspaceWire } from '@/lib/workspace';

type Project = { id: string; name: string; headVersionId: string | null };
type WorkspaceItem = { id: string; revision: number };

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [items, setItems] = useState<Record<string, WorkspaceItem[]>>({});
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function refresh() {
    const response = await api<{ projects: Project[] }>('/projects');
    setProjects(response.projects);
    const pairs = await Promise.all(response.projects.map(async project => [project.id, (await api<{ workspaces: WorkspaceItem[] }>(`/projects/${project.id}/workspaces`)).workspaces] as const));
    setItems(Object.fromEntries(pairs));
  }
  useEffect(() => { refresh().catch(error => setError(error.message)); }, []);
  async function createProject() {
    if (!name.trim()) return;
    setBusy(true);
    try { await api('/projects', { method: 'POST', body: JSON.stringify({ name }) }); setName(''); await refresh(); }
    catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }
  async function importImage(projectId: string, file?: File) {
    if (!file) return;
    setBusy(true);
    try {
      const contentBase64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1]);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      await api(`/projects/${projectId}/imports`, { method: 'POST', body: JSON.stringify({ contentBase64, extension: file.name.split('.').pop()?.toLowerCase() ?? 'png' }) });
      await refresh();
    } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }
  async function createWorkspace(projectId: string) {
    setBusy(true);
    try {
      const wire = await api<WorkspaceWire>(`/projects/${projectId}/workspaces`, { method: 'POST', body: '{}' });
      location.assign(`/workspace/${wire.id}`);
    } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }
  return <main className="mx-auto max-w-4xl p-5 md:p-10">
    <h1 className="text-3xl font-semibold">Character Studio</h1>
    <p className="mt-2 text-slate-300">M2 画像編集Workspace — 生成前のマスクとガイドを編集します。</p>
    {error && <p role="alert" className="mt-4 rounded bg-red-900 p-3">{error}</p>}
    <div className="mt-8 flex gap-2"><input aria-label="プロジェクト名" className="min-w-0 flex-1 rounded bg-slate-800 p-3" value={name} onChange={event => setName(event.target.value)} placeholder="新しいプロジェクト名" /><button disabled={busy} className="rounded bg-cyan-700 px-5" onClick={createProject}>作成</button></div>
    <div className="mt-8 space-y-5">{projects.map(project => <section key={project.id} className="rounded-xl border border-slate-600 bg-slate-800/60 p-5">
      <h2 className="text-xl font-medium">{project.name}</h2>
      <p className="mt-2 text-sm text-slate-300">832×1216の画像を読み込みます。元画像は変更されません。</p>
      <input className="mt-3 block w-full text-sm" aria-label={`${project.name}の画像を読み込む`} type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => importImage(project.id, event.target.files?.[0])} />
      <button disabled={busy || !project.headVersionId} className="mt-4 rounded bg-cyan-700 px-4 py-2 disabled:opacity-40" onClick={() => createWorkspace(project.id)}>新しいWorkspace</button>
      <ul className="mt-4 space-y-2">{(items[project.id] ?? []).map(item => <li key={item.id}><a className="text-cyan-300 underline" href={`/workspace/${item.id}`}>Workspace {item.id.slice(0, 8)}（保存版 {item.revision}）</a></li>)}</ul>
    </section>)}</div>
  </main>;
}
