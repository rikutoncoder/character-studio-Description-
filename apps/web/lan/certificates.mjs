import { execFileSync } from 'node:child_process';
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomBytes, X509Certificate } from 'node:crypto';
import { validateCertificate } from './gateway.mjs';

function openssl(arguments_, passphrase) {
  execFileSync('openssl', arguments_, { input: passphrase, stdio: ['pipe', 'pipe', 'pipe'] });
}

export function certificatePaths(directory) {
  const root = join(directory, 'certs');
  return { root, caKey: join(root, 'ca-key.pem'), caCert: join(root, 'ca-cert.pem'), serverKey: join(root, 'server-key.pem'), serverCert: join(root, 'server-cert.pem') };
}

export function createCertificates(directory, host, passphrase, { renew = false } = {}) {
  if (!/^[a-z0-9-]+\.local$/i.test(host)) throw new Error('Certificate hostname must be a .local name');
  if (typeof passphrase !== 'string' || passphrase.length < 32) throw new Error('Certificate passphrase is missing');
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  if ((statSync(directory).mode & 0o077) !== 0) throw new Error('LAN certificate directory must be private (0700)');
  const current = certificatePaths(directory);
  if (renew !== existsSync(current.root)) throw new Error(renew ? 'Existing local CA was not found' : 'Certificates already exist; use renewal instead');
  const staging = mkdtempSync(join(directory, '.cert-staging-'));
  try {
    const paths = { caKey: join(staging, 'ca-key.pem'), caCert: join(staging, 'ca-cert.pem'), serverKey: join(staging, 'server-key.pem'), serverCert: join(staging, 'server-cert.pem') };
    if (renew) {
      copyFileSync(current.caKey, paths.caKey);
      copyFileSync(current.caCert, paths.caCert);
    } else {
      openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-aes-256-cbc', '-pass', 'stdin', '-out', paths.caKey], passphrase);
      openssl(['req', '-x509', '-new', '-key', paths.caKey, '-passin', 'stdin', '-out', paths.caCert,
        '-days', '3650', '-sha256', '-subj', '/CN=Character Studio Local CA',
        '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
        '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
        '-addext', `nameConstraints=critical,permitted;DNS:${host}`], passphrase);
    }
    openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-aes-256-cbc', '-pass', 'stdin', '-out', paths.serverKey], passphrase);
    const csr = join(staging, 'server.csr');
    const extensions = join(staging, 'server.ext');
    openssl(['req', '-new', '-key', paths.serverKey, '-passin', 'stdin', '-out', csr, '-subj', `/CN=${host}`], passphrase);
    writeFileSync(extensions, `subjectAltName=DNS:${host}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n`, { mode: 0o600 });
    openssl(['x509', '-req', '-in', csr, '-CA', paths.caCert, '-CAkey', paths.caKey, '-passin', 'stdin',
      '-out', paths.serverCert, '-days', '365', '-sha256', '-set_serial', `0x${randomBytes(16).toString('hex')}`, '-extfile', extensions], passphrase);
    execFileSync('openssl', ['verify', '-CAfile', paths.caCert, paths.serverCert], { stdio: ['ignore', 'pipe', 'pipe'] });
    const caPem = readFileSync(paths.caCert), serverPem = readFileSync(paths.serverCert), keyPem = readFileSync(paths.serverKey);
    validateCertificate(serverPem, keyPem, caPem, host, passphrase);
    const fingerprint = new X509Certificate(caPem).fingerprint256;
    rmSync(csr);
    rmSync(extensions);
    chmodSync(paths.caKey, 0o600);
    chmodSync(paths.serverKey, 0o600);
    if (renew) {
      const previous = join(directory, 'certs-previous');
      if (existsSync(previous)) throw new Error('Previous certificate backup already exists; review it before renewing again');
      renameSync(current.root, previous);
      try { renameSync(staging, current.root); }
      catch (error) { renameSync(previous, current.root); throw error; }
    } else renameSync(staging, current.root);
    return { fingerprint, paths: certificatePaths(directory) };
  } catch (error) {
    if (existsSync(staging)) rmSync(staging, { recursive: true });
    throw error;
  }
}
