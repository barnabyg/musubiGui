import { defineConfig } from '@playwright/test';

// Playwright enables FORCE_COLOR in child workers; avoid Node's conflicting-color warning.
delete process.env.NO_COLOR;

export default defineConfig({
  testDir: './tests/browser',
  workers: 1,
  timeout: 60_000,
  // Real backend/media subprocesses on hosted Windows runners can outlast 5 s.
  expect: { timeout: 15_000 },
  use: { browserName: 'chromium', channel: 'msedge', headless: true },
});
