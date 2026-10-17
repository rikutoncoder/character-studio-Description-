import { NextRequest, NextResponse } from 'next/server';
import { proxyInternal } from '@/lib/internal-proxy';

const uuid = /^[a-f0-9-]{36}$/;
type Context = { params: Promise<{ path?: string[] }> };

async function route(request: NextRequest, context: Context) {
  const parts = (await context.params).path ?? [];
  if (parts.length > 2 || (parts[0] && !uuid.test(parts[0]))) {
    return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  }
  if (parts.length === 0 && request.method !== 'POST') return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  if (parts.length === 1 && request.method !== 'GET') return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  if (parts.length === 2 && !['events', 'cancel', 'retry'].includes(parts[1])) {
    return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  }
  const suffix = '/generation-jobs' + (parts.length ? '/' + parts.join('/') : '');
  if (parts[1] === 'events') {
    if (request.method !== 'GET') return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
    const last = Number(request.headers.get('last-event-id') ?? request.nextUrl.searchParams.get('after') ?? '0');
    if (!Number.isSafeInteger(last) || last < 0) return NextResponse.json({ error: { code: 'INVALID_EVENT_ID' } }, { status: 400 });
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        let after = last;
        const deadline = Date.now() + 25_000;
        try {
          while (Date.now() < deadline && !request.signal.aborted) {
            const internal = new URL(request.url);
            internal.search = '?after=' + after;
            const response = await proxyInternal(new NextRequest(internal, { headers: request.headers }), suffix);
            if (!response.ok) {
              controller.enqueue(encoder.encode('event: error\ndata: {}\n\n'));
              break;
            }
            const payload = await response.json() as { events: { id: number; type: string; payload: unknown }[] };
            for (const event of payload.events) {
              after = event.id;
              controller.enqueue(encoder.encode(
                'id: ' + event.id + '\nevent: ' + event.type + '\ndata: ' + JSON.stringify(event.payload) + '\n\n',
              ));
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
        } catch {
          // EventSource reconnects; GET job is authoritative after a disconnect.
        } finally {
          try { controller.close(); } catch { /* Client already disconnected. */ }
        }
      },
    });
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no' } });
  }
  if (parts.length === 2 && request.method !== 'POST') return NextResponse.json({ error: { code: 'NOT_FOUND' } }, { status: 404 });
  return proxyInternal(request, suffix);
}

export { route as GET, route as POST };
