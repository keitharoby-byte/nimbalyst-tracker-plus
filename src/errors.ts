import type { ReaderErrorPayload } from './contracts.ts';

export class NativeTrackerError extends Error {
  readonly code: string;
  readonly details?: Record<string, unknown>;

  constructor(payload: ReaderErrorPayload) {
    super(payload.message);
    this.name = 'NativeTrackerError';
    this.code = payload.code;
    this.details = payload.details;
  }

  toJSON(): ReaderErrorPayload {
    return {
      code: this.code,
      message: this.message,
      ...(this.details ? { details: this.details } : {}),
    };
  }
}

export class BridgeTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BridgeTransportError';
  }
}

export function safeErrorResult(error: unknown): { error: ReaderErrorPayload } {
  if (error instanceof NativeTrackerError) {
    return { error: error.toJSON() };
  }

  return {
    error: {
      code: 'READER_UNAVAILABLE',
      message: error instanceof Error ? error.message : 'The native tracker reader is unavailable.',
    },
  };
}
