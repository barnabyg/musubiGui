import { test, expect } from '@playwright/test';
import { writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace } from './runHarness';

test('Wan and H3 prepare and train through the GUI with frozen captions cache reuse and playable outputs', async ({
  page,
}) => {
  // Both families prepare, train, decode samples and inspect saved state.
  test.setTimeout(120_000);
  const app = await runWorkspace();
  try {
    await page.goto(app.url);
    for (const family of ['wan', 'h3']) {
      const job = app.manifest.jobs.find((job) => job.family === family)!;
      await page.getByRole('button', { name: 'Jobs', exact: true }).click();
      await page
        .getByRole('button', {
          name: `${job.name} · ${family.toUpperCase()} · r1`,
          exact: true,
        })
        .click();
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      await page
        .getByRole('button', { name: 'Prepare run', exact: true })
        .click();
      await expect(
        page.getByText('State: prepared', { exact: true }),
      ).toBeVisible({ timeout: 45_000 });
      const first = (await app.records()).find((run) => run.job.id === job.id)!;
      const originalCaption = first.manifest.items[0].caption.text;
      const captionPath = path.join(
        app.fixture,
        'datasets',
        family,
        'motion.txt',
      );
      await writeFile(captionPath, 'A changed caption — 新しい音.', 'utf8');
      await page.getByRole('button', { name: 'Jobs', exact: true }).click();
      await page
        .getByRole('button', {
          name: `${job.name} · ${family.toUpperCase()} · r1`,
          exact: true,
        })
        .click();
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      await page
        .getByRole('button', { name: 'Prepare run', exact: true })
        .click();
      await expect(
        page.getByText('State: prepared', { exact: true }),
      ).toBeVisible({ timeout: 45_000 });
      const next = (await app.records()).find(
        (run) => run.job.id === job.id && run.id !== first.id,
      )!;
      expect(
        next.stages.find((stage) => stage.name === 'latent_cache')?.status,
      ).toBe('reused');
      expect(
        next.stages.find((stage) => stage.name === 'text_cache')?.status,
      ).toBe('completed');
      const frozen = JSON.parse(
        await readFile(path.join(first.directory, 'inputs.json'), 'utf8'),
      ) as { manifest: { items: { caption: { text: string } }[] } };
      expect(frozen.manifest.items[0].caption.text).toBe(originalCaption);
      await page
        .getByRole('button', { name: 'Start training', exact: true })
        .click();
      await page.reload();
      await page.getByRole('button', { name: 'Runs', exact: true }).click();
      await page
        .getByRole('button', {
          name: `${job.name} · ${family.toUpperCase()} · r1`,
          exact: true,
        })
        .first()
        .click();
      await expect(
        page.getByText('State: completed', { exact: true }),
      ).toBeVisible({ timeout: 45_000 });
      await page
        .getByRole('button', { name: 'Refresh outputs', exact: true })
        .click();
      await page
        .getByRole('button', {
          name: 'samples/sample.mp4 · available',
          exact: true,
        })
        .click();
      const video = page.getByLabel('Output video', { exact: true });
      await video.evaluate(async (element) => {
        const media = element as HTMLVideoElement;
        media.muted = true;
        await media.play();
      });
      await expect
        .poll(() =>
          video.evaluate(
            (element) => (element as HTMLVideoElement).currentTime,
          ),
        )
        .toBeGreaterThan(0);
      const inspect = page.getByRole('button', {
        name: 'Inspect saved states',
        exact: true,
      });
      // Hold the hashing response to observe the busy state, then fail one request.
      let release = () => {};
      const held = new Promise<void>((resolve) => (release = resolve));
      let requests = 0;
      await page.route('**/api/runs/*/states', async (route) => {
        requests += 1;
        await held;
        if (requests === 1) await route.continue();
        else
          await route.fulfill({
            status: 500,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'State inspection failed.' }),
          });
      });
      await inspect.click();
      await expect(inspect).toBeDisabled();
      await expect(
        page.getByText('Checking saved states…', { exact: true }),
      ).toBeVisible();
      await inspect.click({ force: true });
      release();
      await expect(page.getByText(/optimizer step 2/)).toBeVisible();
      await expect(inspect).toBeEnabled();
      expect(requests).toBe(1);
      await inspect.click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'State inspection failed.' }),
      ).toBeVisible();
      await expect(inspect).toBeEnabled();
      await expect(
        page.getByText('Checking saved states…', { exact: true }),
      ).toBeHidden();
      await page.unroute('**/api/runs/*/states');
    }
  } finally {
    await app.close();
  }
});

