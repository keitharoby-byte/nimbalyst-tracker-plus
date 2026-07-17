import assert from 'node:assert/strict';
import test from 'node:test';

import {
  activeUnscheduledItems,
  derivedMilestoneProgress,
  effectiveDeliverableProgress,
  itemMatchesFilters,
  launchFilterSelection,
  milestoneSummaries,
  orderByPrimaryMilestone,
  parseTimelineDocument,
  pullRequestReference,
  trackerReferenceHref,
} from '../src/timeline/model.ts';

test('completion and schedule filters compose and pull request references stay safe', () => {
  const document = parseTimelineDocument(JSON.stringify({
    version: 2,
    title: 'Filter fixture',
    filters: {
      completionStates: ['active'],
      scheduleHealth: ['on-track', 'at-risk'],
    },
    snapshot: {
      items: [
        { id: 'active-track', primaryType: 'task', title: 'Active on track', status: 'in-progress', scheduleHealth: 'on-track' },
        { id: 'active-late', primaryType: 'task', title: 'Active late', status: 'in-progress', scheduleHealth: 'late' },
        { id: 'done-track', primaryType: 'task', title: 'Done on track', status: 'done', scheduleHealth: 'on-track' },
        { id: 'pr-item', primaryType: 'task', title: 'PR-backed work', status: 'in-review', scheduleHealth: 'at-risk', pullRequestUrl: 'https://github.com/example/repo/pull/42' },
      ],
    },
  }));

  assert.deepEqual(
    document.snapshot.items.filter((item) => itemMatchesFilters(item, document.filters)).map((item) => item.id),
    ['active-track', 'pr-item'],
  );
  assert.deepEqual(pullRequestReference(document.snapshot.items[3]), {
    number: 42,
    url: 'https://github.com/example/repo/pull/42',
  });
  assert.deepEqual(pullRequestReference({ ...document.snapshot.items[3], pullRequestNumber: 7, pullRequestUrl: 'javascript:alert(1)' }), {
    number: 7,
    url: null,
  });
  assert.equal(
    trackerReferenceHref({ ...document.snapshot.items[0], issueKey: 'NIM 1/#' }),
    'nimbalyst://NIM%201%2F%23',
  );

  const legacy = parseTimelineDocument(JSON.stringify({ version: 2, snapshot: { items: [] } }));
  assert.deepEqual(legacy.filters.completionStates, ['active', 'complete']);
  assert.deepEqual(legacy.filters.scheduleHealth, ['on-track', 'at-risk', 'late']);
});

test('launch filter keeps explicit members and marks one-hop context as boundary', () => {
  const document = parseTimelineDocument(JSON.stringify({
    version: 2,
    filters: { launch: 'FFP-1' },
    snapshot: {
      items: [
        { id: 'launch', issueKey: 'LAUNCH-1', launchKey: 'FFP-1', primaryType: 'launch', title: 'Launch' },
        { id: 'member', primaryType: 'task', title: 'Member' },
        { id: 'boundary', primaryType: 'milestone', title: 'Prior' },
        { id: 'unrelated', primaryType: 'task', title: 'Other' },
      ],
      relationships: [
        { id: 'membership', sourceId: 'member', targetId: 'launch', relationshipType: 'part-of-launch', state: 'active', scopeRole: 'core' },
        { id: 'dependency', sourceId: 'member', targetId: 'boundary', relationshipType: 'depends-on', state: 'active' },
      ],
    },
  }));
  const selection = launchFilterSelection(document.snapshot, document.filters.launch);
  assert.deepEqual([...selection.itemIds].sort(), ['boundary', 'launch', 'member']);
  assert.deepEqual([...selection.memberIds], ['member']);
  assert.deepEqual([...selection.boundaryIds], ['boundary']);
});

test('milestone progress is derived from primary deliverables and ignores the stored milestone value', () => {
  const deliverables = [
    { id: 'd1', primaryType: 'task', typeTags: ['task'], title: 'One', workflow: 'in-review', progress: 80 },
    { id: 'd2', primaryType: 'task', typeTags: ['task'], title: 'Two', workflow: 'in-review', progress: 90 },
    { id: 'd3', primaryType: 'task', typeTags: ['task'], title: 'Three', workflow: 'in-review', progress: 98 },
    { id: 'd4', primaryType: 'task', typeTags: ['task'], title: 'Four', workflow: 'in-progress', progress: 85 },
  ];
  const document = parseTimelineDocument(JSON.stringify({
    version: 2,
    title: 'Derived progress fixture',
    snapshot: {
      items: [
        { id: 'milestone', primaryType: 'milestone', title: 'MR-R', workflow: 'in-progress', progress: 30 },
        ...deliverables,
      ],
      relationships: deliverables.map((item, index) => ({
        id: `link-${index}`,
        sourceId: item.id,
        targetId: 'milestone',
        relationshipType: 'contributes-to',
        state: 'active',
        primaryContribution: true,
      })),
    },
  }));

  const summary = milestoneSummaries(document.snapshot)[0];
  assert.equal(summary.progress, 88);
  assert.equal(summary.complete, 0);
  assert.equal(summary.deliverables.length, 4);
  assert.equal(document.snapshot.milestones[0].progress, 88);
  assert.equal(effectiveDeliverableProgress({ ...deliverables[0], workflow: 'done', progress: null }), 100);
  assert.equal(effectiveDeliverableProgress({ ...deliverables[0], progress: null }), 0);
  assert.equal(derivedMilestoneProgress([]), 0);
});

