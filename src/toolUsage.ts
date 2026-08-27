export const DEFAULT_CUSTOM_FIELDS_DEPTH = 128;
export const MAX_CUSTOM_FIELDS_DEPTH = 512;

export const CUSTOM_FIELDS_DEPTH_PROPERTY = {
  type: 'integer',
  minimum: 1,
  maximum: MAX_CUSTOM_FIELDS_DEPTH,
  default: DEFAULT_CUSTOM_FIELDS_DEPTH,
  description: 'Maximum legacy customFields envelopes to unwrap per item. Raise for deeply nested historical timelines; the hard cap remains 512.',
};

const TOOL_USAGE: Record<string, Record<string, unknown>> = {
  native_tracker_list_comments: {
    required: ['trackerId'],
    constraints: ['limit is 1..100', 'order is newest or oldest', 'cursor is opaque'],
    example: { trackerId: 'ITEM-123', limit: 20, order: 'newest' },
  },
  native_tracker_get_with_comments: {
    required: ['trackerId'],
    constraints: ['limit is 1..100', 'order is newest or oldest', 'cursor is opaque'],
    example: { trackerId: 'ITEM-123', limit: 20 },
  },
  native_tracker_sync_timeline: {
    chooseAtMostOne: ['launch', 'selector'],
    constraints: [
      'outputPath is a workspace-relative .ntimeline file',
      'maxItems is 1..500',
      `maxCustomFieldsDepth is 1..${MAX_CUSTOM_FIELDS_DEPTH}`,
    ],
    example: { outputPath: 'Tracker Timeline.ntimeline', maxCustomFieldsDepth: DEFAULT_CUSTOM_FIELDS_DEPTH },
  },
  native_tracker_generate_milestone_report: {
    constraints: [
      'outputPath is a workspace-relative .md file',
      'lookaheadDays is 1..365',
      'maxItems is 1..500',
      `maxCustomFieldsDepth is 1..${MAX_CUSTOM_FIELDS_DEPTH}`,
    ],
    example: { outputPath: 'Milestone Report.md', lookaheadDays: 30, maxCustomFieldsDepth: DEFAULT_CUSTOM_FIELDS_DEPTH },
  },
  native_tracker_query: {
    chooseExactlyOne: ['where', 'savedQuery'],
    constraints: ['limit is 1..200', 'cursor must come from the identical preceding query', `maxCustomFieldsDepth is 1..${MAX_CUSTOM_FIELDS_DEPTH}`],
    example: { where: { field: 'status', op: 'eq', value: 'open' }, maxCustomFieldsDepth: DEFAULT_CUSTOM_FIELDS_DEPTH },
  },
  native_tracker_traverse: {
    chooseExactlyOne: ['roots', 'savedQuery'],
    constraints: ['roots contains 1..8 identifiers', 'cursor requires paginate=true', `maxCustomFieldsDepth is 1..${MAX_CUSTOM_FIELDS_DEPTH}`],
    example: { roots: ['ITEM-123'], paginate: true, maxCustomFieldsDepth: DEFAULT_CUSTOM_FIELDS_DEPTH },
  },
};

const USAGE_ERROR_CODES = new Set([
  'INVALID_PARAMS',
  'QUERY_INVALID',
  'QUERY_TOO_COMPLEX',
  'CURSOR_INVALID',
  'CUSTOM_FIELDS_NESTING_EXCEEDED',
]);

interface HelpableErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export function helpedErrorPayload(
  payload: HelpableErrorPayload,
  toolName: string,
): HelpableErrorPayload {
  if (!USAGE_ERROR_CODES.has(payload.code) || !TOOL_USAGE[toolName]) return payload;
  return {
    ...payload,
    details: {
      ...payload.details,
      usage: {
        tool: toolName,
        ...TOOL_USAGE[toolName],
      },
    },
  };
}
