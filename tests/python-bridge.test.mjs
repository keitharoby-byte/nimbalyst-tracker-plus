import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  PythonBridge,
  readerRequestDeadlineMs,
} from '../src/pythonBridge.ts';

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

async function bridgeFixture(startupDelayMs = 0) {
  const extensionPath = await mkdtemp(path.join(tmpdir(), 'tracker-plus-bridge-'));
  const readerPath = path.join(extensionPath, 'dist', 'reader');
  await mkdir(readerPath, { recursive: true });
  const server = Buffer.from(`
import json
import sys
import time

time.sleep(${startupDelayMs} / 1000)
for line in sys.stdin:
    request = json.loads(line)
    params = request.get("params", {})
    time.sleep(params.get("delayMs", 0) / 1000)
    response = {
        "id": request["id"],
        "ok": True,
        "result": {
            "limit": params.get("limit"),
            "page": {
                "nextCursor": params.get("nextCursor"),
                "continuationRequired": params.get("nextCursor") is not None,
                "responseTruncated": bool(params.get("nextCursor")),
            },
        },
    }
    print(json.dumps(response, separators=(",", ":")), flush=True)
`);
  const files = {};
  for (const file of REQUIRED_FILES) {
    const content = file === 'server.py' ? server : Buffer.from(`fixture:${file}`);
    await writeFile(path.join(readerPath, file), content);
    files[file] = hash(content);
  }
  const identity = {
    extensionVersion: '9.9.9',
    adapterVersion: 13,
    registryVersion: 6,
    files,
  };
  const manifest = {
    formatVersion: 1,
    generationId: hash(JSON.stringify(identity)),
    ...identity,
  };
  await writeFile(
    path.join(readerPath, 'bundle-manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );
  return { extensionPath, manifest };
}

test('query deadlines scale across supported page limits', () => {
  assert.equal(readerRequestDeadlineMs('query_items', { limit: 60 }), 15_000);
  assert.equal(readerRequestDeadlineMs('query_items', { limit: 100 }), 17_000);
  assert.equal(readerRequestDeadlineMs('query_items', { limit: 200 }), 22_000);
  assert.ok(
    readerRequestDeadlineMs('traverse_graph', { limits: { maxNodes: 500, maxEdges: 1_000 } })
      > readerRequestDeadlineMs('query_items', { limit: 200 }),
  );
});

test('cold and concurrent supported queries complete with cursor-safe responses', async () => {
  const fixture = await bridgeFixture(40);
  const bridge = new PythonBridge(fixture.extensionPath, () => undefined, {
    deadlineFor: () => 3_000,
  });
  try {
    const results = await Promise.all([60, 100, 200].map((limit, index) =>
      bridge.request('query_items', {
        limit,
        delayMs: 20,
        nextCursor: index === 2 ? 'opaque-cursor' : null,
      })));
    assert.deepEqual(results.map((result) => result.limit), [60, 100, 200]);
    assert.equal(results[2].page.nextCursor, 'opaque-cursor');
    assert.equal(results[2].page.continuationRequired, true);
    assert.equal(results[2].page.responseTruncated, true);
  } finally {
    await bridge.stop();
    await rm(fixture.extensionPath, { recursive: true, force: true });
  }
});

test('forced timeout is structured and the immediate next request succeeds', async () => {
  const fixture = await bridgeFixture();
  const bridge = new PythonBridge(fixture.extensionPath, () => undefined, {
    deadlineFor: (_method, params) => params.delayMs ? 30 : 3_000,
  });
  try {
    await assert.rejects(
      bridge.request('query_items', { limit: 60, delayMs: 120 }),
      (error) => {
        assert.equal(error.code, 'READER_TIMEOUT');
        assert.equal(error.details.method, 'query_items');
        assert.equal(error.details.configuredDeadlineMs, 30);
        assert.equal(error.details.elapsedPhase, 'cold-start-and-execution');
        assert.equal(error.details.attempt, 1);
        assert.equal(error.details.verifiedGeneration, fixture.manifest.generationId);
        return true;
      },
    );

    const next = await bridge.request('query_items', { limit: 60 });
    assert.equal(next.limit, 60);
    assert.equal(next.page.continuationRequired, false);
  } finally {
    await bridge.stop();
    await rm(fixture.extensionPath, { recursive: true, force: true });
  }
});
