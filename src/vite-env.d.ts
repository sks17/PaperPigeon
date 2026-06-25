/// <reference types="vite/client" />

// Optional override for the API origin. Default '' (empty) keeps all calls relative — same-origin
// on Vercel in prod, and through the Vite dev proxy locally. Set at build time (e.g.
// VITE_API_BASE_URL=https://paper-pigeon-api.fly.dev) to point the frontend at the new backend.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
