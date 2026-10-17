import { NextRequest, NextResponse } from 'next/server';

const allowed = /^\/(projects(?:\/[a-f0-9-]+(?:\/(?:imports|workspaces))?)?|workspaces\/[a-f0-9-]+(?:\/fork)?|assets\/[a-f0-9-]+\/content)$/;

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const suffix = `/${path.join('/')}`;
  if (!allowed.test(suffix)) return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  if (request.method !== 'GET') {
    const origin = request.headers.get('origin');
    if (origin && origin !== request.nextUrl.origin) return NextResponse.json({ error: { code: 'BAD_ORIGIN' } }, { status: 403 });
  }
  const token = process.env.CHARACTER_STUDIO_INTERNAL_TOKEN;
  if (!token) return NextResponse.json({ error: { code: 'CORE_NOT_CONFIGURED' } }, { status: 503 });
  const base = process.env.CHARACTER_STUDIO_INTERNAL_URL ?? 'http://127.0.0.1:8765';
  const url = new URL(`/internal${suffix}`, base);
  if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') {
    return NextResponse.json({ error: { code: 'CORE_MUST_BE_LOCAL' } }, { status: 503 });
  }
  try {
    const body = request.method === 'GET' ? undefined : await request.arrayBuffer();
    const upstream = await fetch(url, {
      method: request.method, body, cache: 'no-store',
      headers: { 'X-Internal-Token': token, 'Content-Type': request.headers.get('content-type') ?? 'application/json' },
    });
    // Finish reading the localhost response before handing it to Next. A client
    // disconnect must not leave the Python response stream half-consumed.
    const responseBody = await upstream.arrayBuffer();
    return new NextResponse(responseBody, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/octet-stream', 'Cache-Control': 'no-store' },
    });
  } catch {
    return NextResponse.json({ error: { code: 'CORE_UNAVAILABLE' } }, { status: 503 });
  }
}
export { handle as GET, handle as POST, handle as PUT };
