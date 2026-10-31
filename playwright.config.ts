import { defineConfig } from '@playwright/test';

// Playwright enables FORCE_COLOR in child workers; avoid Node's conflicting-color warning.
delete process.env.NO_COLOR;

export default defineConfig({
  testDir: './tests/browser',
  // Each test owns its backend, port and temporary state. Hosted runners get fewer workers.
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  // CI stops at the first failure; local runs report every failure.
  maxFailures: process.env.CI ? 1 : 0,
  // Upper bounds with deliberate margin; see docs/verification.md. A passing test never waits them out.
  timeout: 1_800_000,
  expect: { timeout: 60_000 },
  use: {
    browserName: 'chromium',
    channel: 'msedge',
    headless: true,
    screenshot: 'only-on-failure',
  },
});
