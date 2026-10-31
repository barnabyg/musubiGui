import { test, expect } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { localBackend } from './backend';
import { datasetHarness } from './datasetHarness';

test('navigation has four distinct matching icons and icon-only buttons have names', async ({
  page,
}) => {
  const root = await mkdtemp(path.join(tmpdir(), 'musubi-shell-'));
  const { url, start, stop } = await localBackend({
    state: path.join(root, 'state'),
    readyRoute: '/api/settings',
    cwd: root,
    env: { PYTHONPATH: process.cwd() },
  });
  try {
    await start();
    await page.goto(url);
    const icons = page.locator('nav svg');
    await expect(icons).toHaveCount(4);
    const shapes = await icons.evaluateAll((elements) =>
      elements.map((element) => ({
        drawing: element.innerHTML,
        hidden: element.getAttribute('aria-hidden'),
        width: element.getBoundingClientRect().width,
        height: element.getBoundingClientRect().height,
        stroke: getComputedStyle(element).strokeWidth,
      })),
    );
    expect(new Set(shapes.map((shape) => shape.drawing)).size).toBe(4);
    for (const shape of shapes)
      expect(shape).toMatchObject({
        hidden: 'true',
        width: shapes[0].width,
        height: shapes[0].height,
        stroke: shapes[0].stroke,
      });
    await expect(
      page.getByRole('button', { name: 'Runs', exact: true }),
    ).toBeVisible();
  } finally {
    await stop();
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
});

test('the dataset editor uses icons with accessible names, not text glyphs', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page.getByLabel('Dataset name', { exact: true }).fill('Icons');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Use existing folder' }).click();
    for (const name of ['Back to datasets', 'Dataset settings']) {
      const button = page.getByRole('button', { name, exact: true });
      await expect(button.locator('svg')).toHaveCount(1);
      await expect(button).toHaveText('');
    }
    await page.getByRole('button', { name: 'Dataset settings' }).click();
    const close = page.getByRole('button', { name: 'Close settings' });
    await expect(close.locator('svg')).toHaveCount(1);
    await expect(close).toHaveText('');
  } finally {
    await app.cleanup();
  }
});
