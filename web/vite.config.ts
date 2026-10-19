import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// 部署在子路径时（如反代到 /reelay/）构建前设置 VITE_BASE_PATH；路由与 API 前缀都取 import.meta.env.BASE_URL。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.VITE_BASE_PATH || '/',
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:28080',
        changeOrigin: true,
      },
    },
  },
});
