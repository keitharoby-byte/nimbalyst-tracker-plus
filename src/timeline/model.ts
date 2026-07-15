import type {
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
const RELATIONSHIP_TYPES = new Set<RelationshipType>([
  'depends-on',
  'contributes-to',
  'reviews',
  'evidences',
  'implements',
  'related',
]);
const SCHEDULE_HEALTH = new Set<ScheduleHealth>(['on-track', 'at-risk', 'late']);
const EXECUTION_CONSTRAINTS = new Set<ExecutionConstraint>(['clear', 'waiting', 'blocked', 'paused']);
const RISK_LEVELS = new Set<RiskLevel>(['low', 'medium', 'high', 'critical']);
const RELATIONSHIP_STATES = new Set<RelationshipState>(['active', 'cleared', 'superseded']);
const DEPENDENCY_MODES = new Set<DependencyMode>(['finish-to-start', 'start-to-start', 'finish-to-finish', 'start-to-finish']);
const HARDNESS_LEVELS = new Set<RelationshipHardness>(['hard-serial', 'shared-resource', 'soft-coordination']);

export const RELATIONSHIP_LABELS: Record<RelationshipType, { forward: string; inverse: string }> = {
  'depends-on': { forward: 'Depends on', inverse: 'Is predecessor of' },
  'contributes-to': { forward: 'Contributes to', inverse: 'Receives contribution from' },
  reviews: { forward: 'Reviews', inverse: 'Is reviewed by' },
  evidences: { forward: 'Evidences', inverse: 'Is evidenced by' },
  implements: { forward: 'Implements', inverse: 'Is implemented by' },
  related: { forward: 'Related to', inverse: 'Related to' },
};

export function emptyTimelineDocument(): TimelineDocument {
  return {
    version: 2,
    title: 'Tracker Timeline',
    view: { mode: 'timeline', zoom: 'week', showUnscheduled: true, compactRows: true, fitToWidth: true },
    filters: { includeUnscheduled: true },
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
  const validation = Array.isArray(rawSnapshot.validation)
    ? rawSnapshot.validation.map(parseFinding).filter((finding): finding is ValidationFinding => finding !== null)
    : [];
  const rawCriticalPath = isRecord(rawSnapshot.criticalPath) ? rawSnapshot.criticalPath : {};
  const snapshot: TimelineSnapshot = {
    generatedAt: typeof rawSnapshot.generatedAt === 'string' ? rawSnapshot.generatedAt : null,
    items,
    milestones: items.filter((item) => item.primaryType === 'milestone'),
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
    },
    filters: {
      includeUnscheduled: filters.includeUnscheduled !== false,
      ...(typeof filters.from === 'string' ? { from: filters.from } : {}),
      ...(typeof filters.to === 'string' ? { to: filters.to } : {}),
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

export function itemReference(item: TimelineItem): string {
  return item.issueKey || item.id;
}

export function relationshipLabel(edge: TimelineRelationship, inverse = false): string {
  return inverse ? RELATIONSHIP_LABELS[edge.relationshipType].inverse : RELATIONSHIP_LABELS[edge.relationshipType].forward;
}

export function milestoneSummaries(snapshot: TimelineSnapshot, now = new Date()): MilestoneSummary[] {
  const itemsById = new Map(snapshot.items.map((item) => [item.id, item]));
  const today = Math.floor(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) / DAY_MS);
  return snapshot.milestones.map((milestone) => {
    const contributionEdges = snapshot.relationships.filter((edge) =>
      edge.relationshipType === 'contributes-to'
      && edge.targetId === milestone.id
      && edge.state === 'active');
    const deliverables = contributionEdges
      .map((edge) => itemsById.get(edge.sourceId))
      .filter((item): item is TimelineItem => Boolean(item));
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
    const progress = typeof milestone.progress === 'number'
      ? milestone.progress
      : deliverables.length ? Math.round((complete / deliverables.length) * 100) : 0;
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
    primaryMilestoneId: stringValue(raw.primaryMilestoneId),
    scheduleSlackDays: finiteNumber(raw.scheduleSlackDays),
    criticalPathSlackDays: finiteNumber(raw.criticalPathSlackDays),
    durationDays: Math.max(1, Math.round(finiteNumber(raw.durationDays) ?? 1)),
    isCritical: raw.isCritical === true,
    updated: stringValue(raw.updated),
  };
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
    ?? (legacyKind ? legacyMap[legacyKind] : undefined);
  if (!relationshipType) return null;
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
    directed: raw.directed !== false && relationshipType !== 'related',
    state: enumValue(raw.state, RELATIONSHIP_STATES) ?? 'active',
    dependencyMode: enumValue(raw.dependencyMode, DEPENDENCY_MODES) ?? (relationshipType === 'depends-on' ? 'finish-to-start' : null),
    hardness: enumValue(raw.hardness, HARDNESS_LEVELS),
    leadLagDays: finiteNumber(raw.leadLagDays) ?? 0,
    clearingCondition: stringValue(raw.clearingCondition),
    ownerLabel: stringValue(raw.ownerLabel),
    primaryContribution: raw.primaryContribution === true,
    entryEvidenceIds: stringArray(raw.entryEvidenceIds),
    exitEvidenceIds: stringArray(raw.exitEvidenceIds),
    evidenceSourceIds: stringArray(raw.evidenceSourceIds),
    effectiveRevision: stringValue(raw.effectiveRevision),
    created: stringValue(raw.created),
    updated: stringValue(raw.updated),
    targetInSnapshot: raw.targetInSnapshot === true,
    legacy: raw.legacy !== false,
  };
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

function stringValue(raw: unknown): string | null {
  return typeof raw === 'string' && raw.trim() ? raw : null;
}

function stringArray(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((value): value is string => typeof value === 'string') : [];
}

function finiteNumber(raw: unknown): number | null {
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
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
