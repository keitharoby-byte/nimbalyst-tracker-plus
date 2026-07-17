import { access, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifest = JSON.parse(await readFile(path.join(root, 'manifest.json'), 'utf8'));
const required = [
  manifest.main,
  ...(manifest.styles ? [manifest.styles] : []),
  ...manifest.contributions.backendModules.map((module) => module.entry),
  'dist/reader/server.py',
  'dist/reader/database.py',
  'dist/reader/contracts.py',
  'dist/reader/registry.py',
  'dist/reader/registry.json',
];

for (const relativePath of required) {
  await access(path.join(root, relativePath));
}

const readerFiles = await readdir(path.join(root, 'dist', 'reader'));
if (readerFiles.some((file) => file.endsWith('.pyc') || file === '__pycache__')) {
  throw new Error('Compiled Python cache files must not be packaged.');
}

if (manifest.contributions.backendModules.some((module) =>
  module.permissions.includes('nimbalyst-database-write') ||
  module.permissions.includes('secrets-read')
)) {
  throw new Error('The reader package requests a forbidden permission.');
}

console.log(`Verified ${manifest.id} ${manifest.version}: ${required.length} required assets present.`);
