import type {
  CompletionState,
  DependencyMode,
  ExecutionConstraint,
  MilestoneSummary,
  RelationshipHardness,
  RelationshipState,
  RelationshipType,
  RiskLevel,
  ScheduleHealth,
  TimelineDocument,
  TimelineItem,
  TimelineRelationship,
  TimelineSnapshot,
  ValidationFinding,
} from './types';

const DAY_MS = 86_400_000;
const COMPLETE_WORKFLOWS = new Set(['done', 'completed', 'achieved', 'closed', 'shipped', 'implemented']);
const COMPLETION_STATES = new Set<CompletionState>(['active', 'complete']);
const EXECUTABLE_TYPES = new Set([
  'task',
  'timeline-item',
  'devops-item',
  'automation',
  'mr',
  'merge-request',
  'pull-request',
  'change-request',
]);
const RELATIONSHIP_TYPES = new Set<RelationshipType>([
  'part-of-launch',
  'in-collection',
  'has-item',
  'governs',
  'depends-on',
  'contributes-to',
  'reviews',
  'evidences',
  'precedes',
  'enables',
  'coordinates-with',
  'implements',
  'related',
]);
const SCHEDULE_HEALTH = new Set<ScheduleHealth>(['on-track', 'at-risk', 'late']);
const EXECUTION_CONSTRAINTS = new Set<ExecutionConstraint>(['clear', 'waiting', 'blocked', 'paused']);
const RISK_LEVELS = new Set<RiskLevel>(['low', 'medium', 'high', 'critical']);
const RELATIONSHIP_STATES = new Set<RelationshipState>([
  'active',
  'cleared',
  'blocked',
  'retired',
  'superseded',
  'unknown',
]);
const DEPENDENCY_MODES = new Set<DependencyMode>(['finish-to-start', 'start-to-start', 'finish-to-finish', 'start-to-finish']);
const HARDNESS_LEVELS = new Set<RelationshipHardness>(['hard-serial', 'shared-resource', 'soft-coordination']);

export const RELATIONSHIP_LABELS: Record<RelationshipType, { forward: string; inverse: string }> = {
  'part-of-launch': { forward: 'Part of launch', inverse: 'Contains launch member' },
  'in-collection': { forward: 'In collection', inverse: 'Has item' },
  'has-item': { forward: 'Has item', inverse: 'In collection' },
  governs: { forward: 'Governs', inverse: 'Is governed by' },
  'depends-on': { forward: 'Depends on', inverse: 'Is predecessor of' },
  'contributes-to': { forward: 'Contributes to', inverse: 'Receives contribution from' },
  reviews: { forward: 'Reviews', inverse: 'Is reviewed by' },
  evidences: { forward: 'Evidences', inverse: 'Is evidenced by' },
  precedes: { forward: 'Precedes', inverse: 'Follows' },
  enables: { forward: 'Enables', inverse: 'Is enabled by' },
  'coordinates-with': { forward: 'Coordinates with', inverse: 'Coordinates with' },
  implements: { forward: 'Implements', inverse: 'Is implemented by' },
  related: { forward: 'Related to', inverse: 'Related to' },
};

export function emptyTimelineDocument(): TimelineDocument {
  return {
    version: 2,
    title: 'Tracker Timeline',
    view: { mode: 'timeline', zoom: 'week', showUnscheduled: true, compactRows: true, fitToWidth: true, summaryRows: false },
    filters: {
      includeUnscheduled: true,
      completionStates: ['active', 'complete'],
      scheduleHealth: ['on-track', 'at-risk', 'late'],
    },
    snapshot: {
      generatedAt: null,
      items: [],
      milestones: [],
      relationships: [],
      validation: [],
      criticalPath: { durationDays: 0, itemIds: [], cycleItemIds: [] },
      page: { returned: 0 },
      source: {},
    },
  };
}

