import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, chmodSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const source = join(dirname(fileURLToPath(import.meta.url)), 'keychain.c');

export function keychainAccount(directory) {
  return createHash('sha256').update(directory).digest('hex').slice(0, 24);
}

function helper(directory) {
  const hash = createHash('sha256').update(readFileSync(source)).digest('hex').slice(0, 12);
  const output = join(directory, `keychain-helper-${hash}`);
  if (!existsSync(output)) {
    execFileSync('/usr/bin/clang', [source, '-framework', 'Security', '-framework', 'CoreFoundation', '-o', output], { stdio: ['ignore', 'pipe', 'pipe'], timeout: 30000 });
    chmodSync(output, 0o700);
  }
  return output;
}

function keychain(directory, operation, input) {
  const result = spawnSync(helper(directory), [operation, keychainAccount(directory)], {
    input, timeout: 15000, maxBuffer: 4096,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  if (result.error || result.status !== 0) throw new Error('Mac Keychain operation failed; no private key material was printed');
  return result.stdout;
}

export function storePassphrase(directory, passphrase) {
  if (typeof passphrase !== 'string' || passphrase.length < 32) throw new Error('Passphrase is missing');
  keychain(directory, 'store', Buffer.from(passphrase));
}

export function readPassphrase(directory) {
  const passphrase = keychain(directory, 'read').toString('utf8');
  if (passphrase.length < 32) throw new Error('Mac Keychain returned an invalid passphrase');
  return passphrase;
}

export function deletePassphrase(directory) {
  keychain(directory, 'delete');
}
