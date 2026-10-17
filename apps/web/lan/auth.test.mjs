import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LanAuth, PAIR_CODE_AGE_MS, SESSION_AGE_MS } from './auth.mjs';

function fixture(run) {
  const directory = mkdtempSync(join(tmpdir(), 'character-studio-lan-test-'));
  let time = 1_700_000_000_000;
  let auth;
  try {
    auth = new LanAuth(directory, { now: () => time });
    run({ auth, directory, advance: milliseconds => { time += milliseconds; }, now: () => time });
  } finally {
    auth?.close();
    rmSync(directory, { recursive: true, force: true });
  }
}

test('pairing code is short lived and one-use; session survives reopen', () => fixture(({ auth, directory, advance }) => {
  const { code } = auth.createPairingCode();
  const first = auth.pair(code, 'My iPhone', '192.168.1.2');
  assert.ok(first.token);
  assert.equal(auth.pair(code, 'Other phone', '192.168.1.3').status, 403);
  assert.equal(auth.session(first.token)?.name, 'My iPhone');
  const reopened = new LanAuth(directory, { now: auth.now });
  try {
    assert.equal(reopened.session(first.token)?.deviceId, first.deviceId);
    advance(SESSION_AGE_MS + 1);
    assert.equal(reopened.session(first.token), null);
  } finally { reopened.close(); }
}));

test('expired pairing code and repeated guesses are rejected', () => fixture(({ auth, advance }) => {
  const { code } = auth.createPairingCode();
  advance(PAIR_CODE_AGE_MS + 1);
  assert.equal(auth.pair(code, 'Phone', '192.168.1.4').status, 403);
  const next = auth.createPairingCode().code;
  for (let index = 0; index < 5; index++) assert.equal(auth.pair('00000000', 'Phone', '192.168.1.5').status, 403);
  assert.equal(auth.pair(next, 'Phone', '192.168.1.5').status, 429);
  advance(10 * 60 * 1000 + 1);
  assert.ok(auth.pair(next, 'Phone', '192.168.1.5').token === undefined, 'the code has also expired');
}));

test('device revocation invalidates every session without changing other devices', () => fixture(({ auth }) => {
  const a = auth.pair(auth.createPairingCode().code, 'A', '192.168.1.6');
  const b = auth.pair(auth.createPairingCode().code, 'B', '192.168.1.7');
  assert.equal(auth.revokeDevice(a.deviceId), true);
  assert.equal(auth.revokeDevice(a.deviceId), false);
  assert.equal(auth.session(a.token), null);
  assert.equal(auth.session(b.token)?.deviceId, b.deviceId);
  assert.equal(auth.devices().find(device => device.id === a.deviceId).revokedAt !== null, true);
}));
