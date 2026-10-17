import http from 'node:http';
import https from 'node:https';
import { randomBytes, timingSafeEqual, X509Certificate, createPrivateKey, createPublicKey } from 'node:crypto';
import { isIP } from 'node:net';
import { networkInterfaces } from 'node:os';

const DEVICE_COOKIE = '__Host-cs_device';
const OWNER_COOKIE = 'cs_owner';
const MAX_REQUEST_BYTES = 30 * 1024 * 1024;
const OWNER_AGE_MS = 12 * 60 * 60 * 1000;
const blockedHeaders = new Set([
  'host', 'origin', 'cookie', 'authorization', 'proxy-authorization', 'connection',
  'keep-alive', 'transfer-encoding', 'upgrade', 'content-length', 'x-internal-token',
  'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'forwarded',
]);

export function validateLanBinding(ip, host, interfaces = networkInterfaces()) {
  if (isIP(ip) !== 4 || !/^[a-z0-9-]+\.local$/i.test(host)) throw new Error('A private IPv4 address and stable .local hostname are required');
  const octets = ip.split('.').map(Number);
  const privateIp = octets[0] === 10 || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) || (octets[0] === 192 && octets[1] === 168);
  if (!privateIp) throw new Error('Refusing to bind the LAN gateway to a public address');
  const assigned = Object.values(interfaces).flat().some(item => item?.family === 'IPv4' && !item.internal && item.address === ip);
  if (!assigned) throw new Error('LAN bind address is not assigned to this Mac');
}

export function validateCertificate(certPem, keyPem, caPem, host, passphrase) {
  const cert = new X509Certificate(certPem);
  const ca = new X509Certificate(caPem);
  if (!ca.ca || !cert.verify(ca.publicKey)) throw new Error('HTTPS certificate is not signed by the configured local CA');
  if (!cert.checkHost(host)) throw new Error('HTTPS certificate does not cover the configured LAN hostname');
  if (Date.parse(cert.validFrom) > Date.now() || Date.parse(cert.validTo) <= Date.now() || Date.parse(ca.validTo) <= Date.now()) throw new Error('HTTPS certificate or CA is outside its validity period');
  const certificateKey = cert.publicKey.export({ type: 'spki', format: 'der' });
  const suppliedKey = createPublicKey(createPrivateKey({ key: keyPem, passphrase })).export({ type: 'spki', format: 'der' });
  if (!certificateKey.equals(suppliedKey)) throw new Error('HTTPS certificate and private key do not match');
  return cert.fingerprint256;
}

function cookies(request) {
  return Object.fromEntries((request.headers.cookie ?? '').split(';').map(part => {
    const equal = part.indexOf('=');
    return equal < 0 ? ['', ''] : [part.slice(0, equal).trim(), part.slice(equal + 1).trim()];
  }).filter(([name]) => name));
}