export function parseTimelineDocument(raw: string): TimelineDocument {
  if (!raw.trim()) return emptyTimelineDocument();
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error('The timeline file is not valid JSON.');
  }
  if (!isRecord(value)) throw new Error('The timeline document must be a JSON object.');
  const fallback = emptyTimelineDocument();
  const view = isRecord(value.view) ? value.view : {};
  const filters = isRecord(value.filters) ? value.filters : {};
  const rawSnapshot = isRecord(value.snapshot) ? value.snapshot : {};
  const items = Array.isArray(rawSnapshot.items)
    ? rawSnapshot.items.map(parseItem).filter((item): item is TimelineItem => item !== null)
    : [];
  const itemIds = new Set(items.map((item) => item.id));
  const relationships = Array.isArray(rawSnapshot.relationships)
    ? rawSnapshot.relationships
        .map(parseRelationship)
        .filter((edge): edge is TimelineRelationship => edge !== null)
    : [];
  for (const edge of relationships) edge.targetInSnapshot = itemIds.has(edge.targetId);
  hydrateRelationshipDimensions(items, relationships);
  applyDerivedMilestoneProgress(items, relationships);
  const validation = Array.isArray(rawSnapshot.validation)
    ? rawSnapshot.validation.map(parseFinding).filter((finding): finding is ValidationFinding => finding !== null)
    : [];
  const rawCriticalPath = isRecord(rawSnapshot.criticalPath) ? rawSnapshot.criticalPath : {};
  const snapshot: TimelineSnapshot = {
    ...(typeof rawSnapshot.generationId === 'string' ? { generationId: rawSnapshot.generationId } : {}),
    generatedAt: typeof rawSnapshot.generatedAt === 'string' ? rawSnapshot.generatedAt : null,
    items,
    milestones: items.filter((item) => item.primaryType === 'milestone' && !item.boundary),
    relationships,
    validation,
    criticalPath: {
      durationDays: finiteNumber(rawCriticalPath.durationDays) ?? 0,
      itemIds: stringArray(rawCriticalPath.itemIds),
      cycleItemIds: stringArray(rawCriticalPath.cycleItemIds),
    },
    page: {
      returned: items.length,
      ...(isRecord(rawSnapshot.page) && typeof rawSnapshot.page.maxItems === 'number'
        ? { maxItems: rawSnapshot.page.maxItems }
        : {}),
      ...(isRecord(rawSnapshot.page) && typeof rawSnapshot.page.queryTruncated === 'boolean'
        ? { queryTruncated: rawSnapshot.page.queryTruncated }
        : {}),
      ...(isRecord(rawSnapshot.page) && typeof rawSnapshot.page.responseTruncated === 'boolean'
        ? { responseTruncated: rawSnapshot.page.responseTruncated }
        : {}),
    },
    source: isRecord(rawSnapshot.source) ? rawSnapshot.source : {},
  };
  return {
    version: 2,
    title: typeof value.title === 'string' && value.title.trim() ? value.title.trim() : fallback.title,
    view: {
      mode: view.mode === 'graph' || view.mode === 'report' ? view.mode : 'timeline',
      zoom: view.zoom === 'day' || view.zoom === 'month' ? view.zoom : 'week',
      showUnscheduled: view.showUnscheduled !== false,
      compactRows: view.compactRows !== false,
      fitToWidth: view.fitToWidth !== false,
      summaryRows: view.summaryRows === true,
    },
    filters: {
      includeUnscheduled: filters.includeUnscheduled !== false,
      completionStates: enumArray(filters.completionStates, COMPLETION_STATES, ['active', 'complete']),
      scheduleHealth: enumArray(filters.scheduleHealth, SCHEDULE_HEALTH, ['on-track', 'at-risk', 'late']),
      ...(typeof filters.from === 'string' ? { from: filters.from } : {}),
      ...(typeof filters.to === 'string' ? { to: filters.to } : {}),
      ...(typeof filters.launch === 'string' ? { launch: filters.launch } : {}),
    },
    snapshot,
  };
}

export function dayNumber(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(`${value.slice(0, 10)}T00:00:00Z`);
  return Number.isFinite(parsed) ? Math.floor(parsed / DAY_MS) : null;
}

