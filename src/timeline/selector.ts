type UnknownRecord = Record<string, unknown>;

export interface TimelineTagSelector {
  launchTags: string[];
}

export interface SelectorSnapshotFailure {
  code: 'RESULT_TRUNCATED' | 'VALIDATION_FAILED';
  message: string;
}

function isRecord(value: unknown): value is UnknownRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function normalizeTimelineSelector(raw: unknown): TimelineTagSelector | undefined {
  if (raw === undefined) return undefined;
  if (!isRecord(raw)) throw new Error('selector must be an object.');
  const unknownKeys = Object.keys(raw).filter((key) => key !== 'launchTags');
  if (unknownKeys.length > 0) throw new Error(`Unknown selector parameter(s): ${unknownKeys.join(', ')}.`);
  const launchTags = raw.launchTags;
  if (!Array.isArray(launchTags) || launchTags.length < 1 || launchTags.length > 8) {
    throw new Error('selector.launchTags must contain 1 through 8 tags.');
  }
  const normalized = launchTags.map((value) => typeof value === 'string'
    ? value.trim().toLocaleLowerCase('en-US')
    : '');
  if (normalized.some((value) => !value || value.length > 100)) {
    throw new Error('selector.launchTags values must be non-empty strings of at most 100 characters.');
  }
  if (new Set(normalized).size !== normalized.length) {
    throw new Error('selector.launchTags must be unique after normalization.');
  }
  return { launchTags: normalized.sort((left, right) => left.localeCompare(right)) };
}

export function selectorSnapshotFailure(snapshot: UnknownRecord): SelectorSnapshotFailure | null {
  const page = isRecord(snapshot.page) ? snapshot.page : {};
  if (page.queryTruncated || page.responseTruncated) {
    return {
      code: 'RESULT_TRUNCATED',
      message: 'The selected timeline was incomplete, so the destination was not replaced.',
    };
  }
  const validation = Array.isArray(snapshot.validation) ? snapshot.validation : [];
  if (validation.some((finding) => isRecord(finding) && finding.severity === 'error')) {
    return {
      code: 'VALIDATION_FAILED',
      message: 'The selected timeline failed validation, so the destination was not replaced.',
    };
  }
  return null;
}