test('backend interruption retains GPU ownership and offers explicit recovery without duplicate training', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs[0];
    const saved = (await (
      await fetch(`${app.url}/api/jobs/${job.id}`)
    ).json()) as {
      name: string;
      family: string;
      dataset_id: string;
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
        config: { ...saved.config, steps: 50 },
      }),
    });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r2`, exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect(
      page.getByText('State: prepared', { exact: true }),
    ).toBeVisible({ timeout: 45_000 });
    const run = (await app.records())[0];
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: training', { exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('Run logs')).toContainText(
      'SYNTHETIC ACCEPTANCE FIXTURE: train',
    );
    await app.crash();
    await app.start();
    await page.reload();
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r2`, exact: true })
      .click();
    await expect(
      page.getByText('State: orphaned', { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('status')).toContainText(run.id);
    expect(await app.records()).toHaveLength(1);
    page.once('dialog', (dialog) => void dialog.accept());
    await page
      .getByRole('button', { name: 'Terminate recovered work', exact: true })
      .click();
    await expect(
      page.getByText('State: interrupted', { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('status')).toHaveText('GPU available');
  } finally {
    await app.close();
  }
});

test('a stopped training run shows the final status of its stopped stage', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs[0];
    const saved = (await (
      await fetch(`${app.url}/api/jobs/${job.id}`)
    ).json()) as {
      name: string;
      family: string;
      dataset_id: string;
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
        config: { ...saved.config, steps: 50 },
      }),
    });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r2`, exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect(
      page.getByText('State: prepared', { exact: true }),
    ).toBeVisible({ timeout: 45_000 });
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(page.getByLabel('Run logs')).toContainText(
      'SYNTHETIC ACCEPTANCE FIXTURE: train',
    );
    page.once('dialog', (dialog) => void dialog.accept());
    await page
      .getByRole('button', { name: 'Force terminate', exact: true })
      .click();
    await expect(
      page.getByText('State: stopped', { exact: true }),
    ).toBeVisible();
    await expect(
      page.locator('p', { hasText: /^train: stopped/ }),
    ).toBeVisible();
    await expect(page.getByRole('status')).toHaveText('GPU available');
  } finally {
    await app.close();
  }
});

test('a gracefully stopped revision can be prepared and started again', async ({
  page,
}) => {
  test.setTimeout(120_000);
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs[0];
    const saved = (await (
      await fetch(`${app.url}/api/jobs/${job.id}`)
    ).json()) as {
      name: string;
      family: string;
      dataset_id: string;
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
        config: { ...saved.config, steps: 50 },
      }),
    });
    const prepare = async () => {
      await page.getByRole('button', { name: 'Jobs', exact: true }).click();
      await page
        .getByRole('button', { name: `${job.name} · WAN · r2`, exact: true })
        .click();
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      await page
        .getByRole('button', { name: 'Prepare run', exact: true })
        .click();
    };
    await page.goto(app.url);
    await prepare();
    await expect(
      page.getByText('State: prepared', { exact: true }),
    ).toBeVisible({ timeout: 45_000 });
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(page.getByLabel('Run logs')).toContainText(
      'SYNTHETIC ACCEPTANCE FIXTURE: train',
    );
    await page
      .getByRole('button', { name: 'Request graceful stop', exact: true })
      .click();
    await expect(page.getByText('State: stopped', { exact: true })).toBeVisible(
      { timeout: 45_000 },
    );
    const stopped = (await app.records()).find((run) => run.job.id === job.id)!;

    // The stopped run no longer blocks preparing the same revision.
    await prepare();
    await expect(
      page.getByText('State: prepared', { exact: true }),
    ).toBeVisible({ timeout: 45_000 });
    const fresh = (await app.records()).find(
      (run) => run.job.id === job.id && run.id !== stopped.id,
    )!;
    expect(fresh.fingerprint).toBe(stopped.fingerprint);
    expect(
      fresh.stages.find((stage) => stage.name === 'latent_cache')?.status,
    ).toBe('reused');

    // Preparing again returns the new run and says so on the Jobs screen.
    await prepare();
    await expect(
      page.getByText(/already has a prepared run with the same inputs/),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Open existing run', exact: true })
      .click();
    await expect(
      page.getByText('State: prepared', { exact: true }),
    ).toBeVisible();
    expect(
      (await app.records()).filter((run) => run.job.id === job.id),
    ).toHaveLength(2);
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(page.getByLabel('Run logs')).toContainText(
      'SYNTHETIC ACCEPTANCE FIXTURE: train',
    );
    page.once('dialog', (dialog) => void dialog.accept());
    await page
      .getByRole('button', { name: 'Force terminate', exact: true })
      .click();
    await expect(
      page.getByText('State: stopped', { exact: true }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('low disk space warns in review and preparation still goes ahead', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const app = await runWorkspace({ freeBytes: 1000 });
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const estimate = page.getByTestId('disk-estimate');
    await expect(estimate).toContainText('Optimizer states');
    await expect(estimate).toContainText('short');
    const volume = path.parse(app.parent).root;
    await expect(estimate).toContainText(volume);
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(review).toContainText('Ready for preparation');
    await expect(review.getByText(/^Low disk space on /)).toContainText(volume);

    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect(
      page.getByText('State: prepared', { exact: true }),
    ).toBeVisible({ timeout: 45_000 });
  } finally {
    await app.close();
  }
});

test('archiving hides a job, keeps its Runs browsable and restores it', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const library = page.getByRole('button', {
      name: `${job.name} · WAN · r1`,
      exact: true,
    });
    const jobsTab = page.getByRole('button', { name: 'Jobs', exact: true });
    const archiveButton = page.getByRole('button', {
      name: 'Archive job',
      exact: true,
    });
    await page.goto(app.url);
    await app.leaseGpu('acquire');
    await jobsTab.click();
    await library.click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect
      .poll(async () => (await app.records())[0]?.status)
      .toBe('pending_preparation');

    // A queued Run keeps its job in the list, and the refusal says why.
    await jobsTab.click();
    await library.click();
    await archiveButton.click();
    await expect(page.getByRole('alert')).toContainText('active Run');
    await expect(library).toBeVisible();

    await app.leaseGpu('release');
    await expect
      .poll(async () => (await app.records())[0]?.status, { timeout: 45_000 })
      .toBe('prepared');
    const [run] = await app.records();

    await archiveButton.click();
    await expect(library).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Archive job', exact: true }),
    ).toHaveCount(0);

    // Still archived after a reload, and its Run stays browsable from Runs.
    await page.reload();
    await jobsTab.click();
    await expect(library).toHaveCount(0);
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: new RegExp(`^${job.name} · WAN · r`) })
      .first()
      .click();
    await expect(page.getByRole('heading', { name: job.name })).toBeVisible();
    await expect(
      page.getByText('State: prepared', { exact: true }),
    ).toBeVisible();
    expect((await app.records()).map((record) => record.id)).toEqual([run.id]);

    // The Run has its own frozen snapshot, so it can still train.
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: completed', { exact: true }),
    ).toBeVisible({ timeout: 45_000 });

    await jobsTab.click();
    await page.getByText('Archived jobs (1)', { exact: true }).click();
    await page
      .getByRole('button', { name: `Restore ${job.name}`, exact: true })
      .click();
    await expect(library).toBeVisible();
    await expect(page.getByText('Archived jobs (1)')).toHaveCount(0);
  } finally {
    await app.close();
  }
});
