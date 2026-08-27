import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { TOOL_NAMES, TOOL_NAMES_BY_FAMILY } from '../src/backendFamilies.ts';
import {
  CUSTOM_FIELDS_DEPTH_PROPERTY,
  helpedErrorPayload,
} from '../src/toolUsage.ts';

const manifest = JSON.parse(await readFile(new URL('../manifest.json', import.meta.url), 'utf8'));
const modules = Object.fromEntries(
  manifest.contributions.backendModules.map((module) => [module.id, module]),
);

test('backend families are disjoint and cover all six stable tools', () => {
  const registered = [
    ...TOOL_NAMES_BY_FAMILY.read,
    ...TOOL_NAMES_BY_FAMILY.projection,
  ];

  assert.equal(TOOL_NAMES_BY_FAMILY.read.length, 4);
  assert.equal(TOOL_NAMES_BY_FAMILY.projection.length, 2);
  assert.equal(new Set(registered).size, 6);
  assert.deepEqual(new Set(registered), new Set(TOOL_NAMES));
  assert.ok(TOOL_NAMES_BY_FAMILY.read.includes('native_tracker_query'));
  assert.ok(TOOL_NAMES_BY_FAMILY.read.includes('native_tracker_traverse'));
});

test('manifest packages the read and projection families as separate modules', () => {
  assert.equal(manifest.contributions.backendModules.length, 2);
  assert.equal(modules['native-tracker-comments-backend'].entry, 'dist/backend-read.js');
  assert.deepEqual(modules['native-tracker-comments-backend'].permissions, ['mcp-server-register']);
  assert.equal(modules['native-tracker-projection-backend'].entry, 'dist/backend-projection.js');
  assert.deepEqual(
    modules['native-tracker-projection-backend'].permissions,
    ['mcp-server-register', 'workspace-files'],
  );
});

test('all tool parameter errors include compact tool-specific usage', async () => {
  for (const toolName of TOOL_NAMES) {
    const error = helpedErrorPayload({
      code: 'INVALID_PARAMS',
      message: 'The parameters are incomplete or conflicting.',
    }, toolName);
    assert.equal(error.details.usage.tool, toolName);
    assert.ok(error.details.usage.example);
    assert.ok(error.details.usage.constraints);
  }
});

test('timeline, report, query, and traversal schemas expose bounded legacy depth', async () => {
  assert.deepEqual(CUSTOM_FIELDS_DEPTH_PROPERTY, {
    type: 'integer',
    minimum: 1,
    maximum: 512,
    default: 128,
    description: 'Maximum legacy customFields envelopes to unwrap per item. Raise for deeply nested historical timelines; the hard cap remains 512.',
  });
});
