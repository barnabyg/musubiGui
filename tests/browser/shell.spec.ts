import { test, expect } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { localBackend } from './backend';
import { datasetHarness } from './datasetHarness';
import { runWorkspace } from './runHarness';

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

test('each view has one primary action, destructive actions look dangerous and focus always shows', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    const primaries = page.locator('button.primary:visible');
    const named = (name: string) =>
      page.getByRole('button', { name, exact: true });

    // The primary action follows the job through its workflow.
    await expect(primaries).toHaveText(['Review configuration']);
    await page.getByLabel('Job name').fill(`${job.name} edited`);
    await expect(primaries).toHaveText(['Save draft']);
    await named('Save draft').click();
    await expect(primaries).toHaveText(['Review configuration']);
    await named('Review configuration').click();
    await expect(primaries).toHaveText(['Prepare and start']);

    await named('More job actions').click();
    await expect(
      page.getByRole('menuitem', { name: 'Delete job', exact: true }),
    ).toHaveClass(/danger/);
    await page.keyboard.press('Escape');

    // Keyboard focus is visible on every stop through the form.
    await page.getByLabel('Job name').focus();
    for (let stop = 0; stop < 12; stop += 1) {
      await page.keyboard.press('Tab');
      const focus = await page.evaluate(() => {
        const element = document.activeElement!;
        const style = getComputedStyle(element);
        return {
          tag: element.tagName,
          outline: style.outlineStyle,
          width: style.outlineWidth,
        };
      });
      expect(focus.outline, focus.tag).toBe('solid');
      expect(focus.width, focus.tag).toBe('2px');
    }

    await named('Prepare run').click();
    await expect(page.getByLabel('Run stages')).toBeVisible();
    await expect(named('Delete run')).toHaveClass(/danger/);
    await expect
      .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
      .toBe('prepared');
    await expect(primaries).toHaveText(['Start training']);
  } finally {
    await app.close();
  }
});

test('a configured app opens on Datasets, or on a Run in progress', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    await page.goto(app.url);
    await expect(
      page.getByRole('heading', { name: 'Datasets', exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/#\/datasets$/);
    await expect(
      page.getByRole('button', { name: 'Datasets', exact: true }),
    ).toHaveClass('active');

    // Choosing a screen before the opening one is decided leaves no blank entry to go back to.
    await page.goto('about:blank');
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/api/settings', async (route) => {
      await held;
      await route.continue().catch(() => undefined);
    });
    await page.goto(app.url);
    const entries = await page.evaluate(() => window.history.length);
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await expect(page).toHaveURL(/#\/jobs$/);
    expect(await page.evaluate(() => window.history.length)).toBe(entries);
    release();
    await page.unroute('**/api/settings');

    // A link that names nothing opens the same place.
    await page.goto('about:blank');
    await page.goto(`${app.url}/#/jobs/not.a.job`);
    await expect(page).toHaveURL(/#\/datasets$/);

    // A link still opens what it names.
    await page.goto(`${app.url}/#/settings`);
    await expect(
      page.getByRole('heading', { name: 'Settings', exact: true }),
    ).toBeVisible();

    // Waiting for the GPU keeps a Run active for as long as this takes.
    await app.leaseGpu('acquire');
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await app.post('/api/runs/prepare', { job_id: job.id });
    const [run] = await app.records();
    await page.goto(app.url);
    await page.reload();
    await expect(page).toHaveURL(new RegExp(`#/runs/${run.id}$`));
    await expect(page.getByLabel('Run stages')).toBeVisible();
    await app.leaseGpu('release');

    // Newest first in the list, but the Run that is working comes first on opening.
    await page.route('**/api/runs', (route) =>
      route.fulfill({
        json: [
          {
            ...run,
            id: 'waiting',
            status: 'pending_training',
            order: 2,
          },
          { ...run, status: 'training', order: 1 },
        ],
      }),
    );
    await page.goto('about:blank');
    await page.goto(app.url);
    await expect(page).toHaveURL(new RegExp(`#/runs/${run.id}$`));
    await page.unroute('**/api/runs');
  } finally {
    await app.close();
  }
});
