import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { access, writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { helper, python } from './backend';
import { runWorkspace } from './runHarness';

const exists = (file: string) =>
  access(file).then(
    () => true,
    () => false,
  );

test('deleting a Run and its Job goes from the UI to the disk and survives a backend restart', async ({
  page,
}) => {
  const app = await runWorkspace();
  let releaseLock = () => {};
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const label = `${job.name} · WAN · r1`;
    const jobButton = page.getByRole('button', { name: label, exact: true });
    const navigate = (name: string) =>
      page.getByRole('button', { name, exact: true }).click();
    const jobAction = async (name: string) => {
      await page.getByRole('button', { name: 'More job actions' }).click();
      await page.getByRole('menuitem', { name, exact: true }).click();
    };
    const button = (name: string) =>
      page.getByRole('button', { name, exact: true });
    await page.goto(app.url);
    await navigate('Jobs');
    await jobButton.click();

    // A Job with a Run is refused with the way forward, before any question is asked.
    await app.post('/api/runs/prepare', { job_id: job.id });
    await jobAction('Delete job');
    await expect(page.getByRole('alert')).toContainText('Archive job');
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await navigate('Runs');
    await jobButton.click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const [run] = await app.records();

    const locked = path.join(run.directory, 'inputs.json');
    const lock = spawn(python, [helper, 'hold-file', locked]);
    releaseLock = () => lock.kill();
    await new Promise((resolve) => lock.stdout.once('data', resolve));

    // A locked file leaves the Run visible, saying what remains.
    await button('Delete run').click();
    const question = page.getByRole('dialog');
    await expect(question).toContainText(run.id.slice(0, 8));
    await expect(question).toContainText(/This frees \d+ (KB|MB|GB)/);
    await expect(question).toContainText("can't be undone");
    await expect(question).toContainText('download any LoRA');
    await question.getByRole('button', { name: 'Delete run' }).click();
    await expect(page.getByLabel('Unfinished delete')).toContainText(
      'inputs.json',
      { timeout: 240_000 },
    );
    await expect(page.getByLabel('Unfinished delete')).toContainText(
      'Still on disk:',
    );
    await expect(
      page.getByText('State: Deleting', { exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('Run outputs')).toHaveCount(0);

    // It is still there, and still deletable, after a restart.
    const closed = new Promise((resolve) => lock.once('exit', resolve));
    lock.stdin.end();
    await closed;
    await app.crash();
    await app.start();
    await page.reload();
    await navigate('Runs');
    await jobButton.click();
    await expect(
      page.getByText('State: Deleting', { exact: true }),
    ).toBeVisible();
    await button('Delete again').click();
    await question.getByRole('button', { name: 'Delete run' }).click();
    await expect(page.getByText(/^Run deleted\. Freed/)).toBeVisible({
      timeout: 240_000,
    });
    await expect(jobButton).toHaveCount(0);
    expect(await exists(run.directory)).toBe(false);
    expect(await exists(path.join(run.directory, '..', '..'))).toBe(true);
    expect(await app.records()).toEqual([]);
    await expect(page.getByRole('alert')).toHaveCount(0);

    // Gone after a restart too, and the Job can now be deleted.
    await app.crash();
    await app.start();
    await page.reload();
    await navigate('Runs');
    await expect(jobButton).toHaveCount(0);
    await navigate('Jobs');
    await jobButton.click();
    await jobAction('Delete job');
    await expect(question).toContainText(job.name);
    await question.getByRole('button', { name: 'Delete job' }).click();
    await expect(jobButton).toHaveCount(0);
    expect(await exists(path.join(app.manifest.state, 'jobs', job.id))).toBe(
      false,
    );

    await app.crash();
    await app.start();
    await page.reload();
    await navigate('Jobs');
    await expect(
      page.getByRole('button', { name: /· H3 · r1$/ }).first(),
    ).toBeVisible();
    await expect(jobButton).toHaveCount(0);
    const jobs = (await (await fetch(`${app.url}/api/jobs`)).json()) as {
      id: string;
    }[];
    expect(jobs.map((item) => item.id)).not.toContain(job.id);

    // An archived Job has the control as well.
    const other = app.manifest.jobs.find((item) => item.family === 'h3')!;
    await app.post(`/api/jobs/${other.id}/archive`);
    await page.reload();
    await navigate('Jobs');
    await page.getByText('Archived jobs (1)', { exact: true }).click();
    await button(`Delete ${other.name}`).click();
    await question.getByRole('button', { name: 'Delete job' }).click();
    await expect(button(`Delete ${other.name}`)).toHaveCount(0);
    expect(await exists(path.join(app.manifest.state, 'jobs', other.id))).toBe(
      false,
    );
  } finally {
    releaseLock();
    await app.close();
  }
});

test('outputs appear when a Run completes and a playing preview survives a refresh', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await app.post('/api/runs/prepare', { job_id: job.id });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(
      page.getByText(/^No supported outputs found yet/),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    const sample = page.getByRole('button', {
      name: 'sample.mp4 · available',
      exact: true,
    });
    await expect(sample).toBeVisible({ timeout: 240_000 });
    await expect(page.getByText(/^No supported outputs found yet/)).toHaveCount(
      0,
    );
    await sample.click();
    const video = page.getByLabel('Output video', { exact: true });
    await video.evaluate(async (element) => {
      const media = element as HTMLVideoElement;
      element.setAttribute('data-kept', 'yes');
      media.muted = true;
      media.loop = true;
      await media.play();
    });
    await page
      .getByRole('button', { name: 'Refresh outputs', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Refresh outputs', exact: true }),
    ).toBeEnabled();
    await expect(sample).toHaveAttribute('aria-pressed', 'true');
    await expect(video).toHaveAttribute('data-kept', 'yes');
    await expect
      .poll(() =>
        video.evaluate((element) => !(element as HTMLVideoElement).paused),
      )
      .toBe(true);
  } finally {
    await app.close();
  }
});

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
    // The fixture reports no ETA, and an unreported value is not estimated.
    await expect(progress).toContainText('ETA unknown');
    await expect(page.getByLabel('Training steps')).toHaveAttribute('max', '6');
    await expect(page.getByLabel('Run logs')).toBeHidden();

    await expect(step(3)).toHaveClass('completed', { timeout: 240_000 });
    await expect(step(1)).toHaveClass('completed');
    await expect(step(2)).toHaveClass('completed');
    await expect(progress).toContainText('Step 6/6');

    // A failed stage is obvious without opening the log, which then opens by itself.
    await writeFile(path.join(app.fixture, 'checkout', 'fail-train'), '');
    const other = app.manifest.jobs.find(
      (item) => item.family === 'wan' && item.id !== job.id,
    )!;
    await app.post('/api/runs/prepare', { job_id: other.id });
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
      page.getByText('Step progress unknown', { exact: false }),
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
      .getByRole('button', { name: 'Prepare run', exact: true })
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
    await page
      .getByRole('button', { name: 'Prepare and start', exact: true })
      .click();
    await expect(
      page.getByText('State: Waiting to prepare', { exact: true }),
    ).toBeVisible();
    await page.close();
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
    await app.close();
  }
});

