export const TOOL_LIST_COMMENTS = 'native_tracker_list_comments';
export const TOOL_GET_WITH_COMMENTS = 'native_tracker_get_with_comments';
export const TOOL_SYNC_TIMELINE = 'native_tracker_sync_timeline';
export const TOOL_MILESTONE_REPORT = 'native_tracker_generate_milestone_report';
export const TOOL_QUERY = 'native_tracker_query';
export const TOOL_TRAVERSE = 'native_tracker_traverse';

export const TOOL_NAMES = [
  TOOL_LIST_COMMENTS,
  TOOL_GET_WITH_COMMENTS,
  TOOL_SYNC_TIMELINE,
  TOOL_MILESTONE_REPORT,
  TOOL_QUERY,
  TOOL_TRAVERSE,
] as const;

export const TOOL_NAMES_BY_FAMILY = {
  read: [
    TOOL_LIST_COMMENTS,
    TOOL_GET_WITH_COMMENTS,
    TOOL_QUERY,
    TOOL_TRAVERSE,
  ],
  projection: [
    TOOL_SYNC_TIMELINE,
    TOOL_MILESTONE_REPORT,
  ],
} as const;

export type BackendFamily = keyof typeof TOOL_NAMES_BY_FAMILY;
