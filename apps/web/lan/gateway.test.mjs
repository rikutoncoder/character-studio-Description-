import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import https from 'node:https';
import { X509Certificate } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LanAuth } from './auth.mjs';
import { certificatePaths, createCertificates } from './certificates.mjs';
import { createGateway } from './gateway.mjs';

const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
const close = server => new Promise(resolve => server.close(resolve));
async function freePort() { const server = http.createServer(); const port = await listen(server); await close(server); return port; }

function request(kind, port, path, { method = 'GET', host, origin, cookie, csrf, body, ca, servername } = {}) {
  return new Promise((resolve, reject) => {
    const headers = { Host: host, ...(origin ? { Origin: origin } : {}), ...(cookie ? { Cookie: cookie } : {}), ...(csrf ? { 'X-CSRF-Token': csrf } : {}) };
    if (body) { headers['Content-Type'] = 'application/x-www-form-urlencoded'; headers['Content-Length'] = Buffer.byteLength(body); }
    const options = { host: '127.0.0.1', port, path, method, headers, agent: false, ...(kind === https ? { ca, servername } : {}) };
    const call = kind.request(options, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
    });
    call.on('error', reject);
    call.end(body);
  });
}

test('HTTPS guards assets, pairs once, checks Origin and revokes a phone', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'cs-gateway-test-'));
  const host = 'character-studio-test.local';
  const passphrase = 'temporary-test-passphrase-'.repeat(2);
  let auth, gateway, upstream;
  try {
    createCertificates(directory, host, passphrase);
    const paths = certificatePaths(directory);
    const ca = readFileSync(paths.caCert);
    const requests = [];
    upstream = http.createServer((req, res) => {
      requests.push({ path: req.url, cookie: req.headers.cookie, origin: req.headers.origin });
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('protected image');
    });
    const upstreamPort = await listen(upstream);
    const httpsPort = await freePort(), ownerPort = await freePort(), onboardingPort = await freePort();
    auth = new LanAuth(join(directory, 'auth'));
    gateway = createGateway({ auth, certPem: readFileSync(paths.serverCert), keyPem: readFileSync(paths.serverKey), caPem: ca,
      passphrase, host, ip: '127.0.0.1', httpsPort, ownerPort, onboardingPort, upstreamPort, validateBinding: false });
    await gateway.listen();
    const httpsHost = `${host}:${httpsPort}`, httpsOrigin = `https://${httpsHost}`;
    const ownerHost = `127.0.0.1:${ownerPort}`, ownerOrigin = `http://${ownerHost}`;
    const secureRequest = (path, options = {}) => request(https, httpsPort, path, { host: httpsHost, servername: host, ca, ...options });
    assert.equal((await secureRequest('/api/m2/assets/a/content')).status, 401);
    assert.equal(requests.length, 0);
    assert.equal((await request(http, onboardingPort, '/api/m2/assets/a/content', { host: `${host}:${onboardingPort}` })).status, 404);
    const certificate = await request(http, onboardingPort, '/character-studio-ca.cer', { host: `${host}:${onboardingPort}` });
    assert.equal(certificate.status, 200);
    assert.equal(new X509Certificate(certificate.body).fingerprint256, gateway.certificateFingerprint);

    const admin = await request(http, ownerPort, '/admin', { host: ownerHost });
    assert.equal(admin.status, 200);
    assert.match(admin.headers['content-security-policy'], /connect-src 'self'/);
    const ownerCookie = admin.headers['set-cookie'][0].split(';')[0];
    const csrf = /const csrf="([^"]+)"/.exec(admin.body.toString())?.[1];
    assert.ok(csrf);
    assert.equal((await request(http, ownerPort, '/api/pairing-codes', { host: ownerHost, method: 'POST', cookie: ownerCookie, csrf, origin: 'http://evil.example' })).status, 403);
    const codeResponse = await request(http, ownerPort, '/api/pairing-codes', { host: ownerHost, method: 'POST', cookie: ownerCookie, csrf, origin: ownerOrigin });
    assert.equal(codeResponse.status, 201);
    const { code } = JSON.parse(codeResponse.body);
    const pairingPage = await secureRequest('/pair');
    assert.equal(pairingPage.status, 200);
    assert.match(pairingPage.headers['content-security-policy'], /connect-src 'self'/);
    const paired = await secureRequest('/api/device-sessions/pair', { method: 'POST', origin: httpsOrigin, body: `code=${code}&name=iPhone` });
    assert.equal(paired.status, 303);
    assert.match(paired.headers['set-cookie'][0], /Secure; HttpOnly; SameSite=Strict/);
    const deviceCookie = paired.headers['set-cookie'][0].split(';')[0];
    assert.equal((await secureRequest('/api/device-sessions/pair', { method: 'POST', origin: httpsOrigin, body: `code=${code}&name=Other` })).status, 403);
    assert.equal((await secureRequest('/api/m2/assets/a/content', { cookie: deviceCookie })).body.toString(), 'protected image');
    assert.equal(requests[0].cookie, undefined);
    assert.equal((await secureRequest('/api/m2/workspaces/a', { method: 'PUT', origin: 'https://evil.example', cookie: deviceCookie, body: '{}' })).status, 403);
    assert.equal(requests.length, 1);
    const devices = await request(http, ownerPort, '/api/devices', { host: ownerHost, cookie: ownerCookie });
    const deviceId = JSON.parse(devices.body).devices[0].id;
    assert.equal((await request(http, ownerPort, `/api/devices/${deviceId}/sessions`, { host: ownerHost, method: 'DELETE', cookie: ownerCookie, csrf, origin: ownerOrigin })).status, 200);
    assert.equal((await secureRequest('/api/m2/assets/a/content', { cookie: deviceCookie })).status, 401);
    assert.equal((await secureRequest('/', { host: 'evil.local:3443' })).status, 421);
  } finally {
    if (gateway) await gateway.close();
    if (upstream?.listening) await close(upstream);
    auth?.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
