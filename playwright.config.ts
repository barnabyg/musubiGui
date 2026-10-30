import { defineConfig } from '@playwright/test';

// Playwright enables FORCE_COLOR in child workers; avoid Node's conflicting-color warning.
delete process.env.NO_COLOR;

export default defineConfig({
  testDir: './tests/browser',
  workers: 1,
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
