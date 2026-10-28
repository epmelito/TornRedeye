import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  input: 'stock.html',
  publicDir: false,
  build: { assetsDir: 'stock-assets', assetsInlineLimit: 0 },
});
