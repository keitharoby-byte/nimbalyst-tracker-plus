import { builtinModules } from 'node:module';
import { defineConfig } from 'vite';

const nodeBuiltins = [
  ...builtinModules,
  ...builtinModules.map((moduleName) => `node:${moduleName}`),
];

export default defineConfig({
  build: {
    target: 'node20',
    outDir: 'dist',
    emptyOutDir: false,
    sourcemap: true,
    lib: {
      entry: './src/backend.ts',
      formats: ['es'],
      fileName: () => 'backend.js',
    },
    rollupOptions: {
      external: nodeBuiltins,
    },
  },
});
