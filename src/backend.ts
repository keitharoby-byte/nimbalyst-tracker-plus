import path from 'node:path';

import {
  TOOL_GET_WITH_COMMENTS,
  TOOL_LIST_COMMENTS,
  type BackendContext,
  type McpToolDescriptor,
  type ReaderMethod,
} from './contracts';
import { NativeTrackerError, safeErrorResult } from './errors';
import { PythonBridge } from './pythonBridge';

const ALLOWED_KEYS = new Set(['trackerId', 'limit', 'cursor', 'since', 'order']);

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
];

function validatedParams(
  raw: Record<string, unknown> | undefined,
  workspacePath: string,
): Record<string, unknown> {
  const params = raw ?? {};
  const unknown = Object.keys(params).filter((key) => !ALLOWED_KEYS.has(key));
  if (unknown.length > 0) {
    throw new NativeTrackerError({
      code: 'INVALID_PARAMS',
      message: `Unknown parameter(s): ${unknown.join(', ')}. Database and workspace paths cannot be supplied by callers.`,
    });
  }

  if (typeof params.trackerId !== 'string' || params.trackerId.trim().length === 0) {
    throw new NativeTrackerError({ code: 'INVALID_PARAMS', message: 'trackerId is required.' });
  }
  if (!path.isAbsolute(workspacePath)) {
    throw new NativeTrackerError({
      code: 'WORKSPACE_UNAVAILABLE',
      message: 'The native tracker reader requires an open local workspace.',
    });
  }

  const limit = params.limit === undefined ? 20 : params.limit;
  if (typeof limit !== 'number' || !Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new NativeTrackerError({
      code: 'INVALID_PARAMS',
      message: 'limit must be an integer from 1 through 100.',
    });
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

export async function activate(context: BackendContext): Promise<{
  methods: Record<string, (params?: Record<string, unknown>) => Promise<unknown>>;
  deactivate: () => Promise<void>;
}> {
  const { workspacePath, log, registerMcpTools } = context.services;
  const extensionPath = context.runtimeContext?.extensionPath ?? context.extensionPath;
  if (!extensionPath) {
    throw new Error('The host did not provide the extension installation path.');
  }

  const bridge = new PythonBridge(extensionPath, log);
  await registerMcpTools(TOOL_DESCRIPTORS);
  log('info', '[native-tracker-comments] addon.activate');

  const call = async (
    method: ReaderMethod,
    params?: Record<string, unknown>,
  ): Promise<unknown> => {
    const started = Date.now();
    try {
      const safeParams = validatedParams(params, workspacePath);
      const result = await bridge.request(method, safeParams);
      log('info', `[native-tracker-comments] tool.${method} durationMs=${Date.now() - started}`);
      return result;
    } catch (error) {
      const safe = safeErrorResult(error);
      log('error', `[native-tracker-comments] tool.error method=${method} code=${safe.error.code} durationMs=${Date.now() - started}`);
      return safe;
    }
  };

  return {
    methods: {
      [TOOL_LIST_COMMENTS]: async (params) => await call('list_comments', params),
      [TOOL_GET_WITH_COMMENTS]: async (params) => await call('get_with_comments', params),
    },
    deactivate: async () => {
      await bridge.stop();
      log('info', '[native-tracker-comments] addon.deactivate');
    },
  };
}

export default { activate };