export function dayToIso(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

export function deriveTimelineRange(items: TimelineItem[]): { start: number; end: number } {
  const dates = items.flatMap((item) => [dayNumber(item.startDate), dayNumber(item.dueDate), dayNumber(item.forecastDate)])
    .filter((value): value is number => value !== null);
  const today = Math.floor(Date.now() / DAY_MS);
  if (!dates.length) return { start: today - 14, end: today + 84 };
  return { start: Math.min(...dates) - 7, end: Math.max(...dates) + 14 };
}

export function isComplete(item: TimelineItem): boolean {
  return COMPLETE_WORKFLOWS.has(String(item.workflow ?? '').toLowerCase());
}

export function itemMatchesFilters(item: TimelineItem, filters: TimelineDocument['filters']): boolean {
  const completionState: CompletionState = isComplete(item) ? 'complete' : 'active';
  return filters.completionStates.includes(completionState)
    && filters.scheduleHealth.includes(item.scheduleHealth);
}

export function launchFilterSelection(
  snapshot: TimelineSnapshot,
  launch: string | undefined,
): { itemIds: Set<string>; memberIds: Set<string>; boundaryIds: Set<string>; rootId: string | null } {
  if (!launch) {
    return { itemIds: new Set(snapshot.items.map((item) => item.id)), memberIds: new Set(), boundaryIds: new Set(), rootId: null };
  }
  const root = snapshot.items.find((item) => item.primaryType === 'launch' && [item.id, item.issueKey, item.launchKey].includes(launch));
  if (!root) return { itemIds: new Set(), memberIds: new Set(), boundaryIds: new Set(), rootId: null };
  const memberships = snapshot.relationships.filter((edge) => edge.relationshipType === 'part-of-launch' && edge.state === 'active' && edge.targetId === root.id);
  const memberIds = new Set(memberships.map((edge) => edge.sourceId));
  const scopeIds = new Set([root.id, ...memberIds]);
  const boundaryIds = new Set<string>();
  for (const edge of snapshot.relationships) {
    if (edge.state !== 'active' || edge.relationshipType === 'part-of-launch') continue;
    if (scopeIds.has(edge.sourceId) && !scopeIds.has(edge.targetId)) boundaryIds.add(edge.targetId);
    if (scopeIds.has(edge.targetId) && !scopeIds.has(edge.sourceId)) boundaryIds.add(edge.sourceId);
  }
  return { itemIds: new Set([...scopeIds, ...boundaryIds]), memberIds, boundaryIds, rootId: root.id };
}

export function pullRequestReference(item: TimelineItem): { number: number | null; url: string | null } {
  const safeUrl = safeHttpsUrl(item.pullRequestUrl);
  const number = positiveInteger(item.pullRequestNumber) ?? pullRequestNumberFromUrl(safeUrl);
  return { number, url: safeUrl };
}

export function deliveryReferences(item: TimelineItem): Array<{ repository: string | null; number: number | null; url: string | null }> {
  const derived = item.deliveryAttribution?.references ?? [];
  if (derived.length) {
    return derived.map((reference) => ({
      repository: reference.repository,
      number: reference.number,
      url: safeHttpsUrl(reference.url),
    }));
  }
  const legacy = pullRequestReference(item);
  return legacy.number != null || legacy.url != null
    ? [{ repository: null, ...legacy }]
    : [];
}

export function effectiveDeliverableProgress(item: TimelineItem): number {
  if (isComplete(item)) return 100;
  if (typeof item.progress !== 'number' || !Number.isFinite(item.progress)) return 0;
  return Math.max(0, Math.min(100, item.progress));
}

export function derivedMilestoneProgress(deliverables: TimelineItem[]): number {
  if (!deliverables.length) return 0;
  const total = deliverables.reduce((sum, item) => sum + effectiveDeliverableProgress(item), 0);
  return Math.round(total / deliverables.length);
}

function primaryDeliverables(
  items: TimelineItem[],
  relationships: TimelineRelationship[],
  milestoneId: string,
): TimelineItem[] {
  const itemsById = new Map(items.map((item) => [item.id, item]));
  const deliverables = new Map<string, TimelineItem>();
  for (const edge of relationships) {
    if (
      edge.relationshipType !== 'contributes-to'
      || edge.targetId !== milestoneId
      || edge.state !== 'active'
      || !edge.primaryContribution
    ) continue;
    const item = itemsById.get(edge.sourceId);
    if (item && !item.boundary) deliverables.set(item.id, item);
  }
  return [...deliverables.values()];
}

function applyDerivedMilestoneProgress(
  items: TimelineItem[],
  relationships: TimelineRelationship[],
): void {
  for (const milestone of items.filter((item) => item.primaryType === 'milestone' && !item.boundary)) {
    milestone.progress = derivedMilestoneProgress(primaryDeliverables(items, relationships, milestone.id));
  }
}

export function isActiveExecutableItem(item: TimelineItem): boolean {
  if (isComplete(item) || item.primaryType === 'milestone') return false;
  const types = new Set([item.primaryType, ...item.typeTags].map((value) => value.toLowerCase()));
  return [...types].some((value) => EXECUTABLE_TYPES.has(value));
}

export function activeUnscheduledItems(items: TimelineItem[]): TimelineItem[] {
  return items.filter((item) =>
    isActiveExecutableItem(item)
    && !item.startDate
    && !item.dueDate
    && !item.forecastDate);
}

export function primaryMilestoneParentIds(
  items: TimelineItem[],
  relationships: TimelineRelationship[],
): Map<string, string> {
  const visibleIds = new Set(items.map((item) => item.id));
  const milestoneIds = new Set(
    items.filter((item) => item.primaryType === 'milestone').map((item) => item.id),
  );
  const primaryParents = new Map<string, Set<string>>();
  for (const edge of relationships) {
    if (
      edge.state !== 'active'
      || edge.relationshipType !== 'contributes-to'
      || !edge.primaryContribution
      || !visibleIds.has(edge.sourceId)
      || !milestoneIds.has(edge.targetId)
    ) continue;
    const parents = primaryParents.get(edge.sourceId) ?? new Set<string>();
    parents.add(edge.targetId);
    primaryParents.set(edge.sourceId, parents);
  }

  const result = new Map<string, string>();
  for (const [itemId, parents] of primaryParents) {
    if (parents.size === 1) result.set(itemId, [...parents][0]!);
  }
  return result;
}

export function orderByPrimaryMilestone(
  items: TimelineItem[],
  relationships: TimelineRelationship[],
): TimelineItem[] {
  const itemById = new Map(items.map((item) => [item.id, item]));
  const parentByItem = primaryMilestoneParentIds(items, relationships);

  const childrenByMilestone = new Map<string, TimelineItem[]>();
  for (const item of items) {
    const parentId = parentByItem.get(item.id);
    if (!parentId) continue;
    const children = childrenByMilestone.get(parentId) ?? [];
    children.push(item);
    childrenByMilestone.set(parentId, children);
  }

  const placedIds = new Set<string>();
  const result: TimelineItem[] = [];
  const appendBranch = (item: TimelineItem): void => {
    if (placedIds.has(item.id)) return;
    placedIds.add(item.id);
    result.push(item);
    if (item.primaryType !== 'milestone') return;
    for (const child of childrenByMilestone.get(item.id) ?? []) appendBranch(child);
  };

  const milestones = items.filter((item) => item.primaryType === 'milestone');
  for (const milestone of milestones) {
    const parentId = parentByItem.get(milestone.id);
    if (!parentId || !itemById.has(parentId)) appendBranch(milestone);
  }
  // A second milestone pass safely emits any cyclic or otherwise malformed
  // hierarchy once; validation can report the topology without duplicating rows.
  for (const milestone of milestones) appendBranch(milestone);
  for (const item of items) appendBranch(item);
  return result;
}

export function itemReference(item: TimelineItem): string {
  return item.issueKey || item.id;
}

export function trackerReferenceHref(item: TimelineItem): string {
  return `nimbalyst://${encodeURIComponent(itemReference(item))}`;
}

export function relationshipLabel(edge: TimelineRelationship, inverse = false): string {
  return inverse ? RELATIONSHIP_LABELS[edge.relationshipType].inverse : RELATIONSHIP_LABELS[edge.relationshipType].forward;
}

export function milestoneSummaries(snapshot: TimelineSnapshot, now = new Date()): MilestoneSummary[] {
  const today = Math.floor(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) / DAY_MS);
  return snapshot.milestones.map((milestone) => {
    const deliverables = primaryDeliverables(snapshot.items, snapshot.relationships, milestone.id);
    const complete = deliverables.filter(isComplete).length;
    const overdue = deliverables.filter((item) => {
      const due = dayNumber(item.forecastDate ?? item.dueDate);
      return due !== null && due < today && !isComplete(item);
    });
    const relevant = new Set([milestone.id, ...deliverables.map((item) => item.id)]);
    const activeDependencies = snapshot.relationships.filter((edge) =>
      edge.relationshipType === 'depends-on'
      && edge.state === 'active'
      && relevant.has(edge.sourceId));
    const relevantItems = [milestone, ...deliverables];
    const blockedItems = relevantItems.filter((item) => item.executionConstraint === 'blocked');
    const waitingItems = relevantItems.filter((item) => item.executionConstraint === 'waiting');
    const progress = derivedMilestoneProgress(deliverables);
    const scheduleHealth: MilestoneSummary['scheduleHealth'] = isComplete(milestone)
      ? 'achieved'
      : worstScheduleHealth(relevantItems.map((item) => item.scheduleHealth));
    const riskLevel = worstRisk(relevantItems.map((item) => item.riskLevel));
    return {
      milestone,
      deliverables,
      complete,
      overdue,
      activeDependencies,
      blockedItems,
      waitingItems,
      scheduleHealth,
      progress,
      riskLevel,
    };
  });
}

