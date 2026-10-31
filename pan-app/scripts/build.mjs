import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const output = resolve(root, 'dist');
const isVercelBuild = process.env.VERCEL === '1' || process.env.VERCEL_ENV === 'production';
// The Vercel deployment uses same-origin functions. Do not bake a remote API
// origin into the static frontend; the browser and database secrets stay server-side.
const apiOrigin = '';

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const file of ['index.html', 'registration.html']) {
  const html = await readFile(resolve(root, file), 'utf8');
  const connectionSources = ["'self'", ...(isVercelBuild ? [] : ['ws:', 'wss:'])];
  const csp = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src ${connectionSources.join(' ')}`,
    "frame-src 'self'",
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
