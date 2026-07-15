import { defineConfig } from 'vite';
import { createExtensionConfig, mergeExtensionConfig } from '@nimbalyst/extension-sdk/vite';

const base = createExtensionConfig({ entry: './src/index.ts' });

export default defineConfig(mergeExtensionConfig(base, {
  // The utility process may be reading packaged Python assets during a hot
  // reload. Keep multi-entry output and replace only the files each build owns.
  build: { emptyOutDir: false },
}));
