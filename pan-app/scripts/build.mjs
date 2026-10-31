import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const output = resolve(root, 'dist');
const isVercelBuild = process.env.VERCEL === '1' || process.env.VERCEL_ENV === 'production';
const configuredBase = (process.env.VERIIDIA_API_BASE_URL || '').trim().replace(/\/$/, '');
let apiOrigin = '';
if (configuredBase) {
  const parsed = new URL(configuredBase);
  if (parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') {
    throw new Error('VERIIDIA_API_BASE_URL must be an origin without credentials, path, query, or fragment.');
  }
  if (isVercelBuild && parsed.protocol !== 'https:') {
    throw new Error('VERIIDIA_API_BASE_URL must use HTTPS in a Vercel deployment.');
  }
  if (isVercelBuild && ['localhost', '127.0.0.1', '::1'].includes(parsed.hostname)) {
    throw new Error('VERIIDIA_API_BASE_URL must not point to localhost in a Vercel deployment.');
  }
  apiOrigin = parsed.origin;
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const file of ['index.html', 'registration.html']) {
  const html = await readFile(resolve(root, file), 'utf8');
  const wsOrigin = apiOrigin ? apiOrigin.replace(/^https:/, 'wss:') : '';
  const connectionSources = ["'self'", apiOrigin, wsOrigin, ...(isVercelBuild ? [] : ['ws:', 'wss:'])].filter(Boolean);
  const csp = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src ${connectionSources.join(' ')}`,
    `frame-src 'self' ${apiOrigin} https://browserbase.com https://*.browserbase.com`.trim(),
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self' https://onlineservices.proteantech.in",
  ].join('; ');
  const policy = `<meta http-equiv="Content-Security-Policy" content="${csp}"><meta name="referrer" content="no-referrer">`;
  await writeFile(resolve(output, file), html.replace('<head>', `<head>\n  ${policy}`), 'utf8');
}
await writeFile(
  resolve(output, 'veriidia-config.js'),
  `window.VERIIDIA_CONFIG = Object.freeze({ apiBaseUrl: ${JSON.stringify(apiOrigin)} });\n`,
  'utf8',
);
console.log(`Built Veriidia static frontend to ${output}`);
