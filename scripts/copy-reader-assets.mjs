import { cp, mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'reader');
const destination = path.join(root, 'dist', 'reader');

await mkdir(path.dirname(destination), { recursive: true });
await rm(destination, { recursive: true, force: true });
await cp(source, destination, {
  recursive: true,
  filter: (entry) => !entry.includes('__pycache__') && !entry.endsWith('.pyc'),
});
