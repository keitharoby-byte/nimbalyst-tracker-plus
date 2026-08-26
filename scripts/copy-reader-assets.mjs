import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'reader');
const destination = path.join(root, 'dist', 'reader');
const packageManifest = JSON.parse(await readFile(path.join(root, 'manifest.json'), 'utf8'));
const registry = JSON.parse(await readFile(path.join(source, 'registry.json'), 'utf8'));

await mkdir(path.dirname(destination), { recursive: true });
await mkdir(destination, { recursive: true });
await rm(path.join(destination, '__pycache__'), { recursive: true, force: true });
await rm(path.join(destination, 'bundle-manifest.json'), { force: true });

for (const entry of await readdir(source, { withFileTypes: true })) {
  if (!entry.isFile() || entry.name.endsWith('.pyc')) continue;
  await copyFile(path.join(source, entry.name), path.join(destination, entry.name));
}

await copyFile(
  path.join(root, '.nimbalyst', 'trackers', 'timeline-item.yaml'),
  path.join(destination, 'timeline-item.schema.yaml'),
);

const files = {};
for (const entry of (await readdir(destination, { withFileTypes: true }))
  .filter((candidate) => candidate.isFile() && candidate.name !== 'bundle-manifest.json')
  .sort((left, right) => left.name.localeCompare(right.name))) {
  const bytes = await readFile(path.join(destination, entry.name));
  files[entry.name] = createHash('sha256').update(bytes).digest('hex');
}

const identity = JSON.stringify({
  extensionVersion: packageManifest.version,
  adapterVersion: 9,
  registryVersion: registry.version,
  files,
});
const bundleManifest = {
  formatVersion: 1,
  generationId: createHash('sha256').update(identity).digest('hex'),
  extensionVersion: packageManifest.version,
  adapterVersion: 9,
  registryVersion: registry.version,
  files,
};
const manifestTarget = path.join(destination, 'bundle-manifest.json');
const manifestTemporary = `${manifestTarget}.tmp`;
await writeFile(manifestTemporary, `${JSON.stringify(bundleManifest, null, 2)}\n`, 'utf8');
await rename(manifestTemporary, manifestTarget);
