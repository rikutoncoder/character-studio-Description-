import { randomBytes } from 'node:crypto';
import { mkdirSync, statSync } from 'node:fs';
import { createCertificates } from './certificates.mjs';
import { lanDirectory, lanHost } from './config.mjs';
import { deletePassphrase, readPassphrase, storePassphrase } from './keychain.mjs';

const directory = lanDirectory();
const host = lanHost();
const renew = process.argv.includes('--renew');
mkdirSync(directory, { recursive: true, mode: 0o700 });
if ((statSync(directory).mode & 0o077) !== 0) throw new Error('LAN data directory must have private permissions (0700)');
if (renew) {
  const result = createCertificates(directory, host, readPassphrase(directory), { renew: true });
  console.log(`Server certificate renewed for ${host}. Local CA SHA-256: ${result.fingerprint}`);
} else {
  const passphrase = randomBytes(40).toString('base64url');
  storePassphrase(directory, passphrase);
  try {
    const result = createCertificates(directory, host, passphrase);
    console.log(`Local CA and server certificate created for ${host}. Local CA SHA-256: ${result.fingerprint}`);
    console.log('Only the public CA certificate may be sent to iPhone. Never send ca-key.pem, server-key.pem, or the Keychain secret.');
  } catch (error) {
    deletePassphrase(directory);
    throw error;
  }
}
