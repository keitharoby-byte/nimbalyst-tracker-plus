import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import path from 'node:path';

import type {
  ReaderMethod,
  ReaderProtocolRequest,
  ReaderProtocolResponse,
} from './contracts.ts';
import { BridgeTransportError, NativeTrackerError } from './errors.ts';
import {
  prepareReaderSnapshot,
  ReaderBundleError,
  removeReaderSnapshot,
  type ReaderSnapshot,
} from './readerBundle.ts';

const MAX_INPUT_LINE_BYTES = 64 * 1024;
const MAX_OUTPUT_LINE_BYTES = 512 * 1024;
const MIN_READER_DEADLINE_MS = 10_000;
const MAX_READER_DEADLINE_MS = 45_000;

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timeout: NodeJS.Timeout;
}

interface PythonCandidate {
  command: string;
  prefixArgs: string[];
}

export interface PythonBridgeOptions {
  deadlineFor?: (method: ReaderMethod, params: Record<string, unknown>) => number;
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
    ? Math.min(value, maximum)
    : fallback;
}

export function readerRequestDeadlineMs(
  method: ReaderMethod,
  params: Record<string, unknown>,
): number {
  if (method === 'query_items') {
    const limit = boundedInteger(params.limit, 50, 200);
    return Math.min(MAX_READER_DEADLINE_MS, 12_000 + (limit * 50));
  }
  if (method === 'traverse_graph') {
    const limits = params.limits && typeof params.limits === 'object' && !Array.isArray(params.limits)
      ? params.limits as Record<string, unknown>
      : {};
    const maxNodes = boundedInteger(limits.maxNodes, 500, 500);
    const maxEdges = boundedInteger(limits.maxEdges, 1_000, 1_000);
    return Math.min(MAX_READER_DEADLINE_MS, 15_000 + (maxNodes * 20) + (maxEdges * 10));
  }
  if (method === 'timeline_snapshot' || method === 'milestone_report') {
    return 30_000;
  }
  return MIN_READER_DEADLINE_MS;
}

export class PythonBridge {
  private readonly extensionPath: string;
  private readonly log: (level: 'info' | 'warn' | 'error', message: string) => void;
  private readonly options: PythonBridgeOptions;
  private child: ChildProcessWithoutNullStreams | null = null;
  private stdoutBuffer = Buffer.alloc(0);
  private readonly pending = new Map<string, PendingRequest>();
  private starting: Promise<void> | null = null;
  private snapshot: ReaderSnapshot | null = null;

  constructor(
    extensionPath: string,
    log: (level: 'info' | 'warn' | 'error', message: string) => void,
    options: PythonBridgeOptions = {},
  ) {
    this.extensionPath = extensionPath;
    this.log = log;
    this.options = options;
  }