test('the URL keeps the screen and selection across navigation, refresh and browser history', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const other = app.manifest.jobs.find((item) => item.family === 'h3')!;
    const nav = (name: string) =>
      page.getByRole('button', { name, exact: true });
    const jobName = page.getByLabel('Job name', { exact: true });
    await page.goto(app.url);
    await nav('Jobs').click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await expect(jobName).toHaveValue(job.name);
    expect(new URL(page.url()).hash).toBe(`#/jobs/${job.id}`);

    // Returning through the sidebar and browser back both reopen the job.
    await nav('Runs').click();
    await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
    await nav('Jobs').click();
    await expect(jobName).toHaveValue(job.name);
    await nav('Runs').click();
    await page.goBack();
    await expect(jobName).toHaveValue(job.name);
    await page.goForward();
    await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
    await page.goBack();
    await page.reload();
    await expect(jobName).toHaveValue(job.name);

    // Back moves between selections too.
    await page
      .getByRole('button', { name: `${other.name} · H3 · r1`, exact: true })
      .click();
    await expect(jobName).toHaveValue(other.name);
    await page.goBack();
    await expect(jobName).toHaveValue(job.name);

    // Unsaved edits ask first, on back as on the sidebar.
    await page.getByLabel('Training steps', { exact: true }).fill('33');
    await page.goBack();
    const leave = page.getByRole('dialog', { name: 'Leave this job?' });
    await leave.getByRole('button', { name: 'Keep editing' }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('33');
    expect(new URL(page.url()).hash).toBe(`#/jobs/${job.id}`);
    // Keeping the edits leaves history as it was, including what lay ahead.
    await page.goBack();
    await leave.getByRole('button', { name: 'Keep editing' }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('33');
    await page.goBack();
    await leave.getByRole('button', { name: 'Discard changes' }).click();
    await expect(jobName).toHaveCount(0);
    // Discarding really leaves: the next step back doesn't return to the discarded job.
    await page.goForward();
    await expect(jobName).toHaveValue(job.name);
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).not.toHaveValue('33');
    await page.goBack();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveCount(0);
    await nav('Settings').click();

    // A link to a Run opens it, and a stale link falls back to the list.
    const run = (await app.post('/api/runs/prepare', { job_id: job.id })) as {
      id: string;
    };
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByText(`Run ${run.id.slice(0, 8)}`, { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByText(`Run ${run.id.slice(0, 8)}`, { exact: true }),
    ).toBeVisible();
    await page.goto(`${app.url}/#/runs/${'0'.repeat(32)}`);
    await expect.poll(() => new URL(page.url()).hash).toBe('#/runs');

    // The selected dataset survives a refresh and the trip to another screen.
    await nav('Datasets').click();
    await page.getByRole('button', { name: /^WAN acceptance/ }).click();
    await expect(
      page.getByRole('button', { name: 'Back to datasets' }).first(),
    ).toBeVisible();
    expect(new URL(page.url()).hash).toMatch(/^#\/datasets\/\w+$/);
    await page.reload();
    await expect(
      page.getByRole('button', { name: 'Back to datasets' }).first(),
    ).toBeVisible();
    await page.goBack();
    await expect(
      page.getByRole('button', { name: /^WAN acceptance/ }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('work finishing on one screen does not pull the user back to it', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const run = (await app.post('/api/runs/prepare', { job_id: job.id })) as {
      id: string;
    };
    await expect
      .poll(async () => (await app.records())[0]?.status, { timeout: 240_000 })
      .toBe('prepared');
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**/api/runs/${run.id}/delete`, async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await page.getByRole('button', { name: 'Delete run', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Delete run' })
      .click();
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
    release();
    await expect.poll(async () => (await app.records()).length).toBe(0);
    await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
    expect(new URL(page.url()).hash).toBe('#/jobs');
  } finally {
    await app.close();
  }
});

test('a Run that reused a deleted Run cache still shows it and can be used', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const prepared = async (count: number) =>
      expect
        .poll(
          async () =>
            (await app.records()).filter((run) => run.status === 'prepared')
              .length,
          { timeout: 240_000 },
        )
        .toBe(count);
    await app.post('/api/runs/prepare', { job_id: job.id });
    await prepared(1);
    const [first] = await app.records();
    await writeFile(
      path.join(app.fixture, 'datasets', 'wan', 'motion.txt'),
      'A changed caption.',
      'utf8',
    );
    await app.post('/api/runs/prepare', { job_id: job.id });
    await prepared(2);

    await page.goto(app.url);
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    const runs = page.getByRole('button', {
      name: `${job.name} · WAN · r1`,
      exact: true,
    });
    await runs.last().click();
    await expect(
      page.getByText(`Run ${first.id.slice(0, 8)}`, { exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Delete run', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Delete run' })
      .click();
    await expect(page.getByText(/^Run deleted\. Freed/)).toBeVisible({
      timeout: 240_000,
    });

    await expect(runs).toHaveCount(1);
    await runs.click();
    await expect(page.getByText('verified cache reused')).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    expect(await exists(first.directory)).toBe(false);
  } finally {
    await app.close();
  }
});

test('Wan and H3 prepare and train through the GUI with frozen captions cache reuse and playable outputs', async ({
  page,
}) => {
  // Both families prepare, train, decode samples and inspect saved state.
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
        page.getByText('State: Ready to start', { exact: true }),
      ).toBeVisible({ timeout: 240_000 });
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
        page.getByText('State: Ready to start', { exact: true }),
      ).toBeVisible({ timeout: 240_000 });
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
        page.getByText('State: Completed', { exact: true }),
      ).toBeVisible({ timeout: 240_000 });
      await page
        .getByRole('button', {
          name: 'sample.mp4 · available',
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
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: Training', { exact: true }),
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
      page.getByText('State: Needs recovery', { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('status')).toContainText(job.name);
    expect(await app.records()).toHaveLength(1);
    await page
      .getByRole('button', { name: 'Terminate recovered work', exact: true })
      .click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Terminate recovered work' })
      .click();
    await expect(
      page.getByText('State: Interrupted', { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('status')).toHaveText('GPU available');
  } finally {
    await app.close();
  }
});

test('a gracefully stopped revision can be prepared and started again', async ({
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
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(page.getByLabel('Run logs')).toContainText(
      'SYNTHETIC ACCEPTANCE FIXTURE: train',
    );
    await page
      .getByRole('button', { name: 'Request graceful stop', exact: true })
      .click();
    await expect(page.getByText('State: Stopped', { exact: true })).toBeVisible(
      { timeout: 240_000 },
    );
    const stopped = (await app.records()).find((run) => run.job.id === job.id)!;

    // The stopped run no longer blocks preparing the same revision.
    await prepare();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
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
      page.getByText(
        /already has a Run with the same inputs \(Ready to start\)/,
      ),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Open existing run', exact: true })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
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
    await page
      .getByRole('button', { name: 'Force terminate', exact: true })
      .click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Force terminate' })
      .click();
    await expect(
      page.getByText('State: Stopped', { exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByRole('list', { name: 'Run stages' })
        .getByRole('listitem')
        .nth(1),
    ).toContainText('TrainingStopped');
    await expect(page.getByRole('status')).toHaveText('GPU available');
  } finally {
    await app.close();
  }
});

test('low disk space warns in review and preparation still goes ahead', async ({
  page,
}) => {
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
    await expect(estimate).toContainText('Optimizer states', {
      timeout: 240_000,
    });
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
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
  } finally {
    await app.close();
  }
});

test('a queued Run refuses delete and archive; archiving hides a job, keeps its Runs browsable and restores it', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const library = page.getByRole('button', {
      name: `${job.name} · WAN · r1`,
      exact: true,
    });
    const jobsTab = page.getByRole('button', { name: 'Jobs', exact: true });
    const archive = async () => {
      await page.getByRole('button', { name: 'More job actions' }).click();
      await page.getByRole('menuitem', { name: 'Archive job' }).click();
    };
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
      .poll(async () => (await app.records())[0]?.status, { timeout: 240_000 })
      .toBe('pending_preparation');

    // Deleting a queued Run is refused with the reason.
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await library.click();
    await expect(
      page.getByText('State: Waiting to prepare', { exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Delete run', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('active');
    const [queued] = await app.records();
    expect(queued.status).toBe('pending_preparation');
    expect(await exists(queued.directory)).toBe(true);

    // A queued Run keeps its job in the list, and the refusal says why.
    await jobsTab.click();
    await library.click();
    await archive();
    await expect(page.getByRole('alert')).toContainText('active Run', {
      timeout: 240_000,
    });
    await expect(library).toBeVisible();

    await app.leaseGpu('release');
    await expect
      .poll(async () => (await app.records())[0]?.status, { timeout: 240_000 })
      .toBe('prepared');
    const [run] = await app.records();

    await archive();
    await expect(library).toHaveCount(0);
    await page.getByRole('button', { name: 'More job actions' }).click();
    await expect(
      page.getByRole('menuitem', { name: 'Archive job' }),
    ).toBeDisabled();
    await page.keyboard.press('Escape');

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
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible();
    expect((await app.records()).map((record) => record.id)).toEqual([run.id]);

    // The Run has its own frozen snapshot, so it can still train.
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });

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
    await gpu.getByRole('link', { name: job.name }).click();
    await expect(page).toHaveURL(new RegExp(`#/runs/${run.id}$`));

    const history = page.locator('section', {
      has: page.getByRole('heading', { name: 'Run history' }),
    });
    await expect(history.locator('.status-badge')).toHaveText('Ready to start');
    await expect(history).not.toContainText('+00:00');
    await expect(history).not.toContainText('prepared');
    await expect(
      page.getByText(`Run ${run.id.slice(0, 8)}`, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(run.id, { exact: true })).toBeHidden();
    await page.getByText('Full Run ID').click();
    await expect(page.getByText(run.id, { exact: true })).toBeVisible();

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

test('a finished Run leads with its sample and final LoRA, and groups the other files', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await app.post('/api/runs/prepare', { job_id: job.id });
    await expect
      .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
      .toBe('prepared');
    const [run] = await app.records();
    await app.post(`/api/runs/${run.id}/start`);
    await expect
      .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
      .toBe('completed');
    await page.goto(`${app.url}/#/runs/${run.id}`);

    const result = page.getByRole('region', { name: 'Run result' });
    const video = result.getByLabel('Output video', { exact: true });
    const lora = result.getByRole('link', { name: 'Download final LoRA' });
    await expect(video).toBeInViewport({ ratio: 1 });
    await expect(lora).toBeInViewport({ ratio: 1 });
    await expect(result).toContainText(/Final LoRA \d+ (KB|MB|GB)/);

    const files = page.getByRole('region', { name: 'Run outputs' });
    const checkpoints = files.locator('.output-checkpoints');
    await expect(checkpoints.locator('table')).toBeHidden();
    await checkpoints.locator('summary').click();
    await expect(checkpoints.locator('tbody tr').last()).toContainText('Final');
    await expect(checkpoints).toContainText(/\d+ (KB|MB|GB)/);
    // Optimizer state weights are not offered as LoRA outputs.
    await expect(checkpoints).not.toContainText('model.safetensors');
    await expect(files).not.toContainText('-state/');
    const states = page.locator('section', {
      has: page.getByRole('heading', { name: 'Saved optimizer state' }),
    });
    await states.getByText(/^Optimizer state files/).click();
    await expect(states.locator('.state-files')).toContainText(
      'model.safetensors',
    );
  } finally {
    await app.close();
  }
});

async function preparedRun(app: Awaited<ReturnType<typeof runWorkspace>>) {
  const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
  await app.post('/api/runs/prepare', { job_id: job.id });
  await expect
    .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
    .toBe('prepared');
  return (await app.records())[0];
}

function outputList(
  run: { id: string },
  status: string,
  outputs: Record<string, unknown>[],
) {
  return {
    run_id: run.id,
    job: { name: 'Mocked', family: 'wan', revision: 1 },
    status,
    warnings: [],
    note: 'These files belong to this Run.',
    outputs: outputs.map((output) => ({
      run_id: run.id,
      file_url: '/nowhere',
      bytes: 2000,
      ...output,
    })),
  };
}

test('a damaged final LoRA is called out rather than offered as the result', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const run = await preparedRun(app);
    await page.route('**/api/runs/*/outputs', (route) =>
      route.fulfill({
        json: outputList(run, 'completed', [
          {
            id: 'final',
            name: 'wan.safetensors',
            path: 'checkpoints/wan.safetensors',
            kind: 'checkpoint',
            status: 'incomplete',
            error: 'Output is empty.',
          },
        ]),
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const result = page.getByRole('region', { name: 'Run result' });
    await expect(result).toContainText('Output is empty.');
    await expect(result.getByRole('link')).toHaveCount(0);
  } finally {
    await app.close();
  }
});

test('a partial Run shows the outputs it has, with their problems', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const run = await preparedRun(app);
    await page.route('**/api/runs/*/outputs', (route) =>
      route.fulfill({
        json: outputList(run, 'failed', [
          {
            id: 'early',
            name: 'wan-step00000002.safetensors',
            path: 'checkpoints/wan-step00000002.safetensors',
            kind: 'checkpoint',
            status: 'available',
          },
          {
            id: 'later',
            name: 'wan-step00000010.safetensors',
            path: 'checkpoints/wan-step00000010.safetensors',
            kind: 'checkpoint',
            status: 'available',
          },
          {
            id: 'sample',
            name: 'sample.mp4',
            path: 'samples/sample.mp4',
            kind: 'video',
            status: 'incomplete',
            error: 'Sample has no audio.',
          },
        ]),
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const result = page.getByRole('region', { name: 'Run result' });
    await expect(result).toContainText('sample.mp4: Sample has no audio.');
    await expect(result).toContainText('Latest checkpoint step 10');
    await expect(
      result.getByRole('link', { name: 'Download latest checkpoint' }),
    ).toHaveAttribute('href', '/nowhere?download=true');
  } finally {
    await app.close();
  }
});