function parseItem(raw: unknown): TimelineItem | null {
  if (!isRecord(raw) || typeof raw.id !== 'string' || typeof raw.title !== 'string' || typeof raw.primaryType !== 'string') return null;
  const workflow = stringValue(raw.workflow) ?? stringValue(raw.status);
  const legacyStatus = String(raw.status ?? '').toLowerCase();
  const legacyBlocked = legacyStatus === 'blocked';
  const legacyAtRisk = legacyStatus === 'at-risk';
  return {
    id: raw.id,
    issueKey: stringValue(raw.issueKey),
    primaryType: raw.primaryType,
    typeTags: stringArray(raw.typeTags).length ? stringArray(raw.typeTags) : [raw.primaryType],
    title: raw.title,
    workflow: legacyBlocked || legacyAtRisk ? 'in-progress' : workflow,
    priority: stringValue(raw.priority),
    ownerLabel: stringValue(raw.ownerLabel),
    startDate: stringValue(raw.startDate),
    dueDate: stringValue(raw.dueDate),
    forecastDate: stringValue(raw.forecastDate),
    progress: clampPercent(finiteNumber(raw.progress)),
    scheduleHealth: enumValue(raw.scheduleHealth, SCHEDULE_HEALTH) ?? (legacyAtRisk ? 'at-risk' : 'on-track'),
    scheduleHealthReasons: stringArray(raw.scheduleHealthReasons),
    executionConstraint: enumValue(raw.executionConstraint, EXECUTION_CONSTRAINTS) ?? (legacyBlocked ? 'blocked' : 'clear'),
    impact: boundedInteger(raw.impact, 1, 5),
    likelihood: boundedInteger(raw.likelihood, 1, 5),
    riskScore: finiteNumber(raw.riskScore),
    riskLevel: enumValue(raw.riskLevel, RISK_LEVELS) ?? 'low',
    riskReasons: stringArray(raw.riskReasons),
    riskDurability: stringValue(raw.riskDurability),
    recoverability: stringValue(raw.recoverability),
    evidenceConfidence: stringValue(raw.evidenceConfidence),
    technicalUncertainty: stringValue(raw.technicalUncertainty),
    capacityPressure: stringValue(raw.capacityPressure),
    gate: stringValue(raw.gate),
    launchScoped: raw.launchScoped === true || raw.launchScope === 'launch',
    launchKey: stringValue(raw.launchKey),
    launchMember: raw.launchMember === true,
    boundary: raw.boundary === true,
    primaryMilestoneId: stringValue(raw.primaryMilestoneId),
    scheduleSlackDays: finiteNumber(raw.scheduleSlackDays),
    criticalPathSlackDays: finiteNumber(raw.criticalPathSlackDays),
    durationDays: Math.max(1, Math.round(finiteNumber(raw.durationDays) ?? 1)),
    isCritical: raw.isCritical === true,
    pullRequestNumber: positiveInteger(raw.pullRequestNumber) ?? pullRequestNumberFromUrl(stringValue(raw.pullRequestUrl)),
    pullRequestUrl: stringValue(raw.pullRequestUrl),
    deliveryAttribution: parseDeliveryAttribution(raw.deliveryAttribution),
    updated: stringValue(raw.updated),
  };
}

