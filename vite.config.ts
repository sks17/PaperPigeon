import path from "path"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  optimizeDeps: {
    include: ["aframe", "3d-force-graph-vr"],
  },
  // Dev-only: proxy /api to the new Repopulation API (FastAPI) so the existing frontend renders
  // off the new backend with zero code change. Affects `vite dev` only; production build ignores it.
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
})
