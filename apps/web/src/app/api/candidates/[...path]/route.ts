import { NextRequest, NextResponse } from 'next/server';
import { proxyInternal } from '@/lib/internal-proxy';

const uuid = /^[a-f0-9-]{36}$/;

async function route(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const parts = (await context.params).path;
  if (!parts || !uuid.test(parts[0]) || (parts.length !== 1 && !(parts.length === 2 && ['adopt', 'reject'].includes(parts[1])))) {
    return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  }
  if ((parts.length === 1 && request.method !== 'GET') || (parts.length === 2 && request.method !== 'POST')) {
    return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  }
  return proxyInternal(request, '/candidates/' + parts.join('/'));
}

export { route as GET, route as POST };