test('native projection relationships survive parsing and hydrate milestone dimensions', () => {
  const document = parseTimelineDocument(JSON.stringify({
    version: 2,
    title: 'Native relationship fixture',
    snapshot: {
      items: [
        {
          id: 'milestone-one',
          issueKey: 'NIM-10',
          primaryType: 'milestone',
          title: 'Alpha milestone',
          status: 'in-progress',
          forecastDate: '2026-08-01',
        },
        {
          id: 'task-active',
          issueKey: 'NIM-11',
          primaryType: 'task',
          title: 'Active executable work',
          status: 'in-review',
        },
        {
          id: 'task-done',
          issueKey: 'NIM-12',
          primaryType: 'task',
          title: 'Completed evidence',
          status: 'done',
        },
        {
          id: 'plan-reference',
          issueKey: 'NIM-13',
          primaryType: 'plan',
          title: 'Reference plan',
          status: 'in-review',
        },
      ],
      relationships: [
        {
          id: 'link-primary',
          sourceId: 'task-active',
          targetId: 'milestone-one',
          kind: 'contributes-to',
          status: 'active',
          directedness: 'directed',
          contributionRole: 'primary',
          entryEvidence: [{ itemId: 'task-done' }],
          legacy: false,
        },
        {
          id: 'link-secondary',
          sourceId: 'plan-reference',
          targetId: 'milestone-one',
          kind: 'contributes-to',
          status: 'active',
          contributionRole: 'secondary',
          legacy: false,
        },
        {
          id: 'link-evidence',
          sourceId: 'task-done',
          targetId: 'task-active',
          kind: 'evidences',
          status: 'active',
          legacy: false,
        },
      ],
    },
  }));

  assert.equal(document.snapshot.relationships.length, 3);
  const primaryEdge = document.snapshot.relationships[0];
  assert.equal(primaryEdge.relationshipType, 'contributes-to');
  assert.equal(primaryEdge.state, 'active');
  assert.equal(primaryEdge.primaryContribution, true);
  assert.deepEqual(primaryEdge.entryEvidenceIds, ['task-done']);

  const activeTask = document.snapshot.items.find((item) => item.id === 'task-active');
  assert.equal(activeTask.primaryMilestoneId, 'milestone-one');
  assert.equal(activeTask.launchScoped, true);
  assert.equal(document.snapshot.items.filter((item) => item.launchScoped).length, 4);

  assert.deepEqual(
    activeUnscheduledItems(document.snapshot.items).map((item) => item.id),
    ['task-active'],
  );
  const milestone = milestoneSummaries(document.snapshot, new Date('2026-07-15T00:00:00Z'))[0];
  assert.deepEqual(milestone.deliverables.map((item) => item.id), ['task-active']);

  const [milestoneItem, activeItem, ...ungrouped] = orderByPrimaryMilestone(
    [
      document.snapshot.items.find((item) => item.id === 'plan-reference'),
      document.snapshot.items.find((item) => item.id === 'task-done'),
      document.snapshot.items.find((item) => item.id === 'milestone-one'),
      document.snapshot.items.find((item) => item.id === 'task-active'),
    ].filter(Boolean),
    document.snapshot.relationships,
  );
  assert.equal(milestoneItem.id, 'milestone-one');
  assert.equal(activeItem.id, 'task-active');
  assert.deepEqual(ungrouped.map((item) => item.id), ['plan-reference', 'task-done']);

  const nestedMilestone = {
    ...document.snapshot.items.find((item) => item.id === 'milestone-one'),
    id: 'milestone-child',
    issueKey: 'NIM-14',
    title: 'Nested milestone',
  };
  const nestedOrder = orderByPrimaryMilestone(
    [...document.snapshot.items, nestedMilestone],
    [
      ...document.snapshot.relationships,
      {
        ...primaryEdge,
        id: 'link-nested-milestone',
        sourceId: 'milestone-child',
        targetId: 'milestone-one',
      },
    ],
  );
  assert.equal(nestedOrder.filter((item) => item.id === 'milestone-child').length, 1);
  assert.ok(nestedOrder.findIndex((item) => item.id === 'milestone-child') > nestedOrder.findIndex((item) => item.id === 'milestone-one'));
});
