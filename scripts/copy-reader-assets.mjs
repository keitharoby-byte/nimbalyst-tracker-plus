import { copyFile, mkdir, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'reader');
const destination = path.join(root, 'dist', 'reader');

await mkdir(path.dirname(destination), { recursive: true });
await mkdir(destination, { recursive: true });
await rm(path.join(destination, '__pycache__'), { recursive: true, force: true });

for (const entry of await readdir(source, { withFileTypes: true })) {
  if (!entry.isFile() || entry.name.endsWith('.pyc')) continue;
  await copyFile(path.join(source, entry.name), path.join(destination, entry.name));
}
