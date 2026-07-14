export const TOOL_LIST_COMMENTS = 'native_tracker_list_comments';
export const TOOL_GET_WITH_COMMENTS = 'native_tracker_get_with_comments';

export const TOOL_NAMES = [TOOL_LIST_COMMENTS, TOOL_GET_WITH_COMMENTS] as const;

export type ReaderMethod = 'list_comments' | 'get_with_comments';

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
