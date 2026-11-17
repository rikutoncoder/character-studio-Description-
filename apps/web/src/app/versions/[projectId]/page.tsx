import VersionHistory from '@/components/VersionHistory';

export default async function Page({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <VersionHistory projectId={projectId} />;
}