function parseDeliveryAttribution(raw: unknown): TimelineItem['deliveryAttribution'] | undefined {
  if (!isRecord(raw)) return undefined;
  const authority = enumValue(raw.authority, new Set(['native-fields', 'cross-repo-body', 'none'] as const));
  const state = enumValue(raw.state, new Set(['attributed', 'invalid', 'unattributed'] as const));
  const receiptId = stringValue(raw.receiptId);
  if (!authority || !state || !receiptId || !Array.isArray(raw.references) || !Array.isArray(raw.validation)) return undefined;
  const references = raw.references.flatMap((value) => {
    if (!isRecord(value)) return [];
    const number = positiveInteger(value.number);
    const url = safeHttpsUrl(stringValue(value.url));
    const repository = stringValue(value.repository);
    if (number == null && url == null) return [];
    return [{ repository, number, url }];
  });
  const validation = raw.validation.flatMap((value) => {
    if (!isRecord(value)) return [];
    const code = stringValue(value.code);
    const message = stringValue(value.message);
    if (!code || !message || value.severity !== 'warning') return [];
    return [{ code, severity: 'warning' as const, message }];
  });
  const evidenceSource = enumValue(
    raw.evidenceSource,
    new Set(['collaborative-content', 'local-snapshot', 'empty'] as const),
  ) ?? null;
  return { authority, state, references, validation, evidenceSource, receiptId };
}

