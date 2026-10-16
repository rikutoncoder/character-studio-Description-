import WorkspaceEditor from '@/components/WorkspaceEditor';
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceEditor id={id} />;
}
