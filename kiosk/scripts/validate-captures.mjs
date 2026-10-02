// Validates kiosk/public/captures/*.json before build so a malformed or
// placeholder capture can never ship silently.
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'captures');
const required = ['id', 'capturedAt', 'tool', 'backend', 'prompt', 'toolCalls', 'responseMarkdown', 'pacing'];

const files = readdirSync(dir).filter((f) => f.endsWith('.json'));
if (files.length === 0) {
  console.error('No captures found in', dir);
  process.exit(1);
}

let failed = false;
for (const file of files) {
  try {
    const capture = JSON.parse(readFileSync(join(dir, file), 'utf8'));
    const missing = required.filter((key) => !(key in capture));
    if (missing.length) throw new Error(`missing keys: ${missing.join(', ')}`);
    if (!capture.responseMarkdown.trim()) throw new Error('empty responseMarkdown');
    if (!Array.isArray(capture.toolCalls)) throw new Error('toolCalls must be an array');
    if (capture.id !== file.replace(/\.json$/, '')) throw new Error(`id "${capture.id}" != filename`);
    console.log(`ok ${file} (${capture.responseMarkdown.length} chars, captured ${capture.capturedAt})`);
  } catch (err) {
    console.error(`FAIL ${file}: ${err.message}`);
    failed = true;
  }
}
process.exit(failed ? 1 : 0);