function parseRelationship(raw: unknown): TimelineRelationship | null {
  if (!isRecord(raw) || typeof raw.sourceId !== 'string' || typeof raw.targetId !== 'string') return null;
  const legacyKind = stringValue(raw.kind);
  const legacyMap: Record<string, RelationshipType> = {
    blocker: 'depends-on',
    'waiting-on': 'depends-on',
    related: 'related',
    source: 'evidences',
    milestone: 'contributes-to',
  };
  const relationshipType = enumValue(raw.relationshipType, RELATIONSHIP_TYPES)
    ?? enumValue(raw.kind, RELATIONSHIP_TYPES)
    ?? (legacyKind ? legacyMap[legacyKind] : undefined);
  if (!relationshipType) return null;
  const directedness = stringValue(raw.directedness);
  const state = enumValue(raw.state, RELATIONSHIP_STATES)
    ?? enumValue(raw.status, RELATIONSHIP_STATES)
    ?? 'active';
  return {
    id: stringValue(raw.id) ?? `legacy:${raw.sourceId}:${relationshipType}:${raw.targetId}`,
    issueKey: stringValue(raw.issueKey),
    sourceId: raw.sourceId,
    sourceIssueKey: stringValue(raw.sourceIssueKey),
    sourceTitle: stringValue(raw.sourceTitle),
    targetId: raw.targetId,
    targetIssueKey: stringValue(raw.targetIssueKey),
    targetTitle: stringValue(raw.targetTitle),
    targetType: stringValue(raw.targetType),
    relationshipType,
    directed: raw.directed !== false && directedness !== 'symmetric' && relationshipType !== 'related',
    state,
    dependencyMode: enumValue(raw.dependencyMode, DEPENDENCY_MODES) ?? (relationshipType === 'depends-on' ? 'finish-to-start' : null),
    hardness: enumValue(raw.hardness, HARDNESS_LEVELS),
    leadLagDays: finiteNumber(raw.leadLagDays) ?? 0,
    clearingCondition: stringValue(raw.clearingCondition),
    ownerLabel: stringValue(raw.ownerLabel),
    primaryContribution: raw.primaryContribution === true || raw.contributionRole === 'primary' || legacyKind === 'milestone',
    contributionRole: stringValue(raw.contributionRole),
    scopeRole: stringValue(raw.scopeRole),
    entryEvidenceIds: relationshipItemIds(raw.entryEvidenceIds ?? raw.entryEvidence),
    exitEvidenceIds: relationshipItemIds(raw.exitEvidenceIds ?? raw.exitEvidence),
    evidenceSourceIds: relationshipItemIds(raw.evidenceSourceIds ?? raw.evidenceSources),
    effectiveRevision: stringValue(raw.effectiveRevision),
    created: stringValue(raw.created),
    updated: stringValue(raw.updated),
    targetInSnapshot: raw.targetInSnapshot === true,
    legacy: raw.legacy !== false,
  };
}

