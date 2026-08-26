import { createHash } from 'node:crypto';
import { access, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifest = JSON.parse(await readFile(path.join(root, 'manifest.json'), 'utf8'));
const backendModules = manifest.contributions.backendModules;
const required = [
  manifest.main,
  ...(manifest.styles ? [manifest.styles] : []),
  ...backendModules.map((module) => module.entry),
  'dist/reader/server.py',
  'dist/reader/database.py',
  'dist/reader/contracts.py',
  'dist/reader/registry.py',
  'dist/reader/registry.json',
  'dist/reader/saved-queries.json',
  'dist/reader/bundle-manifest.json',
  'dist/reader/timeline-item.schema.yaml',
];

const expectedBackendModules = {
  'native-tracker-comments-backend': {
    entry: 'dist/backend-read.js',
    permissions: ['mcp-server-register'],
  },
  'native-tracker-projection-backend': {
    entry: 'dist/backend-projection.js',
    permissions: ['mcp-server-register', 'workspace-files'],
  },
};

assertBackendModules();

function assertBackendModules() {
  if (backendModules.length !== 2) {
    throw new Error('Tracker+ must package separate read/query and projection backend modules.');
  }
  for (const module of backendModules) {
    const expected = expectedBackendModules[module.id];
    if (!expected || module.entry !== expected.entry) {
      throw new Error(`Unexpected backend module layout for ${module.id}.`);
    }
    if (JSON.stringify(module.permissions) !== JSON.stringify(expected.permissions)) {
      throw new Error(`Unexpected permissions for backend module ${module.id}.`);
    }
  }
}

for (const relativePath of required) {
  await access(path.join(root, relativePath));
}

const readerFiles = await readdir(path.join(root, 'dist', 'reader'));
if (readerFiles.some((file) => file.endsWith('.pyc') || file === '__pycache__')) {
  throw new Error('Compiled Python cache files must not be packaged.');
}

const readerManifest = JSON.parse(
  await readFile(path.join(root, 'dist', 'reader', 'bundle-manifest.json'), 'utf8'),
);
if (
  readerManifest.formatVersion !== 1
  || readerManifest.extensionVersion !== manifest.version
  || readerManifest.adapterVersion !== 6
  || readerManifest.registryVersion !== 5
) {
  throw new Error('Reader bundle identity does not match the extension release.');
}
for (const [file, expectedHash] of Object.entries(readerManifest.files ?? {})) {
  const bytes = await readFile(path.join(root, 'dist', 'reader', file));
  const actualHash = createHash('sha256').update(bytes).digest('hex');
  if (actualHash !== expectedHash) {
    throw new Error(`Reader bundle hash mismatch for ${file}.`);
  }
}

const distFiles = await readdir(path.join(root, 'dist'));
if (distFiles.includes('backend.js') || distFiles.includes('backend.js.map')) {
  throw new Error('Obsolete single-module backend output must not be packaged.');
}

const rendererBundle = await readFile(path.join(root, manifest.main), 'utf8');
if (rendererBundle.includes('TrackerReferenceChip')) {
  throw new Error('Renderer imports TrackerReferenceChip, which is unavailable in the validated Nimbalyst runtime.');
}

if (backendModules.some((module) =>
  module.permissions.includes('nimbalyst-database-write') ||
  module.permissions.includes('secrets-read')
)) {
  throw new Error('The reader package requests a forbidden permission.');
}

console.log(`Verified ${manifest.id} ${manifest.version}: ${required.length} required assets present.`);
