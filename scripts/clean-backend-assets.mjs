import { readdir, unlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDirectory = path.join(root, 'dist');
const backendAsset = /^backend(?:-[A-Za-z0-9_]+)?\.js(?:\.map)?$/;

for (const file of await readdir(outputDirectory)) {
  if (backendAsset.test(file)) {
    await unlink(path.join(outputDirectory, file));
  }
}
