import { execFileSync } from 'node:child_process';
import { homedir, networkInterfaces } from 'node:os';
import { join } from 'node:path';

export function lanDirectory() {
  return process.env.CHARACTER_STUDIO_LAN_DATA_DIR ?? join(homedir(), 'Library', 'Application Support', 'CharacterStudio', 'LAN');
}

export function lanHost() {
  if (process.env.CHARACTER_STUDIO_LAN_HOST) return process.env.CHARACTER_STUDIO_LAN_HOST.toLowerCase();
  const localName = execFileSync('/usr/sbin/scutil', ['--get', 'LocalHostName'], { encoding: 'utf8' }).trim();
  return `${localName}.local`.toLowerCase();
}

export function lanIp() {
  if (process.env.CHARACTER_STUDIO_LAN_IP) return process.env.CHARACTER_STUDIO_LAN_IP;
  const candidates = Object.values(networkInterfaces()).flat().filter(item => item?.family === 'IPv4' && !item.internal && /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(item.address));
  if (candidates.length !== 1) throw new Error('Specify CHARACTER_STUDIO_LAN_IP; exactly one private LAN address could not be selected');
  return candidates[0].address;
}
