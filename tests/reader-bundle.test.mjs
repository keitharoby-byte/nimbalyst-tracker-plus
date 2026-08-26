import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  prepareReaderSnapshot,
  ReaderBundleError,
  removeReaderSnapshot,
} from '../src/readerBundle.ts';

const REQUIRED_FILES = [
  'server.py',
  'database.py',
  'contracts.py',
  'registry.py',
  'registry.json',
  'saved-queries.json',
];

function hash(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

async function fixture() {
  const extensionPath = await mkdtemp(path.join(tmpdir(), 'tracker-plus-extension-'));
  const reader = path.join(extensionPath, 'dist', 'reader');
  await mkdir(reader, { recursive: true });
  const files = {};
  for (const file of REQUIRED_FILES) {
    const content = Buffer.from(`fixture:${file}`);
    await writeFile(path.join(reader, file), content);
    files[file] = hash(content);
  }
  const identity = {
    extensionVersion: '9.9.9',
    adapterVersion: 9,
    registryVersion: 5,
    files,
  };
  const manifest = {
    formatVersion: 1,
    generationId: hash(JSON.stringify(identity)),
    ...identity,
  };
  await writeFile(
    path.join(reader, 'bundle-manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );
  return { extensionPath, reader, manifest };
}

test('reader snapshot stays coherent when installed files change afterward', async () => {
  const source = await fixture();
  let snapshot;
  try {
    snapshot = await prepareReaderSnapshot(source.extensionPath, { attempts: 1, retryMs: 0 });
    await writeFile(path.join(source.reader, 'registry.json'), 'new generation');
    assert.equal(
      await readFile(path.join(snapshot.directory, 'registry.json'), 'utf8'),
      'fixture:registry.json',
    );
    assert.equal(snapshot.manifest.generationId, source.manifest.generationId);
    assert.notEqual(snapshot.directory, source.reader);
  } finally {
    await removeReaderSnapshot(snapshot ?? null);
    await rm(source.extensionPath, { recursive: true, force: true });
  }
});

test('mismatched live-update assets fail with restart-required diagnostics', async () => {
  const source = await fixture();
  try {
    await writeFile(path.join(source.reader, 'saved-queries.json'), 'partial generation');
    await assert.rejects(
      prepareReaderSnapshot(source.extensionPath, { attempts: 1, retryMs: 0 }),
      (error) => {
        assert.ok(error instanceof ReaderBundleError);
        assert.equal(error.code, 'READER_RESTART_REQUIRED');
        assert.equal(error.details.extensionVersion, '9.9.9');
        assert.equal(error.details.adapterVersion, 9);
        assert.equal(error.details.registryVersion, 5);
        assert.match(error.details.assetPath, /saved-queries\.json$/);
        assert.match(error.details.expectedHash, /^[a-f0-9]{64}$/);
        assert.match(error.details.actualHash, /^[a-f0-9]{64}$/);
        return true;
      },
    );
  } finally {
    await rm(source.extensionPath, { recursive: true, force: true });
  }
});