  async request(method: ReaderMethod, params: Record<string, unknown>): Promise<unknown> {
    let lastError: unknown;

    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        return await this.requestOnce(method, params, attempt + 1);
      } catch (error) {
        if (!(error instanceof BridgeTransportError) || attempt === 1) {
          throw error;
        }
        lastError = error;
        this.log('warn', `[tracker-plus] helper.restart attempt=${attempt + 1}`);
        await this.stop();
      }
    }

    throw lastError instanceof Error
      ? lastError
      : new BridgeTransportError('The native tracker helper failed twice.');
  }

  async stop(): Promise<void> {
    const child = this.child;
    this.child = null;
    this.starting = null;
    this.stdoutBuffer = Buffer.alloc(0);

    const childStopped = child && child.exitCode === null
      ? new Promise<void>((resolve) => {
          const fallback = setTimeout(resolve, 1_000);
          fallback.unref();
          child.once('exit', () => {
            clearTimeout(fallback);
            resolve();
          });
          if (!child.killed) child.kill();
        })
      : Promise.resolve();

    this.rejectAll(new BridgeTransportError('The native tracker helper stopped.'));
    const snapshot = this.snapshot;
    this.snapshot = null;
    await childStopped;
    await removeReaderSnapshot(snapshot);
  }

  private async requestOnce(
    method: ReaderMethod,
    params: Record<string, unknown>,
    attempt: number,
  ): Promise<unknown> {
    const coldStart = !this.child || this.child.killed;
    await this.ensureStarted();
    const child = this.child;
    if (!child || child.killed) {
      throw new BridgeTransportError('The native tracker helper did not start.');
    }

    const request: ReaderProtocolRequest = {
      id: randomUUID(),
      method,
      params,
    };
    const encoded = `${JSON.stringify(request)}\n`;
    if (Buffer.byteLength(encoded, 'utf8') > MAX_INPUT_LINE_BYTES) {
      throw new NativeTrackerError({
        code: 'INVALID_PARAMS',
        message: 'The tracker comment request is too large.',
      });
    }

    const configuredDeadlineMs = Math.max(
      1,
      Math.floor((this.options.deadlineFor ?? readerRequestDeadlineMs)(method, params)),
    );
    const verifiedGeneration = this.snapshot?.manifest.generationId ?? 'unavailable';
    const phase = coldStart ? 'cold-start-and-execution' : 'execution';

    return await new Promise<unknown>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(request.id);
        const error = new NativeTrackerError({
          code: 'READER_TIMEOUT',
          message: 'The native tracker reader exceeded its supported execution deadline.',
          details: {
            method,
            configuredDeadlineMs,
            elapsedPhase: phase,
            attempt,
            verifiedGeneration,
          },
        });
        reject(error);
        this.rejectAll(error);
        this.log(
          'warn',
          `[tracker-plus] helper.timeout method=${method}`
          + ` deadlineMs=${configuredDeadlineMs}`
          + ` phase=${phase}`
          + ` attempt=${attempt}`
          + ` generation=${verifiedGeneration.slice(0, 12)}`,
        );
        void this.stop();
      }, configuredDeadlineMs);

      this.pending.set(request.id, { resolve, reject, timeout });
      child.stdin.write(encoded, 'utf8', (error) => {
        if (!error) return;
        const pending = this.pending.get(request.id);
        if (!pending) return;
        clearTimeout(pending.timeout);
        this.pending.delete(request.id);
        pending.reject(new BridgeTransportError('Failed to send a request to the native tracker helper.'));
      });
    });
  }

  private async ensureStarted(): Promise<void> {
    if (this.child && !this.child.killed) return;
    if (this.starting) return await this.starting;

    this.starting = this.startHelper();
    try {
      await this.starting;
    } finally {
      this.starting = null;
    }
  }

  private async startHelper(): Promise<void> {
    await removeReaderSnapshot(this.snapshot);
    try {
      this.snapshot = await prepareReaderSnapshot(this.extensionPath);
    } catch (error) {
      if (error instanceof ReaderBundleError) {
        throw new NativeTrackerError({
          code: error.code,
          message: error.message,
          details: error.details,
        });
      }
      throw error;
    }
    const scriptPath = path.join(this.snapshot.directory, 'server.py');
    if (!existsSync(scriptPath)) {
      throw new BridgeTransportError('The packaged native tracker reader is missing. Rebuild the extension.');
    }

    const candidates = this.pythonCandidates();
    let lastError: unknown;
    for (const candidate of candidates) {
      try {
        await this.spawnCandidate(candidate, scriptPath);
        this.log(
          'info',
          `[tracker-plus] helper.start command=${candidate.command}`
          + ` generation=${this.snapshot.manifest.generationId.slice(0, 12)}`
          + ` extension=${this.snapshot.manifest.extensionVersion}`,
        );
        return;
      } catch (error) {
        lastError = error;
      }
    }

    await removeReaderSnapshot(this.snapshot);
    this.snapshot = null;
    throw new NativeTrackerError({
      code: 'PYTHON_NOT_FOUND',
      message: 'Python 3 is required to read native tracker comments. Install Python 3 and reload the extension.',
      details: {
        attemptedCommands: candidates.map((candidate) => candidate.command),
        reason: lastError instanceof Error ? lastError.message : 'No Python 3 command could be started.',
      },
    });
  }

  private async spawnCandidate(candidate: PythonCandidate, scriptPath: string): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const child = spawn(candidate.command, [...candidate.prefixArgs, '-I', '-B', scriptPath], {
        cwd: path.dirname(scriptPath),
        env: {
          ...process.env,
          PYTHONUTF8: '1',
          PYTHONDONTWRITEBYTECODE: '1',
          TRACKER_PLUS_REQUIRE_BUNDLE_MANIFEST: '1',
        },
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      const onError = (error: Error): void => {
        reject(error);
      };
      child.once('error', onError);
      child.once('spawn', () => {
        child.off('error', onError);
        this.attachChild(child);
        resolve();
      });
    });
  }

  private attachChild(child: ChildProcessWithoutNullStreams): void {
    this.child = child;

    child.stdout.on('data', (chunk: Buffer) => this.onStdout(chunk));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk: string) => {
      for (const line of chunk.split(/\r?\n/).filter(Boolean)) {
        this.log('warn', `[tracker-plus] helper ${line.slice(0, 1_000)}`);
      }
    });
    child.once('exit', (code, signal) => {
      if (this.child !== child) return;
      this.child = null;
      this.rejectAll(new BridgeTransportError(`The native tracker helper exited unexpectedly (${code ?? signal ?? 'unknown'}).`));
    });
    child.once('error', () => {
      if (this.child !== child) return;
      this.child = null;
      this.rejectAll(new BridgeTransportError('The native tracker helper crashed.'));
    });
  }

  private onStdout(chunk: Buffer): void {
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);
    if (this.stdoutBuffer.length > MAX_OUTPUT_LINE_BYTES && !this.stdoutBuffer.includes(0x0a)) {
      this.rejectAll(new BridgeTransportError('The native tracker helper exceeded its output limit.'));
      void this.stop();
      return;
    }

    let newlineIndex = this.stdoutBuffer.indexOf(0x0a);
    while (newlineIndex >= 0) {
      const line = this.stdoutBuffer.subarray(0, newlineIndex);
      this.stdoutBuffer = this.stdoutBuffer.subarray(newlineIndex + 1);
      if (line.length > MAX_OUTPUT_LINE_BYTES) {
        this.rejectAll(new BridgeTransportError('The native tracker helper exceeded its output limit.'));
        void this.stop();
        return;
      }
      this.handleLine(line.toString('utf8'));
      newlineIndex = this.stdoutBuffer.indexOf(0x0a);
    }
  }

  private handleLine(line: string): void {
    let response: ReaderProtocolResponse;
    try {
      response = JSON.parse(line) as ReaderProtocolResponse;
    } catch {
      this.rejectAll(new BridgeTransportError('The native tracker helper returned invalid JSON.'));
      void this.stop();
      return;
    }

    const pending = this.pending.get(response.id);
    if (!pending) return;
    clearTimeout(pending.timeout);
    this.pending.delete(response.id);

    if (response.ok) {
      pending.resolve(response.result);
    } else {
      pending.reject(new NativeTrackerError(response.error));
    }
  }

  private rejectAll(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timeout);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private pythonCandidates(): PythonCandidate[] {
    const configured = process.env.NIMBALYST_TRACKER_PYTHON?.trim();
    const candidates: PythonCandidate[] = [];
    if (configured) candidates.push({ command: configured, prefixArgs: [] });
    if (process.platform === 'win32') candidates.push({ command: 'py', prefixArgs: ['-3'] });
    candidates.push({ command: 'python3', prefixArgs: [] });
    candidates.push({ command: 'python', prefixArgs: [] });
    return candidates;
  }
}
