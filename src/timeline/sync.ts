type UnknownRecord = Record<string, unknown>;

export interface TimelineValidationSummary {
  state: 'pass' | 'warn' | 'fail';
  total: number;
  bySeverity: {
    error: number;
    warning: number;
    info: number;
  };
  byCode: Record<string, number>;
}

export interface TimelineProjectionDelta {
  priorGenerationId: string | null;
  currentGenerationId: string;
  addedNodeIds: string[];
  removedNodeIds: string[];
  addedRelationshipIds: string[];
  removedRelationshipIds: string[];
  milestoneCount: {
    prior: number;
    current: number;
    change: number;
  };
}

export interface TimelineSyncPreparation {
  document: UnknownRecord;
  snapshot: UnknownRecord;
  validation: TimelineValidationSummary;
  delta: TimelineProjectionDelta;
}

const DEFAULT_VIEW = {
  mode: 'timeline',
  zoom: 'week',
  showUnscheduled: true,
  compactRows: true,
  fitToWidth: true,
  summaryRows: false,
};

function isRecord(value: unknown): value is UnknownRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function records(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function stableIds(value: unknown): string[] {
  return records(value)
    .map((entry) => entry.id)
    .filter((id): id is string => typeof id === 'string' && id.length > 0)
    .sort((left, right) => left.localeCompare(right));
}

function difference(left: string[], right: string[]): string[] {
  const rightIds = new Set(right);
  return left.filter((id) => !rightIds.has(id));
}

function generationId(snapshot: UnknownRecord): string | null {
  if (typeof snapshot.generationId === 'string' && snapshot.generationId) return snapshot.generationId;
  return typeof snapshot.generatedAt === 'string' && snapshot.generatedAt ? snapshot.generatedAt : null;
}

export function summarizeTimelineValidation(raw: unknown): TimelineValidationSummary {
  const bySeverity = { error: 0, warning: 0, info: 0 };
  const counts = new Map<string, number>();
  for (const finding of records(raw)) {
    const severity = finding.severity;
    if (severity === 'error' || severity === 'warning' || severity === 'info') bySeverity[severity] += 1;
    const code = typeof finding.code === 'string' && finding.code ? finding.code : 'unknown';
    counts.set(code, (counts.get(code) ?? 0) + 1);
  }
  const byCode = Object.fromEntries([...counts.entries()].sort(([left], [right]) => left.localeCompare(right)));
  const total = bySeverity.error + bySeverity.warning + bySeverity.info;
  return {
    state: bySeverity.error > 0 ? 'fail' : bySeverity.warning > 0 ? 'warn' : 'pass',
    total,
    bySeverity,
    byCode,
  };
}

export function prepareTimelineSync(
  existing: UnknownRecord,
  incomingSnapshot: UnknownRecord,
  params: UnknownRecord,
  currentGenerationId: string,
): TimelineSyncPreparation {
  const previousSnapshot = isRecord(existing.snapshot) ? existing.snapshot : {};
  const snapshot: UnknownRecord = { ...incomingSnapshot, generationId: currentGenerationId };
  const previousNodeIds = stableIds(previousSnapshot.items);
  const currentNodeIds = stableIds(snapshot.items);
  const previousRelationshipIds = stableIds(previousSnapshot.relationships);
  const currentRelationshipIds = stableIds(snapshot.relationships);
  const previousMilestones = records(previousSnapshot.milestones).length;
  const currentMilestones = records(snapshot.milestones).length;
  const filters = isRecord(existing.filters) ? { ...existing.filters } : {};

  filters.includeUnscheduled = params.includeUnscheduled;
  if (typeof params.from === 'string') filters.from = params.from;
  if (typeof params.to === 'string') filters.to = params.to;
  if (typeof params.launch === 'string') filters.launch = params.launch;

  return {
    document: {
      version: 2,
      title: typeof existing.title === 'string' && existing.title.trim() ? existing.title : 'Tracker Timeline',
      view: isRecord(existing.view) ? { ...existing.view } : { ...DEFAULT_VIEW },
      filters,
      snapshot,
    },
    snapshot,
    validation: summarizeTimelineValidation(snapshot.validation),
    delta: {
      priorGenerationId: generationId(previousSnapshot),
      currentGenerationId,
      addedNodeIds: difference(currentNodeIds, previousNodeIds),
      removedNodeIds: difference(previousNodeIds, currentNodeIds),
      addedRelationshipIds: difference(currentRelationshipIds, previousRelationshipIds),
      removedRelationshipIds: difference(previousRelationshipIds, currentRelationshipIds),
      milestoneCount: {
        prior: previousMilestones,
        current: currentMilestones,
        change: currentMilestones - previousMilestones,
      },
    },
  };
}
