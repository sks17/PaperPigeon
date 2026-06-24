import { defineConfig } from '@playwright/test';

/**
 * Phase-1 e2e (P1-T09): prove the existing graph renders off the NEW backend.
 *
 * Prereq: start the API stack first ->  .venv/Scripts/python.exe scripts/run_local_stack.py
 * (boots no-Docker Postgres + FastAPI on :8000). This config starts Vite dev (:5173), which
 * proxies /api -> :8000 via vite.config.ts. Then:  pnpm test:e2e
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
