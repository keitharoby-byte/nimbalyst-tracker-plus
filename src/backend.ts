import { randomUUID } from 'node:crypto';
import { lstat, readFile, realpath, rename, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';

import {
  TOOL_GET_WITH_COMMENTS,
  TOOL_LIST_COMMENTS,
  TOOL_MILESTONE_REPORT,
  TOOL_SYNC_TIMELINE,
  type BackendContext,
  type McpToolDescriptor,
  type ReaderMethod,
} from './contracts';
import { NativeTrackerError, safeErrorResult } from './errors';
import { PythonBridge } from './pythonBridge';

const COMMENT_ALLOWED_KEYS = new Set(['trackerId', 'limit', 'cursor', 'since', 'order']);
const TIMELINE_ALLOWED_KEYS = new Set(['outputPath', 'includeUnscheduled', 'maxItems', 'from', 'to']);
const REPORT_ALLOWED_KEYS = new Set(['outputPath', 'milestoneId', 'asOf', 'lookaheadDays', 'maxItems']);
const MAX_EXISTING_TIMELINE_BYTES = 1024 * 1024;

const PAGINATION_PROPERTIES = {
  trackerId: {
    type: 'string',
    description: 'Native issue key (for example NIM-123) or internal tracker id.',
  },
  limit: {
    type: 'number',
    description: 'Maximum comments to return. Defaults to 20 and is capped at 100.',
    default: 20,
  },
  cursor: {
    type: 'string',
    description: 'Opaque nextCursor returned by an earlier call.',
  },
  since: {
    type: 'string',
    description: 'Optional ISO-8601 lower bound on comment creation time.',
  },
  order: {
    type: 'string',
    enum: ['newest', 'oldest'],
    description: 'Comment order. Defaults to newest.',
    default: 'newest',
  },
};

const TIMELINE_PROPERTIES = {
  outputPath: {
    type: 'string',
    description: 'Workspace-relative .ntimeline file. Defaults to Tracker Timeline.ntimeline.',
    default: 'Tracker Timeline.ntimeline',
  },
  includeUnscheduled: {
    type: 'boolean',
    description: 'Include tracker items without dates in the unscheduled lane.',
    default: true,
  },
  maxItems: {
    type: 'number',
    description: 'Maximum tracker items to project. Defaults to 300 and is capped at 500.',
    default: 300,
  },
  from: {
    type: 'string',
    description: 'Optional ISO-8601 range start.',
  },
  to: {
    type: 'string',
    description: 'Optional ISO-8601 range end.',
  },
};

const TOOL_DESCRIPTORS: McpToolDescriptor[] = [
  {
    name: TOOL_LIST_COMMENTS,
    description: 'Read one bounded page of non-deleted comments from a native Nimbalyst tracker item in the current workspace. Use before tracker_update or tracker_add_comment when existing discussion may affect the durable update. This tool never writes tracker data.',
    scope: 'global',
    inputSchema: {
      type: 'object',
      properties: PAGINATION_PROPERTIES,
      required: ['trackerId'],
      additionalProperties: false,
    },
  },
  {
    name: TOOL_GET_WITH_COMMENTS,
    description: 'Read a native Nimbalyst tracker item together with one bounded page of its non-deleted comments in the current workspace. Use it to orient before making a durable update with Nimbalyst tracker_update or tracker_add_comment. This tool never writes tracker data.',
    scope: 'global',
    inputSchema: {
      type: 'object',
      properties: PAGINATION_PROPERTIES,
      required: ['trackerId'],
      additionalProperties: false,
    },
  },
  {
    name: TOOL_SYNC_TIMELINE,
    description: 'Project current native tracker items and normalized timeline-link records into a workspace .ntimeline document with schedule health, execution constraints, risk, critical path, validation findings, and provenance. The tracker database is opened read-only.',
    scope: 'global',
    inputSchema: {
      type: 'object',
      properties: TIMELINE_PROPERTIES,
      additionalProperties: false,
    },
  },
  {
    name: TOOL_MILESTONE_REPORT,
    description: 'Generate a Markdown milestone report that separates workflow, schedule health, execution constraints, risk, normalized dependencies, and governance validation. The tracker database is read-only.',
    scope: 'global',
    inputSchema: {
      type: 'object',
      properties: {
        outputPath: {
          type: 'string',
          description: 'Workspace-relative .md file. Defaults to Milestone Report.md.',
          default: 'Milestone Report.md',
        },
        milestoneId: {
          type: 'string',
          description: 'Optional milestone issue key or internal id. Omit for all milestones.',
        },
        asOf: {
          type: 'string',
          description: 'Optional ISO-8601 report date. Defaults to today.',
        },
        lookaheadDays: {
          type: 'number',
          description: 'Upcoming-work window. Defaults to 30 and is capped at 365.',
          default: 30,
        },
        maxItems: {
          type: 'number',
          description: 'Maximum tracker items to scan. Defaults to 500 and is capped at 500.',
          default: 500,
        },
      },
      additionalProperties: false,
    },
  },
];

function ensureWorkspace(workspacePath: string): void {
  if (!path.isAbsolute(workspacePath)) {
    throw new NativeTrackerError({
      code: 'WORKSPACE_UNAVAILABLE',
      message: 'Tracker+ requires an open local workspace.',
    });
  }
}

function rejectUnknown(raw: Record<string, unknown>, allowed: Set<string>): void {
  const unknown = Object.keys(raw).filter((key) => !allowed.has(key));
  if (unknown.length > 0) {
    throw new NativeTrackerError({
      code: 'INVALID_PARAMS',
      message: `Unknown parameter(s): ${unknown.join(', ')}. Database and workspace paths cannot be supplied by callers.`,
    });
  }
}

function validatedCommentParams(
  raw: Record<string, unknown> | undefined,
  workspacePath: string,
): Record<string, unknown> {
  const params = raw ?? {};
  rejectUnknown(params, COMMENT_ALLOWED_KEYS);
  ensureWorkspace(workspacePath);
  if (typeof params.trackerId !== 'string' || params.trackerId.trim().length === 0) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'trackerId is required.' });
  }
  const limit = params.limit === undefined ? 20 : params.limit;
  if (typeof limit !== 'number' || !Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'limit must be an integer from 1 through 100.' });
  }
  if (params.cursor !== undefined && typeof params.cursor !== 'string') {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'cursor must be a string.' });
  }
  if (params.since !== undefined && typeof params.since !== 'string') {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'since must be an ISO-8601 string.' });
  }
  if (params.order !== undefined && params.order !== 'newest' && params.order !== 'oldest') {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'order must be newest or oldest.' });
  }
  return {
    trackerId: params.trackerId.trim(),
    limit,
    ...(params.cursor ? { cursor: params.cursor } : {}),
    ...(params.since ? { since: params.since } : {}),
    order: params.order ?? 'newest',
    workspacePath,
  };
}

