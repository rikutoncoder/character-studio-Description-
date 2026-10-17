import GenerationWorkspace from '@/components/GenerationWorkspace';

export default async function GenerationPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  return <GenerationWorkspace workspaceId={workspaceId} />;
}
