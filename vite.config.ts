import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { nodePolyfills } from 'vite-plugin-node-polyfills'
export default defineConfig({
  define: { 'process.env.NODE_ENV': JSON.stringify('production'), appName: JSON.stringify('exapp_events'), appVersion: JSON.stringify('0.1.0') },
  plugins: [vue(), nodePolyfills({ include: ['process', 'stream', 'buffer', 'events', 'util', 'path', 'string_decoder'] })],
  build: {
    outDir: 'ex_app/js', emptyOutDir: true,
    lib: { entry: 'ex_app/src/main.ts', name: 'ExappEvents', formats: ['iife'], fileName: () => 'events-main.js', cssFileName: 'events-main' },
    cssCodeSplit: false,
  },
})