function validatedTimelineParams(
  raw: Record<string, unknown> | undefined,
  workspacePath: string,
): Record<string, unknown> {
  const params = raw ?? {};
  rejectUnknown(params, TIMELINE_ALLOWED_KEYS);
  ensureWorkspace(workspacePath);
  const includeUnscheduled = params.includeUnscheduled ?? true;
  const maxItems = params.maxItems ?? 300;
  if (typeof includeUnscheduled !== 'boolean') {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'includeUnscheduled must be a boolean.' });
  }
  if (typeof maxItems !== 'number' || !Number.isInteger(maxItems) || maxItems < 1 || maxItems > 500) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'maxItems must be an integer from 1 through 500.' });
  }
  for (const field of ['from', 'to'] as const) {
    if (params[field] !== undefined && typeof params[field] !== 'string') {
      throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: `${field} must be an ISO-8601 string.` });
    }
  }
  return {
    workspacePath,
    includeUnscheduled,
    maxItems,
    outputPath: validatedOutputName(params.outputPath, 'Tracker Timeline.ntimeline', '.ntimeline'),
    ...(params.from ? { from: params.from } : {}),
    ...(params.to ? { to: params.to } : {}),
  };
}

function validatedReportParams(
  raw: Record<string, unknown> | undefined,
  workspacePath: string,
): Record<string, unknown> {
  const params = raw ?? {};
  rejectUnknown(params, REPORT_ALLOWED_KEYS);
  ensureWorkspace(workspacePath);
  const lookaheadDays = params.lookaheadDays ?? 30;
  const maxItems = params.maxItems ?? 500;
  if (typeof lookaheadDays !== 'number' || !Number.isInteger(lookaheadDays) || lookaheadDays < 1 || lookaheadDays > 365) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'lookaheadDays must be an integer from 1 through 365.' });
  }
  if (typeof maxItems !== 'number' || !Number.isInteger(maxItems) || maxItems < 1 || maxItems > 500) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'maxItems must be an integer from 1 through 500.' });
  }
  for (const field of ['milestoneId', 'asOf'] as const) {
    if (params[field] !== undefined && (typeof params[field] !== 'string' || !params[field].trim())) {
      throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: `${field} must be a non-empty string.` });
    }
  }
  return {
    workspacePath,
    outputPath: validatedOutputName(params.outputPath, 'Milestone Report.md', '.md'),
    lookaheadDays,
    maxItems,
    ...(typeof params.milestoneId === 'string' ? { milestoneId: params.milestoneId.trim() } : {}),
    ...(typeof params.asOf === 'string' ? { asOf: params.asOf.trim() } : {}),
  };
}

