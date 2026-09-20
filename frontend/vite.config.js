import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server for the Smart Member Growth Tracker dashboard. The backend base
// URL is configurable via the VITE_API_BASE_URL env var (see src/api.js); a
// dev proxy is provided so relative "/api/*" calls reach the FastAPI backend
// on localhost:8000 without CORS setup during local development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5176,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
