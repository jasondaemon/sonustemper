import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
export default defineConfig({
  plugins: [svelte()],
  base: '/static/album-workspace/',
  build: { outDir: '../sonustemper-ui/app/static/album-workspace', emptyOutDir: true }
});
