import http from 'node:http';
import https from 'node:https';
import { readFileSync } from 'node:fs';
import { lanDirectory, lanHost, lanIp } from './config.mjs';
import { certificatePaths } from './certificates.mjs';

const host = lanHost(), ip = lanIp();
const ca = readFileSync(certificatePaths(lanDirectory()).caCert);
const ownerOrigin = 'http://127.0.0.1:3444';
const httpsOrigin = `https://${host}:3443`;

function call(kind, port, path, { method = 'GET', origin, cookie, csrf, body, lan = false } = {}) {
  return new Promise((resolve, reject) => {
    const headers = { Host: lan ? `${host}:${port}` : `127.0.0.1:${port}` };
    if (origin) headers.Origin = origin;
    if (cookie) headers.Cookie = cookie;
    if (csrf) headers['X-CSRF-Token'] = csrf;
    if (body) { headers['Content-Type'] = 'application/x-www-form-urlencoded'; headers['Content-Length'] = Buffer.byteLength(body); }
    const request = kind.request({ host: lan ? ip : '127.0.0.1', port, path, method, headers,
      ...(kind === https ? { ca, servername: host } : {}), agent: false }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
    });
    request.on('error', reject);
    request.end(body);
  });
}

function expectStatus(actual, expected, step) {
  if (actual !== expected) throw new Error(`${step}: expected HTTP ${expected}, received ${actual}`);
}

const admin = await call(http, 3444, '/admin');
expectStatus(admin.status, 200, 'Mac owner page');
const ownerCookie = admin.headers['set-cookie']?.[0]?.split(';')[0];
const csrf = /const csrf="([^"]+)"/.exec(admin.body.toString())?.[1];
if (!ownerCookie || !csrf) throw new Error('Mac owner session could not be established');
expectStatus((await call(https, 3443, '/api/m2/projects', { lan: true })).status, 401, 'Unpaired HTTPS access');
expectStatus((await call(http, 3445, '/api/m2/projects', { lan: true })).status, 404, 'HTTP API access');
const issued = await call(http, 3444, '/api/pairing-codes', { method: 'POST', cookie: ownerCookie, csrf, origin: ownerOrigin });
expectStatus(issued.status, 201, 'Create pairing code');
const code = JSON.parse(issued.body).code;
const paired = await call(https, 3443, '/api/device-sessions/pair', { lan: true, method: 'POST', origin: httpsOrigin, body: `code=${code}&name=Mac-smoke-test` });
expectStatus(paired.status, 303, 'Pairing');
const deviceCookie = paired.headers['set-cookie']?.[0]?.split(';')[0];
if (!deviceCookie) throw new Error('Device session cookie missing');
const projects = await call(https, 3443, '/api/m2/projects', { lan: true, cookie: deviceCookie });
expectStatus(projects.status, 200, 'Authenticated Next.js to Python API');
if (!Array.isArray(JSON.parse(projects.body).projects)) throw new Error('Python core response is invalid');
const devices = await call(http, 3444, '/api/devices', { cookie: ownerCookie });
const id = JSON.parse(devices.body).devices.find(device => device.name === 'Mac-smoke-test' && !device.revokedAt)?.id;
if (!id) throw new Error('Temporary device not found for revocation');
expectStatus((await call(http, 3444, `/api/devices/${id}/sessions`, { method: 'DELETE', cookie: ownerCookie, csrf, origin: ownerOrigin })).status, 200, 'Revoke device');
expectStatus((await call(https, 3443, '/api/m2/projects', { lan: true, cookie: deviceCookie })).status, 401, 'Revoked device');
console.log('LAN smoke passed: HTTPS, owner pairing, Next.js→Python API, HTTP denial, and revocation');
