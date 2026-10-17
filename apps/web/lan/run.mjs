import { randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { readFileSync, mkdirSync } from 'node:fs';
import { connect } from 'node:net';
import http from 'node:http';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LanAuth } from './auth.mjs';
import { certificatePaths } from './certificates.mjs';
import { lanDirectory, lanHost, lanIp } from './config.mjs';
import { createGateway } from './gateway.mjs';
import { readPassphrase } from './keychain.mjs';

const webDirectory = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const agentDirectory = resolve(webDirectory, '..', '..', 'services', 'generation-agent');
const webPort = 3000;
const agentPort = 8765;

function portOpen(port) {
  return new Promise(resolve => {
    const socket = connect({ host: '127.0.0.1', port });
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => resolve(false));
    socket.setTimeout(1000, () => { socket.destroy(); resolve(false); });
  });
}

async function waitForPort(port, child) {
  let startupError;
  child.once('error', error => { startupError = error; });
  for (let attempt = 0; attempt < 100; attempt++) {
    if (startupError) throw startupError;
    if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Local service on ${port} exited before startup`);
    if (await portOpen(port)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Local service on ${port} did not start within 10 seconds`);
}

function getLocal(port, path, headers = {}) {
  return new Promise((resolve, reject) => {
    const request = http.get({ hostname: '127.0.0.1', port, path, headers, timeout: 5000 }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, body: Buffer.concat(chunks) }));
    });
    request.on('timeout', () => request.destroy(new Error('Local service timed out')));
    request.on('error', reject);
  });
}

async function verifyLocalService(port, path, headers = {}) {
  const response = await getLocal(port, path, headers);
  let payload;
  try { payload = JSON.parse(response.body.toString()); } catch { /* fail below */ }
  if (response.status !== 200 || !Array.isArray(payload?.projects)) {
    throw new Error(`Local service on ${port} failed its authenticated startup check`);
  }
}

function stopChild(child) {
  if (!child || !child.pid || child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise(resolve => {
    const timeout = setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 5000);
    child.once('exit', () => { clearTimeout(timeout); resolve(); });
    child.kill('SIGTERM');
  });
}

const directory = lanDirectory();
const host = lanHost();
const ip = lanIp();
const paths = certificatePaths(directory);
const passphrase = readPassphrase(directory);
const certPem = readFileSync(paths.serverCert);
const keyPem = readFileSync(paths.serverKey);
const caPem = readFileSync(paths.caCert);
const dataDirectory = process.env.CHARACTER_STUDIO_DATA_DIR ?? join(homedir(), 'Library', 'Application Support', 'CharacterStudio', 'Data');
mkdirSync(dataDirectory, { recursive: true, mode: 0o700 });
if (await portOpen(webPort) || await portOpen(agentPort)) throw new Error('Port 3000 or 8765 is already in use; refusing to proxy an unknown local service');
const token = randomBytes(48).toString('base64url');
const python = process.env.CHARACTER_STUDIO_PYTHON ?? 'python3';
let agent, web, auth, gateway, stopping = false;

async function shutdown() {
  if (stopping) return;
  stopping = true;
  if (gateway) await gateway.close();
  await Promise.all([stopChild(web), stopChild(agent)]);
  auth?.close();
}

try {
  agent = spawn(python, ['-m', 'character_studio.internal_api'], {
    cwd: agentDirectory, stdio: 'inherit',
    env: { ...process.env, PYTHONPATH: join(agentDirectory, 'src'), CHARACTER_STUDIO_INTERNAL_TOKEN: token,
      CHARACTER_STUDIO_DATA_DIR: dataDirectory, CHARACTER_STUDIO_INTERNAL_PORT: String(agentPort) },
  });
  await waitForPort(agentPort, agent);
  await verifyLocalService(agentPort, '/internal/projects', { 'X-Internal-Token': token });
  web = spawn(process.execPath, [join(webDirectory, 'node_modules', 'next', 'dist', 'bin', 'next'), 'start', '--hostname', '127.0.0.1', '--port', String(webPort)], {
    cwd: webDirectory, stdio: 'inherit',
    env: { ...process.env, NODE_ENV: 'production', CHARACTER_STUDIO_INTERNAL_TOKEN: token,
      CHARACTER_STUDIO_INTERNAL_URL: `http://127.0.0.1:${agentPort}` },
  });
  await waitForPort(webPort, web);
  await verifyLocalService(webPort, '/api/m2/projects');
  auth = new LanAuth(join(directory, 'auth'));
  gateway = createGateway({ auth, certPem, keyPem, caPem, passphrase, host, ip, upstreamPort: webPort });
  await gateway.listen();
  console.log(`Mac app: http://127.0.0.1:${webPort}/`);
  console.log(`Mac-only device management: http://127.0.0.1:3444/admin`);
  console.log(`iPhone certificate setup: http://${host}:3445/`);
  console.log(`iPhone HTTPS pairing: https://${host}:3443/pair`);
  console.log(`Local CA SHA-256: ${gateway.certificateFingerprint}`);
  for (const child of [agent, web]) child.once('exit', async () => {
    if (!stopping) { console.error('A local service stopped; closing LAN access'); await shutdown(); process.exitCode = 1; }
  });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, async () => { await shutdown(); process.exit(0); });
} catch (error) {
  await shutdown();
  throw error;
}
