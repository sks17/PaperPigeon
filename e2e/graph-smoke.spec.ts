import { test, expect, type ConsoleMessage, type Response } from '@playwright/test';

/**
 * P1-T09 — Smoke test proving the EXISTING graph still renders.
 *
 * Additive guard: this only observes the current app; it changes nothing.
 * The main thread wires Playwright (config + baseURL + webServer); this spec
 * navigates to the app root via the configured baseURL ('/').
 *
 * Selectors / signals used (documented per task contract):
 *   - Graph data request: GET '/api/graph/data' (src/services/dynamodb.ts → fetchGraphData()).
 *   - Graph canvas container: '#main-content' — the <div ref> in
 *     src/components/ResearchNetworkGraph.tsx that hosts the graph.
 *   - Rendered canvas: '#main-content canvas' — 3d-force-graph (three.js) injects a
 *     <canvas> into that container once graph data is loaded and the graph initializes.
 *   - Console errors: collected from page 'console' events of type 'error' plus
 *     uncaught 'pageerror' exceptions.
 */
test('existing research graph renders from /api/graph/data', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg: ConsoleMessage) => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
    }
  });
  page.on('pageerror', (err: Error) => {
    consoleErrors.push(`pageerror: ${err.message}`);
  });

  // Capture the graph data response triggered on app mount.
  const graphResponsePromise: Promise<Response> = page.waitForResponse(
    (res) => res.url().includes('/api/graph/data') && res.request().method() === 'GET',
  );

  await page.goto('/');

  // The graph data request must succeed and return a non-empty nodes array.
  const graphResponse = await graphResponsePromise;
  expect(graphResponse.ok()).toBeTruthy();

  const graphData = await graphResponse.json();
  expect(Array.isArray(graphData.nodes)).toBeTruthy();
  expect(graphData.nodes.length).toBeGreaterThan(0);

  // The graph container appears (loading screen has been replaced by the graph view).
  await expect(page.locator('#main-content')).toBeVisible();

  // 3d-force-graph injects a <canvas> once the graph initializes with the data.
  await expect(page.locator('#main-content canvas')).toBeVisible();

  // No uncaught console errors during initial render.
  expect(consoleErrors, `Unexpected console errors:\n${consoleErrors.join('\n')}`).toEqual([]);
});
