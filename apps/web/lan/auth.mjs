import { createHash, createHmac, randomBytes, randomInt, randomUUID } from 'node:crypto';
import { mkdirSync, openSync, readFileSync, closeSync, writeFileSync, statSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const SESSION_AGE_MS = 30 * 24 * 60 * 60 * 1000;
export const PAIR_CODE_AGE_MS = 5 * 60 * 1000;
const ATTEMPT_WINDOW_MS = 10 * 60 * 1000;
const MAX_ATTEMPTS = 5;

const sha256 = value => createHash('sha256').update(value).digest('hex');

export class LanAuth {
  constructor(dataDirectory, { now = () => Date.now(), secret } = {}) {
    mkdirSync(dataDirectory, { recursive: true, mode: 0o700 });
    if ((statSync(dataDirectory).mode & 0o077) !== 0) throw new Error('LAN authentication directory must be private (0700)');
    this.now = now;
    this.secret = secret ?? loadSecret(dataDirectory);
    const databasePath = join(dataDirectory, 'lan-auth.sqlite');
    this.db = new DatabaseSync(databasePath, { timeout: 5000 });
    chmodSync(databasePath, 0o600);
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA foreign_keys = ON;
      CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at INTEGER NOT NULL,
        last_seen_at INTEGER NOT NULL, revoked_at INTEGER
      );
      CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
        expires_at INTEGER NOT NULL, last_used_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS sessions_device_idx ON sessions(device_id);
      CREATE TABLE IF NOT EXISTS pair_codes (
        code_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS pair_attempts (
        ip_hash TEXT NOT NULL, attempted_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS pair_attempts_time_idx ON pair_attempts(attempted_at);
    `);
  }

  close() { this.db.close(); }

  codeHash(code) { return createHmac('sha256', this.secret).update(`pair:${code}`).digest('hex'); }
  ipHash(ip) { return createHmac('sha256', this.secret).update(`ip:${ip}`).digest('hex'); }

  createPairingCode() {
    const code = String(randomInt(0, 100_000_000)).padStart(8, '0');
    const expiresAt = this.now() + PAIR_CODE_AGE_MS;
    this.db.exec('BEGIN IMMEDIATE');
    try {
      this.db.exec('DELETE FROM pair_codes');
      this.db.prepare('INSERT INTO pair_codes(code_hash, expires_at) VALUES (?, ?)').run(this.codeHash(code), expiresAt);
      this.db.exec('COMMIT');
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
    return { code, expiresAt };
  }

  pair(code, name, ip) {
    if (typeof code !== 'string' || !/^\d{8}$/.test(code) || typeof name !== 'string' || !name.trim() || name.length > 80) {
      return { error: 'PAIRING_CODE_INVALID', status: 400 };
    }
    const time = this.now();
    const ipHash = this.ipHash(ip);
    this.db.exec('BEGIN IMMEDIATE');
    try {
      this.db.prepare('DELETE FROM pair_attempts WHERE attempted_at <= ?').run(time - ATTEMPT_WINDOW_MS);
      const attempts = this.db.prepare('SELECT count(*) AS total FROM pair_attempts WHERE ip_hash = ?').get(ipHash).total;
      if (attempts >= MAX_ATTEMPTS) {
        this.db.exec('COMMIT');
        return { error: 'PAIRING_RATE_LIMITED', status: 429 };
      }
      this.db.prepare('INSERT INTO pair_attempts(ip_hash, attempted_at) VALUES (?, ?)').run(ipHash, time);
      const row = this.db.prepare('SELECT expires_at FROM pair_codes WHERE code_hash = ?').get(this.codeHash(code));
      if (!row || row.expires_at <= time) {
        this.db.exec('COMMIT');
        return { error: 'PAIRING_CODE_INVALID', status: 403 };
      }
      this.db.exec('DELETE FROM pair_codes');
      const id = randomUUID();
      const token = randomBytes(32).toString('base64url');
      this.db.prepare('INSERT INTO devices(id, name, created_at, last_seen_at) VALUES (?, ?, ?, ?)').run(id, name.trim(), time, time);
      this.db.prepare('INSERT INTO sessions(token_hash, device_id, expires_at, last_used_at) VALUES (?, ?, ?, ?)').run(sha256(token), id, time + SESSION_AGE_MS, time);
      this.db.exec('COMMIT');
      return { deviceId: id, token, expiresAt: time + SESSION_AGE_MS };
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }

  session(token) {
    if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(token)) return null;
    const row = this.db.prepare(`SELECT s.device_id, s.expires_at, d.name FROM sessions s
      JOIN devices d ON d.id = s.device_id
      WHERE s.token_hash = ? AND d.revoked_at IS NULL`).get(sha256(token));
    const time = this.now();
    if (!row || row.expires_at <= time) return null;
    this.db.prepare('UPDATE sessions SET expires_at = ?, last_used_at = ? WHERE token_hash = ?').run(time + SESSION_AGE_MS, time, sha256(token));
    this.db.prepare('UPDATE devices SET last_seen_at = ? WHERE id = ?').run(time, row.device_id);
    return { deviceId: row.device_id, name: row.name, expiresAt: time + SESSION_AGE_MS };
  }

  devices() {
    return this.db.prepare('SELECT id, name, created_at AS createdAt, last_seen_at AS lastSeenAt, revoked_at AS revokedAt FROM devices ORDER BY created_at DESC').all();
  }

  revokeDevice(id) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const changed = this.db.prepare('UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL').run(this.now(), id).changes;
      this.db.prepare('DELETE FROM sessions WHERE device_id = ?').run(id);
      this.db.exec('COMMIT');
      return changed === 1;
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
}

function loadSecret(directory) {
  const path = join(directory, 'auth-secret');
  try {
    const descriptor = openSync(path, 'wx', 0o600);
    try { writeFileSync(descriptor, randomBytes(32)); }
    finally { closeSync(descriptor); }
  } catch (error) { if (error.code !== 'EEXIST') throw error; }
  const secret = readFileSync(path);
  if ((statSync(path).mode & 0o077) !== 0) throw new Error('LAN authentication secret must be private (0600)');
  if (secret.length !== 32) throw new Error('Invalid LAN authentication secret');
  return secret;
}
