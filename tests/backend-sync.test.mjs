import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeTimelineSelector,
  selectorSnapshotFailure,
} from '../src/timeline/selector.ts';
import { prepareTimelineSync } from '../src/timeline/sync.ts';

test('sync preserves curated metadata and reports warning-only validation and deterministic deltas', () => {
  const existing = {
    title: 'Alpha launch control room',
    view: { mode: 'graph', zoom: 'month', showUnscheduled: false, compactRows: false, fitToWidth: false, summaryRows: true },
    filters: { completionStates: ['active'], scheduleHealth: ['at-risk'], launch: 'ALPHA' },
    snapshot: {
      generationId: 'generation-before',
      items: [{ id: 'keep' }, { id: 'remove' }],
      milestones: [{ id: 'milestone-old' }],
      relationships: [{ id: 'edge-remove' }],
    },
  };
  const incoming = {
    generatedAt: '2026-07-17T12:00:00Z',
    items: [{ id: 'add' }, { id: 'keep' }],
    milestones: [{ id: 'milestone-one' }, { id: 'milestone-two' }],
    relationships: [{ id: 'edge-add' }],
    validation: [
      { code: 'standalone-seed', severity: 'info' },
      { code: 'schedule-warning', severity: 'warning' },
      { code: 'schedule-warning', severity: 'warning' },
    ],
  };

  const prepared = prepareTimelineSync(existing, incoming, { includeUnscheduled: true }, 'generation-after');

  assert.equal(prepared.document.title, existing.title);
  assert.deepEqual(prepared.document.view, existing.view);
  assert.deepEqual(prepared.document.filters, {
    completionStates: ['active'],
    scheduleHealth: ['at-risk'],
    launch: 'ALPHA',
    includeUnscheduled: true,
  });
  assert.deepEqual(prepared.validation, {
    state: 'warn',
    total: 3,
    bySeverity: { error: 0, warning: 2, info: 1 },
    byCode: { 'schedule-warning': 2, 'standalone-seed': 1 },
  });
  assert.deepEqual(prepared.delta, {
    priorGenerationId: 'generation-before',
    currentGenerationId: 'generation-after',
    addedNodeIds: ['add'],
    removedNodeIds: ['remove'],
    addedRelationshipIds: ['edge-add'],
    removedRelationshipIds: ['edge-remove'],
    milestoneCount: { prior: 1, current: 2, change: 1 },
  });
});

test('sync reports hard errors and keeps no-selector behavior for a new document', () => {
  const prepared = prepareTimelineSync({}, {
    generatedAt: '2026-07-17T12:00:00Z',
    items: [],
    milestones: [],
    relationships: [],
    validation: [{ code: 'orphan-endpoint', severity: 'error' }],
  }, { includeUnscheduled: false }, 'generation-one');

  assert.equal(prepared.document.title, 'Tracker Timeline');
  assert.deepEqual(prepared.document.filters, { includeUnscheduled: false });
  assert.equal(prepared.validation.state, 'fail');
  assert.deepEqual(prepared.validation.bySeverity, { error: 1, warning: 0, info: 0 });
  assert.equal(prepared.delta.priorGenerationId, null);
});

test('tag selector validation normalizes deterministically and rejects ambiguous input', () => {
  const selected = normalizeTimelineSelector({
    launchTags: [' Demo-Launch ', 'ALPHA-LAUNCH'],
  });
  assert.deepEqual(selected, {
    launchTags: ['alpha-launch', 'demo-launch'],
  });

  assert.throws(
    () => normalizeTimelineSelector({ launchTags: ['Alpha-Launch', ' alpha-launch '] }),
    /unique after normalization/,
  );
  assert.throws(
    () => normalizeTimelineSelector({ launchTags: [] }),
    /1 through 8/,
  );
});

test('tag selector completeness guard rejects truncation and error validation', () => {
  assert.equal(selectorSnapshotFailure({
    page: { queryTruncated: false, responseTruncated: false },
    validation: [{ severity: 'warning' }],
  }), null);
  assert.deepEqual(selectorSnapshotFailure({
    page: { queryTruncated: true },
    validation: [],
  }), {
    code: 'RESULT_TRUNCATED',
    message: 'The selected timeline was incomplete, so the destination was not replaced.',
  });
  assert.equal(selectorSnapshotFailure({
    page: {},
    validation: [{ severity: 'error' }],
  })?.code, 'VALIDATION_FAILED');
});