function validatedOutputName(raw: unknown, fallback: string, extension: string): string {
  const value = raw ?? fallback;
  if (typeof value !== 'string' || !value.trim() || value.includes('\0')) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: `outputPath must be a workspace-relative ${extension} file.` });
  }
  const normalized = value.trim().replaceAll('\\', '/');
  if (path.isAbsolute(normalized) || normalized.split('/').some((segment) => segment === '..')) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'outputPath must stay inside the current workspace.' });
  }
  if (!normalized.toLowerCase().endsWith(extension)) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: `outputPath must end in ${extension}.` });
  }
  return normalized;
}

async function confinedOutputPath(workspacePath: string, relativePath: string): Promise<string> {
  const workspaceReal = await realpath(workspacePath);
  const target = path.resolve(workspaceReal, relativePath);
  const relative = path.relative(workspaceReal, target);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    if (!relative) {
      throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'outputPath must name a file.' });
    }
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'outputPath must stay inside the current workspace.' });
  }
  try {
    const targetInfo = await lstat(target);
    if (targetInfo.isSymbolicLink() || !targetInfo.isFile()) {
      throw new NativeTrackerError({ code: 'OUTPUT_PATH_UNSAFE', message: 'The requested output path is not a regular workspace file.' });
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  let parentReal: string;
  try {
    parentReal = await realpath(path.dirname(target));
  } catch {
    throw new NativeTrackerError({ code: 'OUTPUT_DIRECTORY_NOT_FOUND', message: 'The requested output directory does not exist.' });
  }
  const parentRelative = path.relative(workspaceReal, parentReal);
  if (parentRelative.startsWith('..') || path.isAbsolute(parentRelative)) {
    throw new NativeTrackerError({ code: 'OUTPUT_PATH_UNSAFE', message: 'The requested output directory resolves outside the workspace.' });
  }
  return target;
}

async function atomicWrite(target: string, content: string): Promise<void> {
  const temporary = path.join(path.dirname(target), `.${path.basename(target)}.${randomUUID()}.tmp`);
  try {
    await writeFile(temporary, content, { encoding: 'utf8', flag: 'wx' });
    await rename(temporary, target);
  } catch {
    throw new NativeTrackerError({
      code: 'OUTPUT_WRITE_FAILED',
      message: 'The generated workspace file could not be written safely.',
    });
  }
}

async function readTimelineShell(target: string): Promise<Record<string, unknown>> {
  try {
    const info = await stat(target);
    if (info.size > MAX_EXISTING_TIMELINE_BYTES) return {};
    const parsed = JSON.parse(await readFile(target, 'utf8')) as unknown;
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function readerParams(params: Record<string, unknown>): Record<string, unknown> {
  const { outputPath: _outputPath, ...reader } = params;
  return reader;
}

export async function activate(context: BackendContext): Promise<{
  methods: Record<string, (params?: Record<string, unknown>) => Promise<unknown>>;
  deactivate: () => Promise<void>;
}> {
  const { workspacePath, log, registerMcpTools } = context.services;
  const extensionPath = context.runtimeContext?.extensionPath ?? context.extensionPath;
  if (!extensionPath) throw new Error('The host did not provide the extension installation path.');

  const bridge = new PythonBridge(extensionPath, log);
  await registerMcpTools(TOOL_DESCRIPTORS);
  log('info', '[tracker-plus] addon.activate');

  const callReader = async (method: ReaderMethod, params: Record<string, unknown>): Promise<unknown> => {
    const started = Date.now();
    try {
      const result = await bridge.request(method, params);
      log('info', `[tracker-plus] tool.${method} durationMs=${Date.now() - started}`);
      return result;
    } catch (error) {
      const safe = safeErrorResult(error);
      log('error', `[tracker-plus] tool.error method=${method} code=${safe.error.code} durationMs=${Date.now() - started}`);
      return safe;
    }
  };

  const callComments = async (method: 'list_comments' | 'get_with_comments', raw?: Record<string, unknown>) => {
    try {
      return await callReader(method, validatedCommentParams(raw, workspacePath));
    } catch (error) {
      return safeErrorResult(error);
    }
  };

  const syncTimeline = async (raw?: Record<string, unknown>): Promise<unknown> => {
    try {
      const params = validatedTimelineParams(raw, workspacePath);
      const snapshot = await bridge.request('timeline_snapshot', readerParams(params)) as Record<string, unknown>;
      const target = await confinedOutputPath(workspacePath, String(params.outputPath));
      const existing = await readTimelineShell(target);
      const document = {
        version: 2,
        title: typeof existing.title === 'string' ? existing.title : 'Tracker Timeline',
        view: existing.view && typeof existing.view === 'object'
          ? existing.view
          : { mode: 'timeline', zoom: 'week', showUnscheduled: true, compactRows: true, fitToWidth: true, summaryRows: false },
        filters: {
          includeUnscheduled: params.includeUnscheduled,
          ...(params.from ? { from: params.from } : {}),
          ...(params.to ? { to: params.to } : {}),
        },
        snapshot,
      };
      await atomicWrite(target, `${JSON.stringify(document, null, 2)}\n`);
      const page = snapshot.page as Record<string, unknown> | undefined;
      const milestones = snapshot.milestones as unknown[] | undefined;
      const relationships = snapshot.relationships as unknown[] | undefined;
      return {
        action: 'timeline-synced',
        file: params.outputPath,
        generatedAt: snapshot.generatedAt,
        itemCount: page?.returned ?? 0,
        milestoneCount: milestones?.length ?? 0,
        relationshipCount: relationships?.length ?? 0,
        truncated: Boolean(page?.queryTruncated || page?.responseTruncated),
        source: snapshot.source,
      };
    } catch (error) {
      return safeErrorResult(error);
    }
  };

  const generateReport = async (raw?: Record<string, unknown>): Promise<unknown> => {
    try {
      const params = validatedReportParams(raw, workspacePath);
      const report = await bridge.request('milestone_report', readerParams(params)) as Record<string, unknown>;
      const markdown = typeof report.markdown === 'string' ? report.markdown : '# Milestone report\n';
      const target = await confinedOutputPath(workspacePath, String(params.outputPath));
      await atomicWrite(target, markdown.endsWith('\n') ? markdown : `${markdown}\n`);
      return {
        action: 'milestone-report-generated',
        file: params.outputPath,
        generatedAt: report.generatedAt,
        asOf: report.asOf,
        lookaheadDays: report.lookaheadDays,
        milestones: report.milestones,
        markdown,
        source: report.source,
      };
    } catch (error) {
      return safeErrorResult(error);
    }
  };

  return {
    methods: {
      [TOOL_LIST_COMMENTS]: async (params) => await callComments('list_comments', params),
      [TOOL_GET_WITH_COMMENTS]: async (params) => await callComments('get_with_comments', params),
      [TOOL_SYNC_TIMELINE]: syncTimeline,
      [TOOL_MILESTONE_REPORT]: generateReport,
    },
    deactivate: async () => {
      await bridge.stop();
      log('info', '[tracker-plus] addon.deactivate');
    },
  };
}

export default { activate };
