// One-shot local migration: never prints secret values or stores AWS passwords.
import { readFileSync, writeFileSync, chmodSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
const file = new URL('../.env', import.meta.url);
const lines = readFileSync(file, 'utf8').split('\n');
const removed = [];
const changed = [];
const result = lines.filter(line => {
  const key = line.split('=', 1)[0];
  if (key.startsWith('AWS_CONSOLE_')) { removed.push(key); return false; }
  return true;
}).map(line => {
  const [key, ...parts] = line.split('=');
  const value = parts.join('=');
  if ((key === 'DJANGO_SECRET_KEY' && value === 'cambia-esto') ||
      (key === 'INGEST_TOKEN' && value === 'dev-ingest-token')) {
    changed.push(key);
    return `${key}=${randomBytes(48).toString('hex')}`;
  }
  if (key === 'DJANGO_DEBUG') return 'DJANGO_DEBUG=false';
  return line;
});
writeFileSync(file, result.join('\n'), { mode: 0o600 });
chmodSync(file, 0o600);
console.log(JSON.stringify({ removed, rotated: changed, debug: false }));
