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
    await page
      .getByRole('radio', { name: 'Use existing folder', exact: true })
      .check();
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page
      .getByRole('checkbox', { name: 'Index this entire dataset root' })
      .check();
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

    // Tabs and their fields retain the app's visible keyboard focus treatment.
    const presets = page.getByRole('tab', { name: 'Presets', exact: true });
    await presets.focus();
    await page.keyboard.press('Enter');
    await expect(presets).toBeFocused();
    await expect(presets).toHaveCSS('outline-style', 'solid');
    const preset = page.getByLabel('Preset name', { exact: true });
    await preset.focus();
    await expect(preset).toBeFocused();
    await expect(preset).toHaveCSS('outline-style', 'solid');
    await expect(preset).toHaveCSS('outline-width', '2px');

    await named('Prepare only').click();
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

test('collection navigation separates selection and creation from dataset, job and Run details', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const button = (name: string) =>
      page.getByRole('button', { name, exact: true });
    await page.goto(`${app.url}/#/jobs`);
    await expect(button('New Wan job')).toBeVisible();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      job.name,
    );
    await expect(page.getByRole('table', { name: 'Saved jobs' })).toHaveCount(
      0,
    );
    await expect(button('New Wan job')).toHaveCount(0);
    await button('Back to jobs').click();
    await expect(page).toHaveURL(/#\/jobs$/);
    await expect(
      page.getByRole('tablist', { name: 'Job sections' }),
    ).toHaveCount(0);
    await page.goBack();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      job.name,
    );
    await button('Jobs').click();
    await expect(page.getByRole('table', { name: 'Saved jobs' })).toBeVisible();

    await button('Datasets').click();
    await expect(
      page.getByRole('region', { name: 'Dataset library' }),
    ).toBeVisible();
    await page.getByRole('button', { name: /^WAN acceptance/ }).click();
    await expect(button('Back to datasets')).toBeVisible();
    await expect(
      page.getByRole('region', { name: 'Dataset library' }),
    ).toHaveCount(0);
    await expect(button('New dataset')).toHaveCount(0);
    await button('Datasets').click();
    await expect(page).toHaveURL(/#\/datasets$/);
    await expect(
      page.getByRole('region', { name: 'Dataset library' }),
    ).toBeVisible();

    const run = (await app.post('/api/runs/prepare', { job_id: job.id })) as {
      id: string;
    };
    await button('Runs').click();
    await expect(
      page.getByRole('heading', { name: 'Run history' }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`#/runs/${run.id}$`));
    await expect(page.getByLabel('Run stages')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Run history' }),
    ).toHaveCount(0);
    await expect(
      page.getByRole('heading', { name: 'Pending queue' }),
    ).toHaveCount(0);
    await page.reload();
    await expect(page.getByLabel('Run stages')).toBeVisible();
    await button('Back to runs').click();
    await expect(page).toHaveURL(/#\/runs$/);
    await expect(page.getByLabel('Run stages')).toHaveCount(0);
    await expect(
      page.getByRole('link', { name: 'Open jobs to prepare a Run' }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});