function hydrateRelationshipDimensions(items: TimelineItem[], relationships: TimelineRelationship[]): void {
  if (!relationships.length) return;
  const itemsById = new Map(items.map((item) => [item.id, item]));
  const milestoneIds = new Set(items.filter((item) => item.primaryType === 'milestone').map((item) => item.id));
  const primaryBySource = new Map<string, Set<string>>();
  const adjacency = new Map(items.map((item) => [item.id, new Set<string>()]));

  for (const edge of relationships) {
    if (edge.state !== 'active') continue;
    if (edge.relationshipType === 'contributes-to' && edge.primaryContribution && milestoneIds.has(edge.targetId)) {
      const targets = primaryBySource.get(edge.sourceId) ?? new Set<string>();
      targets.add(edge.targetId);
      primaryBySource.set(edge.sourceId, targets);
    }
    if (itemsById.has(edge.sourceId) && itemsById.has(edge.targetId)) {
      adjacency.get(edge.sourceId)?.add(edge.targetId);
      adjacency.get(edge.targetId)?.add(edge.sourceId);
    }
  }

  const launchConnected = new Set(milestoneIds);
  const queue = [...milestoneIds].sort();
  while (queue.length) {
    const itemId = queue.shift();
    if (!itemId) continue;
    for (const relatedId of [...(adjacency.get(itemId) ?? [])].sort()) {
      if (!launchConnected.has(relatedId)) {
        launchConnected.add(relatedId);
        queue.push(relatedId);
      }
    }
  }

  for (const item of items) {
    const primaryIds = [...(primaryBySource.get(item.id) ?? [])].sort();
    item.primaryMilestoneId = primaryIds.length === 1 ? primaryIds[0] : null;
    item.launchScoped = item.launchScoped || launchConnected.has(item.id);
  }
}

function parseFinding(raw: unknown): ValidationFinding | null {
  if (!isRecord(raw) || typeof raw.code !== 'string' || typeof raw.message !== 'string') return null;
  const severity = raw.severity === 'error' || raw.severity === 'info' ? raw.severity : 'warning';
  return {
    code: raw.code,
    severity,
    message: raw.message,
    itemIds: stringArray(raw.itemIds),
    relationshipIds: stringArray(raw.relationshipIds),
  };
}

function worstScheduleHealth(values: ScheduleHealth[]): ScheduleHealth {
  if (values.includes('late')) return 'late';
  if (values.includes('at-risk')) return 'at-risk';
  return 'on-track';
}

function worstRisk(values: RiskLevel[]): RiskLevel {
  const order: RiskLevel[] = ['low', 'medium', 'high', 'critical'];
  return values.reduce((worst, value) => order.indexOf(value) > order.indexOf(worst) ? value : worst, 'low');
}

function enumValue<T extends string>(raw: unknown, allowed: Set<T>): T | undefined {
  return typeof raw === 'string' && allowed.has(raw as T) ? raw as T : undefined;
}

function enumArray<T extends string>(raw: unknown, allowed: Set<T>, fallback: T[]): T[] {
  if (!Array.isArray(raw)) return [...fallback];
  return [...new Set(raw.filter((value): value is T => typeof value === 'string' && allowed.has(value as T)))];
}

function stringValue(raw: unknown): string | null {
  return typeof raw === 'string' && raw.trim() ? raw : null;
}

function stringArray(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((value): value is string => typeof value === 'string') : [];
}

function relationshipItemIds(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const ids = raw.flatMap((value) => {
    if (typeof value === 'string') return [value];
    if (!isRecord(value)) return [];
    const itemId = stringValue(value.itemId) ?? stringValue(value.id);
    return itemId ? [itemId] : [];
  });
  return [...new Set(ids)];
}

function finiteNumber(raw: unknown): number | null {
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
}

function positiveInteger(raw: unknown): number | null {
  if (typeof raw === 'string' && /^#?\d+$/.test(raw.trim())) raw = Number(raw.trim().replace(/^#/, ''));
  return typeof raw === 'number' && Number.isInteger(raw) && raw > 0 ? raw : null;
}

function safeHttpsUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    return parsed.protocol === 'https:' ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function pullRequestNumberFromUrl(raw: string | null | undefined): number | null {
  if (!raw) return null;
  const match = raw.match(/\/pull\/(\d+)(?:[/?#]|$)/i);
  return match ? positiveInteger(match[1]) : null;
}

function boundedInteger(raw: unknown, min: number, max: number): number | null {
  const value = finiteNumber(raw);
  if (value === null) return null;
  return Math.max(min, Math.min(max, Math.round(value)));
}

function clampPercent(value: number | null): number | null {
  return value === null ? null : Math.max(0, Math.min(100, value));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}
