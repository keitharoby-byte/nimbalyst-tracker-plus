import assert from 'node:assert/strict';
import test from 'node:test';

import {
  activeUnscheduledItems,
  milestoneSummaries,
  orderByPrimaryMilestone,
  parseTimelineDocument,
} from '../src/timeline/model.ts';

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
