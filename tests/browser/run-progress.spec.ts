import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace } from './runHarness';

test('live progress steps through stages, reports steps and loss, and shows a failed stage', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const saved = (await (
      await fetch(`${app.url}/api/jobs/${job.id}`)
    ).json()) as {
      name: string;
      family: string;
      datasets: unknown[];
      revision: number;
      config: Record<string, unknown>;
    };
    await fetch(`${app.url}/api/jobs/${job.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...saved,
        version: undefined,
        id: undefined,
        config: { ...saved.config, steps: 6, save_every: 6 },
      }),
    });
    await app.post('/api/runs/prepare', { job_id: job.id });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r2`, exact: true })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const steps = page.getByRole('list', { name: 'Run stages' });
    const step = (index: number) => steps.getByRole('listitem').nth(index);
    await expect(step(0)).toHaveClass('completed');
    await expect(step(0)).toContainText('Caching');
    await expect(step(1)).toHaveClass('pending');
    await expect(step(3)).toContainText('Done');
    await expect(page.getByLabel('Training progress')).toHaveCount(0);

    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(step(1)).toHaveAttribute('aria-current', 'step');
    const progress = page.getByLabel('Training progress');
    await expect(progress).toContainText(/Step [1-5]\/6/);
    await expect(progress).toContainText('Loss 0.0');
    // The fixture reports no timing, so both reported and estimated ETA stay unknown.
    await expect(
      page.getByLabel('Training ETA', { exact: true }),
    ).toContainText('Unknown');
    await expect(
      page.getByLabel('Estimated training remaining', { exact: true }),
    ).toContainText('Unknown');
    await expect(page.getByLabel('Training steps')).toHaveAttribute('max', '6');
    await expect(page.getByLabel('Run logs')).toBeHidden();

    await expect(step(3)).toHaveClass('completed', { timeout: 240_000 });
    await expect(step(1)).toHaveClass('completed');
    await expect(step(2)).toHaveClass('completed');
    await expect(progress).toContainText('Step 6/6');
    await expect(progress).not.toContainText(/ETA|remaining to max steps/);

    // A failed stage is obvious without opening the log, which then opens by itself.
    await writeFile(path.join(app.fixture, 'checkout', 'fail-train'), '');
    const other = app.manifest.jobs.find(
      (item) => item.family === 'wan' && item.id !== job.id,
    )!;
    await app.post('/api/runs/prepare', { job_id: other.id });
    await page
      .getByRole('button', { name: 'Back to runs', exact: true })
      .click();
    await page
      .getByRole('button', { name: `${other.name} · WAN · r1`, exact: true })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(step(1)).toHaveClass('failed', { timeout: 240_000 });
    await expect(step(1)).toContainText('Exit code 3');
    await expect(step(3)).toHaveClass('pending');
    await expect(
      page.getByText('Waiting for the first training step.'),
    ).toBeVisible();
    await expect(page.getByLabel('Run logs')).toContainText(
      'Deliberate fixture failure',
    );
    await expect(page.getByLabel('Run logs')).toBeVisible();
  } finally {
    await app.close();
  }
});

test('Prepare and start keeps going with the tab closed, and the tab title signals a ready Run', async ({
  page,
}) => {
  const app = await runWorkspace();
  let held = false;
  try {
    const [first, second] = app.manifest.jobs.filter(
      (item) => item.family === 'wan',
    );
    const openJob = async (name: string, target = page) => {
      await target.goto(app.url);
      await target.getByRole('button', { name: 'Jobs', exact: true }).click();
      await target
        .getByRole('button', { name: `${name} · WAN · r1`, exact: true })
        .click();
      await target
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
    };

    // Prepare alone stops at prepared, and says so in the title from any screen.
    await openJob(first.name);
    await page
      .getByRole('button', { name: 'Prepare only', exact: true })
      .click();
    await expect(page).toHaveTitle(`Ready: ${first.name} — Musubi`, {
      timeout: 240_000,
    });
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await expect(page).not.toHaveTitle(/Ready/);

    await openJob(second.name);
    // Keep preparation queued until the tab closes; a fast fixture can otherwise finish
    // before the browser ever observes the transient waiting state.
    await app.leaseGpu('acquire');
    held = true;
    await page
      .getByRole('button', { name: 'Prepare and start', exact: true })
      .click();
    await expect(
      page.getByText('State: Waiting to prepare', { exact: true }),
    ).toBeVisible();
    await page.close();
    await app.leaseGpu('release');
    held = false;
    await expect
      .poll(
        async () =>
          (await app.records()).find((run) => run.job.id === second.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('completed');

    // Failed preparation never reaches training.
    await writeFile(
      path.join(app.fixture, 'checkout', 'fail-latent_cache'),
      '',
    );
    const h3 = app.manifest.jobs.find((item) => item.family === 'h3')!;
    const failed = (await app.post('/api/runs/prepare', {
      job_id: h3.id,
      start: true,
    })) as { id: string };
    await expect
      .poll(
        async () =>
          (await app.records()).find((run) => run.id === failed.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('failed');
    const record = (await app.records()).find((run) => run.id === failed.id)!;
    expect(record.stages.find((stage) => stage.name === 'train')?.status).toBe(
      'pending',
    );
  } finally {
    if (held) await app.leaseGpu('release');
    await app.close();
  }
});

test('Runs read as labels and local times, and the GPU line links to the Run using it', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await app.post('/api/runs/prepare', { job_id: job.id });
    await expect
      .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
      .toBe('prepared');
    const [run] = await app.records();
    await page.route('**/api/runs/capabilities', (route) =>
      route.fulfill({
        json: { fixture: true, gpu_owner: { run_id: run.id } },
      }),
    );
    await page.goto(`${app.url}/#/runs`);
    const gpu = page.getByRole('status');
    await expect(gpu).toHaveText(`GPU in use by ${job.name}`);
    const history = page.locator('section', {
      has: page.getByRole('heading', { name: 'Run history' }),
    });
    await expect(history.locator('.status-badge')).toHaveText('Ready to start');
    await expect(history).not.toContainText('+00:00');
    await expect(history).not.toContainText('prepared');
    await gpu.getByRole('link', { name: job.name }).click();
    await expect(page).toHaveURL(new RegExp(`#/runs/${run.id}$`));

    await expect(
      page.getByText(`Run ${run.id.slice(0, 8)}`, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(run.id, { exact: true })).toBeHidden();
    await page.getByText('Full Run ID').click();
    await expect(page.getByText(run.id, { exact: true })).toBeVisible();
    await expect(
      page.getByRole('status').filter({ hasText: /GPU/ }),
    ).toHaveCount(0);
    await page.getByRole('button', { name: 'Back to runs' }).click();

    // Work outside this instance has no Run to name.
    await page.unroute('**/api/runs/capabilities');
    await page.route('**/api/runs/capabilities', (route) =>
      route.fulfill({
        json: { fixture: true, gpu_owner: { run_id: 'other-work' } },
      }),
    );
    await expect(gpu).toHaveText('GPU in use by other work');
  } finally {
    await app.close();
  }
});
