export const TOOL_LIST_COMMENTS = 'native_tracker_list_comments';
export const TOOL_GET_WITH_COMMENTS = 'native_tracker_get_with_comments';
export const TOOL_SYNC_TIMELINE = 'native_tracker_sync_timeline';
export const TOOL_MILESTONE_REPORT = 'native_tracker_generate_milestone_report';

export const TOOL_NAMES = [
  TOOL_LIST_COMMENTS,
  TOOL_GET_WITH_COMMENTS,
  TOOL_SYNC_TIMELINE,
  TOOL_MILESTONE_REPORT,
] as const;

export type ReaderMethod =
  | 'list_comments'
  | 'get_with_comments'
  | 'timeline_snapshot'
  | 'milestone_report';

export interface ReaderErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ReaderProtocolRequest {
  id: string;
  method: ReaderMethod;
  params: Record<string, unknown>;
}

export type ReaderProtocolResponse =
  | { id: string; ok: true; result: unknown }
  | { id: string; ok: false; error: ReaderErrorPayload };

export interface BackendContext {
  extensionPath?: string;
  runtimeContext?: {
    extensionPath?: string;
    extensionId?: string;
  };
  services: {
    workspacePath: string;
    log: (level: 'info' | 'warn' | 'error', message: string) => void;
    registerMcpTools: (tools: McpToolDescriptor[]) => Promise<void>;
  };
}

export interface McpToolDescriptor {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  scope: 'global';
}