function commonHeaders(extra = {}) {
  return { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff', ...extra };
}

function json(response, status, value, headers = {}) {
  response.writeHead(status, commonHeaders({ 'Content-Type': 'application/json; charset=utf-8', ...headers }));
  response.end(JSON.stringify(value));
}

function html(response, status, value, headers = {}) {
  response.writeHead(status, commonHeaders({
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Security-Policy': "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    ...headers,
  }));
  response.end(value);
}

function fail(response, status, code) { json(response, status, { error: { code } }); }

function validMutationOrigin(request, origin) {
  return request.headers.origin === origin && (!request.headers['sec-fetch-site'] || request.headers['sec-fetch-site'] === 'same-origin');
}

async function body(request, maxBytes = MAX_REQUEST_BYTES) {
  const declared = Number(request.headers['content-length'] ?? 0);
  if (!Number.isFinite(declared) || declared > maxBytes) throw new Error('REQUEST_TOO_LARGE');
  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > maxBytes) throw new Error('REQUEST_TOO_LARGE');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function pathOf(request, origin) {
  if (!request.url?.startsWith('/') || request.url.startsWith('//')) return null;
  try { return new URL(request.url, origin); }
  catch { return null; }
}

function deviceCookie(token) {
  return `${DEVICE_COOKIE}=${token}; Path=/; Max-Age=2592000; Secure; HttpOnly; SameSite=Strict`;
}

function ownerCookie(token) {
  return `${OWNER_COOKIE}=${token}; Path=/; Max-Age=43200; HttpOnly; SameSite=Strict`;
}

function equalSecret(expected, provided) {
  if (typeof provided !== 'string') return false;
  const a = Buffer.from(expected), b = Buffer.from(provided);
  return a.length === b.length && timingSafeEqual(a, b);
}

function pairingPage() {
  return `<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Character Studio 端末登録</title><style>body{font:16px system-ui;max-width:32rem;margin:2rem auto;padding:1rem;background:#111827;color:white}input,button{font:inherit;padding:.7rem;width:100%;box-sizing:border-box;margin:.4rem 0}button{background:#0e7490;color:white;border:0;border-radius:.4rem}</style><h1>iPhoneを登録</h1><p>Macの「端末を追加」に表示された8桁のコードを入力してください。コードの有効期限は5分です。</p><form method="post" action="/api/device-sessions/pair"><label>端末名<input name="name" maxlength="80" required value="iPhone"></label><label>8桁のコード<input name="code" inputmode="numeric" pattern="[0-9]{8}" maxlength="8" required autocomplete="one-time-code"></label><button>登録して開く</button></form><p id="message" role="alert"></p><script>document.querySelector('form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;const response=await fetch(form.action,{method:'POST',body:new URLSearchParams(new FormData(form))});if(response.ok){location.assign('/');return}document.querySelector('#message').textContent=response.status===429?'入力回数の上限です。10分後にMacで新しいコードを作ってください。':'コードが違うか期限切れです。Macで新しいコードを作ってください。'}</script></html>`;
}

function ownerPage(csrf, host, httpsPort, certificateFingerprint) {
  const safeHost = host.replace(/[^a-z0-9.-]/gi, '');
  return `<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Character Studio 端末管理</title><style>body{font:16px system-ui;max-width:36rem;margin:2rem auto;padding:1rem;background:#111827;color:white}button{font:inherit;padding:.7rem;margin:.4rem;background:#0e7490;color:white;border:0;border-radius:.4rem}code{word-break:break-all}</style><h1>端末を追加</h1><p>この画面はMac本体からのみ開けます。iPhoneの接続先: <code>https://${safeHost}:${httpsPort}/pair</code></p><p>専用認証局（公開証明書）のSHA-256指紋: <code>${certificateFingerprint}</code></p><button id="new">5分間有効なコードを作る</button><p id="code" role="status"></p><h2>登録済み端末</h2><ul id="devices"></ul><script>const csrf=${JSON.stringify(csrf)};async function refresh(){const r=await fetch('/api/devices');const data=await r.json();const list=document.querySelector('#devices');list.replaceChildren();for(const device of data.devices??[]){const li=document.createElement('li');li.textContent=device.name+' ('+device.id.slice(0,8)+') '+(device.revokedAt?'解除済み':'有効');if(!device.revokedAt){const button=document.createElement('button');button.textContent='この端末を解除';button.onclick=async()=>{if(!confirm('この端末のアクセスを解除しますか？'))return;await fetch('/api/devices/'+device.id+'/sessions',{method:'DELETE',headers:{'X-CSRF-Token':csrf}});refresh()};li.append(button)}list.append(li)}}document.querySelector('#new').onclick=async()=>{const r=await fetch('/api/pairing-codes',{method:'POST',headers:{'X-CSRF-Token':csrf}});const data=await r.json();document.querySelector('#code').textContent=r.ok?'コード: '+data.code+'（5分間・1回限り）':'作成できませんでした'};refresh()</script></html>`;
}

function proxy(request, response, upstreamPort, payload, cookie) {
  const headers = {};
  for (const [name, value] of Object.entries(request.headers)) if (!blockedHeaders.has(name) && !name.startsWith('x-forwarded-')) headers[name] = value;
  headers.host = `127.0.0.1:${upstreamPort}`;
  headers['content-length'] = String(payload.length);
  const upstream = http.request({ hostname: '127.0.0.1', port: upstreamPort, path: request.url, method: request.method, headers }, incoming => {
    const out = { ...incoming.headers, ...commonHeaders() };
    delete out.connection;
    delete out['transfer-encoding'];
    delete out['set-cookie'];
    out['set-cookie'] = cookie;
    response.writeHead(incoming.statusCode ?? 502, out);
    incoming.pipe(response);
  });
  upstream.on('error', () => { if (!response.headersSent) fail(response, 503, 'APP_UNAVAILABLE'); else response.destroy(); });
  upstream.end(payload);
}

export function createGateway({ auth, certPem, keyPem, caPem, passphrase, host, ip, httpsPort = 3443, ownerPort = 3444, onboardingPort = 3445, upstreamPort = 3000, validateBinding = true }) {
  if (validateBinding) validateLanBinding(ip, host);
  validateCertificate(certPem, keyPem, caPem, host, passphrase);
  const certificateFingerprint = new X509Certificate(caPem).fingerprint256;
  const httpsOrigin = `https://${host}:${httpsPort}`;
  const ownerOrigin = `http://127.0.0.1:${ownerPort}`;
  const onboardingHost = `${host}:${onboardingPort}`;
  const ownerSessions = new Map();
  const secure = https.createServer({ key: keyPem, cert: certPem, passphrase, minVersion: 'TLSv1.2' }, async (request, response) => {
    if (request.headers.host !== `${host}:${httpsPort}`) return fail(response, 421, 'BAD_HOST');
    const url = pathOf(request, httpsOrigin);
    if (!url) return fail(response, 400, 'BAD_PATH');
    if (!['GET', 'HEAD', 'POST', 'PUT', 'DELETE'].includes(request.method)) return fail(response, 405, 'METHOD_NOT_ALLOWED');
    if (!['GET', 'HEAD'].includes(request.method) && !validMutationOrigin(request, httpsOrigin)) return fail(response, 403, 'BAD_ORIGIN');
    if (url.pathname === '/pair' && request.method === 'GET') return html(response, 200, pairingPage());
    if (url.pathname === '/api/device-sessions/pair' && request.method === 'POST') {
      let raw;
      try { raw = await body(request, 2048); }
      catch { return fail(response, 413, 'REQUEST_TOO_LARGE'); }
      let input;
      try {
        input = request.headers['content-type']?.startsWith('application/json') ? JSON.parse(raw.toString()) : Object.fromEntries(new URLSearchParams(raw.toString()));
      } catch { return fail(response, 400, 'BAD_BODY'); }
      const result = auth.pair(input.code, input.name, request.socket.remoteAddress ?? 'unknown');
      if (result.error) return fail(response, result.status, result.error);
      response.writeHead(303, commonHeaders({ Location: '/', 'Set-Cookie': deviceCookie(result.token) }));
      return response.end();
    }
    const session = auth.session(cookies(request)[DEVICE_COOKIE]);
    if (!session) return fail(response, 401, 'AUTH_REQUIRED');
    if (url.pathname.startsWith('/api/device-sessions/') || url.pathname.startsWith('/api/pairing-codes') || url.pathname.startsWith('/api/devices')) return fail(response, 403, 'OWNER_ONLY');
    let payload;
    try { payload = await body(request); }
    catch { return fail(response, 413, 'REQUEST_TOO_LARGE'); }
    return proxy(request, response, upstreamPort, payload, deviceCookie(cookies(request)[DEVICE_COOKIE]));
  });
  const owner = http.createServer((request, response) => {
    if (request.headers.host !== `127.0.0.1:${ownerPort}`) return fail(response, 421, 'BAD_HOST');
    const url = pathOf(request, ownerOrigin);
    if (!url) return fail(response, 400, 'BAD_PATH');
    let token = cookies(request)[OWNER_COOKIE];
    let session = ownerSessions.get(token);
    if (request.method === 'GET' && url.pathname === '/admin') {
      if (!session || session.expiresAt <= Date.now()) {
        token = randomBytes(32).toString('base64url');
        session = { csrf: randomBytes(24).toString('base64url'), expiresAt: Date.now() + OWNER_AGE_MS };
        ownerSessions.set(token, session);
      }
      return html(response, 200, ownerPage(session.csrf, host, httpsPort, certificateFingerprint), { 'Set-Cookie': ownerCookie(token) });
    }
    if (!session || session.expiresAt <= Date.now()) return fail(response, 401, 'OWNER_SESSION_REQUIRED');
    if (request.method === 'GET' && url.pathname === '/api/devices') return json(response, 200, { devices: auth.devices() });
    if (!validMutationOrigin(request, ownerOrigin) || !equalSecret(session.csrf, request.headers['x-csrf-token'])) return fail(response, 403, 'BAD_ORIGIN_OR_CSRF');
    if (request.method === 'POST' && url.pathname === '/api/pairing-codes') return json(response, 201, auth.createPairingCode());
    const match = /^\/api\/devices\/([a-f0-9-]{36})\/sessions$/.exec(url.pathname);
    if (request.method === 'DELETE' && match) return json(response, auth.revokeDevice(match[1]) ? 200 : 404, { ok: true });
    return fail(response, 404, 'NOT_FOUND');
  });
  const onboarding = http.createServer((request, response) => {
    if (request.headers.host !== onboardingHost) return fail(response, 421, 'BAD_HOST');
    const url = pathOf(request, `http://${onboardingHost}`);
    if (request.method !== 'GET' || !url) return fail(response, 404, 'NOT_FOUND');
    if (url.pathname === '/character-studio-ca.cer') {
      response.writeHead(200, commonHeaders({ 'Content-Type': 'application/x-x509-ca-cert', 'Content-Disposition': 'attachment; filename="character-studio-ca.cer"' }));
      return response.end(new X509Certificate(caPem).raw);
    }
    if (url.pathname !== '/') return fail(response, 404, 'NOT_FOUND');
    return html(response, 200, `<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Character Studio 接続準備</title><h1>安全な接続の準備</h1><p>このHTTPページでは画像・編集・認証はできません。Macの端末管理画面と以下の指紋が一致することを確認してください。一致しなければ登録しないでください。</p><p>公開証明書のSHA-256指紋: <code>${certificateFingerprint}</code></p><p><a href="/character-studio-ca.cer">公開証明書を取得</a></p><p>iPhoneの設定で信頼設定後、<a href="${httpsOrigin}/pair">HTTPSの端末登録ページ</a>へ進みます。</p>`);
  });
  return { secure, owner, onboarding, certificateFingerprint, async listen() {
    const start = (server, port, address) => new Promise((resolve, reject) => {
      const onError = error => { server.off('listening', onListening); reject(error); };
      const onListening = () => { server.off('error', onError); resolve(); };
      server.once('error', onError);
      server.once('listening', onListening);
      server.listen(port, address);
    });
    try {
      await start(owner, ownerPort, '127.0.0.1');
      await start(secure, httpsPort, ip);
      await start(onboarding, onboardingPort, ip);
    } catch (error) {
      await Promise.all([secure, owner, onboarding].filter(server => server.listening).map(server => new Promise(resolve => server.close(resolve))));
      throw error;
    }
  }, async close() { await Promise.all([secure, owner, onboarding].filter(server => server.listening).map(server => new Promise(resolve => server.close(resolve)))); } };
}
