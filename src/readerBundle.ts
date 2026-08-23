import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

const BUNDLE_MANIFEST = 'bundle-manifest.json';
const BUNDLE_FORMAT_VERSION = 1;
const SUPPORTED_ADAPTER_VERSION = 5;
const SUPPORTED_REGISTRY_VERSION = 5;
const SNAPSHOT_ATTEMPTS = 20;
const SNAPSHOT_RETRY_MS = 100;
const REQUIRED_READER_FILES = new Set([
  'server.py',
  'database.py',
  'contracts.py',
  'registry.py',
  'registry.json',
  'saved-queries.json',
]);

export interface ReaderBundleManifest {
  formatVersion: number;
  generationId: string;
  extensionVersion: string;
  adapterVersion: number;
  registryVersion: number;
  files: Record<string, string>;
}

export interface ReaderSnapshot {
  directory: string;
  manifest: ReaderBundleManifest;
}

export class ReaderBundleError extends Error {
  readonly code = 'READER_RESTART_REQUIRED';
  readonly details: Record<string, unknown>;

  constructor(details: Record<string, unknown>) {
    super('Tracker+ could not load one complete reader generation. Reload the extension and retry.');
    this.name = 'ReaderBundleError';
    this.details = details;
  }
}

interface BundleFailure {
  cause: string;
  file?: string;
  expectedHash?: string;
  actualHash?: string;
  manifest?: Partial<ReaderBundleManifest>;
}

function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}

function bounded(value: unknown, limit = 300): string {
  return String(value ?? 'unknown').slice(0, limit);
}

function validateManifest(value: unknown): ReaderBundleManifest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('bundle manifest must be an object');
  }
  const manifest = value as Partial<ReaderBundleManifest>;
  if (
    manifest.formatVersion !== BUNDLE_FORMAT_VERSION
    || typeof manifest.generationId !== 'string'
    || !/^[a-f0-9]{64}$/.test(manifest.generationId)
    || typeof manifest.extensionVersion !== 'string'
    || !manifest.extensionVersion
    || manifest.adapterVersion !== SUPPORTED_ADAPTER_VERSION
    || manifest.registryVersion !== SUPPORTED_REGISTRY_VERSION
    || !manifest.files
    || typeof manifest.files !== 'object'
    || Array.isArray(manifest.files)
  ) {
    throw new Error('bundle manifest fields are invalid');
  }
  const entries = Object.entries(manifest.files);
  if (
    !entries.length
    || entries.some(([file, hash]) =>
      !/^[A-Za-z0-9._-]+$/.test(file) || !/^[a-f0-9]{64}$/.test(hash))
    || [...REQUIRED_READER_FILES].some((file) => !(file in manifest.files!))
  ) {
    throw new Error('bundle manifest file inventory is invalid');
  }
  return manifest as ReaderBundleManifest;
}

async function delay(milliseconds: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function trySnapshot(sourceDirectory: string): Promise<ReaderSnapshot> {
  const manifestPath = path.join(sourceDirectory, BUNDLE_MANIFEST);
  const manifestBytes = await readFile(manifestPath);
  const manifest = validateManifest(JSON.parse(manifestBytes.toString('utf8')) as unknown);
  const directory = await mkdtemp(path.join(tmpdir(), 'tracker-plus-reader-'));

  try {
    for (const [file, expectedHash] of Object.entries(manifest.files).sort(([left], [right]) =>
      left.localeCompare(right))) {
      const bytes = await readFile(path.join(sourceDirectory, file));
      const actualHash = sha256(bytes);
      if (actualHash !== expectedHash) {
        throw Object.assign(new Error(`reader asset ${file} does not match its manifest`), {
          bundleFailure: {
            cause: 'asset-hash-mismatch',
            file,
            expectedHash,
            actualHash,
            manifest,
          } satisfies BundleFailure,
        });
      }
      await writeFile(path.join(directory, file), bytes);
    }

    const confirmedManifest = await readFile(manifestPath);
    if (sha256(confirmedManifest) !== sha256(manifestBytes)) {
      throw Object.assign(new Error('reader bundle manifest changed during snapshot'), {
        bundleFailure: {
          cause: 'manifest-changed-during-snapshot',
          manifest,
        } satisfies BundleFailure,
      });
    }
    await writeFile(path.join(directory, BUNDLE_MANIFEST), manifestBytes);
    return { directory, manifest };
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

export async function prepareReaderSnapshot(
  extensionPath: string,
  options: { attempts?: number; retryMs?: number } = {},
): Promise<ReaderSnapshot> {
  const sourceDirectory = path.join(extensionPath, 'dist', 'reader');
  const attempts = options.attempts ?? SNAPSHOT_ATTEMPTS;
  const retryMs = options.retryMs ?? SNAPSHOT_RETRY_MS;
  let failure: BundleFailure = { cause: 'bundle-unavailable' };

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await trySnapshot(sourceDirectory);
    } catch (error) {
      const candidate = error && typeof error === 'object'
        ? (error as { bundleFailure?: BundleFailure }).bundleFailure
        : undefined;
      failure = candidate ?? {
        cause: error instanceof SyntaxError
          ? 'manifest-json-invalid'
          : error instanceof Error
            ? bounded(error.message)
            : 'bundle-unavailable',
      };
      if (attempt < attempts) await delay(retryMs);
    }
  }

  const manifest = failure.manifest ?? {};
  throw new ReaderBundleError({
    extensionVersion: manifest.extensionVersion ?? 'unavailable',
    adapterVersion: manifest.adapterVersion ?? 'unavailable',
    registryVersion: manifest.registryVersion ?? 'unavailable',
    generationId: manifest.generationId ?? 'unavailable',
    manifestPath: path.join(sourceDirectory, BUNDLE_MANIFEST),
    validationCause: bounded(failure.cause),
    ...(failure.file ? { assetPath: path.join(sourceDirectory, failure.file) } : {}),
    ...(failure.expectedHash ? { expectedHash: failure.expectedHash } : {}),
    ...(failure.actualHash ? { actualHash: failure.actualHash } : {}),
    attempts,
  });
}

export async function removeReaderSnapshot(snapshot: ReaderSnapshot | null): Promise<void> {
  if (!snapshot) return;
  await rm(snapshot.directory, { recursive: true, force: true });
}
