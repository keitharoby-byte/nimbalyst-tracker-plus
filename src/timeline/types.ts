export type TimelineMode = 'timeline' | 'graph' | 'report';
export type TimelineZoom = 'day' | 'week' | 'month';
export type ScheduleHealth = 'on-track' | 'at-risk' | 'late';
export type ExecutionConstraint = 'clear' | 'waiting' | 'blocked' | 'paused';
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type RelationshipType =
  | 'depends-on'
  | 'contributes-to'
  | 'reviews'
  | 'evidences'
  | 'implements'
  | 'related';
export type DependencyMode = 'finish-to-start' | 'start-to-start' | 'finish-to-finish' | 'start-to-finish';
export type RelationshipHardness = 'hard-serial' | 'shared-resource' | 'soft-coordination';
export type RelationshipState = 'active' | 'cleared' | 'superseded';
export type ValidationSeverity = 'error' | 'warning' | 'info';

export interface TimelineItem {
  id: string;
  issueKey?: string | null;
  primaryType: string;
  typeTags: string[];
  title: string;
  workflow?: string | null;
  priority?: string | null;
  ownerLabel?: string | null;
  startDate?: string | null;
  dueDate?: string | null;
  forecastDate?: string | null;
  progress?: number | null;
  scheduleHealth: ScheduleHealth;
  scheduleHealthReasons: string[];
  executionConstraint: ExecutionConstraint;
  impact?: number | null;
  likelihood?: number | null;
  riskScore?: number | null;
  riskLevel: RiskLevel;
  riskReasons: string[];
  riskDurability?: string | null;
  recoverability?: string | null;
  evidenceConfidence?: string | null;
  technicalUncertainty?: string | null;
  capacityPressure?: string | null;
  gate?: string | null;
  launchScoped: boolean;
  primaryMilestoneId?: string | null;
  scheduleSlackDays?: number | null;
  criticalPathSlackDays?: number | null;
  durationDays: number;
  isCritical: boolean;
  updated?: string | null;
}

export interface TimelineRelationship {
  id: string;
  issueKey?: string | null;
  sourceId: string;
  sourceIssueKey?: string | null;
  sourceTitle?: string | null;
  targetId: string;
  targetIssueKey?: string | null;
  targetTitle?: string | null;
  targetType?: string | null;
  relationshipType: RelationshipType;
  directed: boolean;
  state: RelationshipState;
  dependencyMode?: DependencyMode | null;
  hardness?: RelationshipHardness | null;
  leadLagDays: number;
  clearingCondition?: string | null;
  ownerLabel?: string | null;
  primaryContribution: boolean;
  entryEvidenceIds: string[];
  exitEvidenceIds: string[];
  evidenceSourceIds: string[];
  effectiveRevision?: string | null;
  created?: string | null;
  updated?: string | null;
  targetInSnapshot: boolean;
  legacy: boolean;
}

export interface ValidationFinding {
  code: string;
  severity: ValidationSeverity;
  message: string;
  itemIds: string[];
  relationshipIds: string[];
}

export interface CriticalPathSummary {
  durationDays: number;
  itemIds: string[];
  cycleItemIds: string[];
}

export interface TimelineSnapshot {
  generatedAt: string | null;
  items: TimelineItem[];
  milestones: TimelineItem[];
  relationships: TimelineRelationship[];
  validation: ValidationFinding[];
  criticalPath: CriticalPathSummary;
  page: {
    maxItems?: number;
    returned: number;
    queryTruncated?: boolean;
    responseTruncated?: boolean;
  };
  source: {
    backend?: string;
    mode?: string;
    schemaAdapter?: string;
    schemaFingerprint?: string;
    projectStateRevision?: string;
    [key: string]: unknown;
  };
}

export interface TimelineDocument {
  version: 2;
  title: string;
  view: {
    mode: TimelineMode;
    zoom: TimelineZoom;
    showUnscheduled: boolean;
    compactRows: boolean;
    fitToWidth: boolean;
    summaryRows: boolean;
  };
  filters: {
    includeUnscheduled: boolean;
    from?: string;
    to?: string;
  };
  snapshot: TimelineSnapshot;
}

export interface MilestoneSummary {
  milestone: TimelineItem;
  deliverables: TimelineItem[];
  complete: number;
  overdue: TimelineItem[];
  activeDependencies: TimelineRelationship[];
  blockedItems: TimelineItem[];
  waitingItems: TimelineItem[];
  scheduleHealth: ScheduleHealth | 'achieved';
  progress: number;
  riskLevel: RiskLevel;
}
