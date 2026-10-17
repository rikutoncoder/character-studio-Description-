import { NextRequest, NextResponse } from 'next/server';

export async function proxyInternal(request: NextRequest, suffix: string, method = request.method) {
  if (method !== 'GET') {
    const origin = request.headers.get('origin');
    const host = request.headers.get('host');
    const localOrigin = host && /^(127\.0\.0\.1|localhost):\d+$/.test(host) ? `http://${host}` : null;
    if (origin && origin !== request.nextUrl.origin && origin !== localOrigin) {
      return NextResponse.json({ error: { code: 'BAD_ORIGIN' } }, { status: 403 });
    }
  }
  const token = process.env.CHARACTER_STUDIO_INTERNAL_TOKEN;
  if (!token) return NextResponse.json({ error: { code: 'CORE_NOT_CONFIGURED' } }, { status: 503 });
  const base = process.env.CHARACTER_STUDIO_INTERNAL_URL ?? 'http://127.0.0.1:8765';
  const url = new URL('/internal' + suffix, base);
  if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
    return NextResponse.json({ error: { code: 'CORE_MUST_BE_LOCAL' } }, { status: 503 });
  }
  if (method === 'GET' && request.nextUrl.search) url.search = request.nextUrl.search;
  try {
    const body = method === 'GET' ? undefined : await request.arrayBuffer();
    const upstream = await fetch(url, {
      method, body, cache: 'no-store',
      headers: {
        'X-Internal-Token': token,
        'Content-Type': request.headers.get('content-type') ?? 'application/json',
        'Idempotency-Key': request.headers.get('idempotency-key') ?? '',
      },
    });
    const bytes = await upstream.arrayBuffer();
    return new NextResponse(bytes, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json', 'Cache-Control': 'no-store' },
    });
  } catch {
    return NextResponse.json({ error: { code: 'CORE_UNAVAILABLE' } }, { status: 503 });
  }
}
