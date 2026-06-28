import path from "path"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // A-Frame bundles its own three fork ("super-three"); 3d-force-graph-vr (and three-render-objects)
    // import vanilla "three". Two different THREE classes means A-Frame's `setObject3D` rejects the
    // graph's objects (`instanceof THREE.Object3D` fails) and the VR scene renders EMPTY. Routing
    // "three" -> "super-three" gives one THREE instance everywhere. (Object-form alias matches `three`
    // and `three/*` only — NOT `three-render-objects` — and resolves the target as a module.)
    alias: {
      "@": path.resolve(__dirname, "./src"),
      three: "super-three",
    },
    dedupe: ["super-three"],
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
