import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { helper, python } from './backend';
import { exists, runWorkspace } from './runHarness';

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

    const locked = path.join(run.directory, 'config', 'inputs.json');
    const lock = spawn(python, [helper, 'hold-file', locked]);
    releaseLock = () => lock.kill();
    await new Promise((resolve) => lock.stdout.once('data', resolve));

    // A locked file leaves the Run visible, saying what remains.
    await button('Delete run').click();
    const question = page.getByRole('dialog');
    await expect(question).toContainText(run.id.slice(0, 8));
    await expect(question).toContainText(/This frees \d+ (KB|MB|GB)/);
    await expect(question).toContainText("can't be undone");
    await expect(question).toContainText('copy any LoRA');
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
    await expect(
      page.getByRole('button', { name: 'Refresh outputs' }),
    ).toHaveCount(0);

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
    await expect(page.getByRole('status')).toHaveText('Job deleted');
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
      .getByRole('button', { name: 'Prepare only', exact: true })
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
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      job.name,
    );

    await app.leaseGpu('release');
    await expect
      .poll(async () => (await app.records())[0]?.status, { timeout: 240_000 })
      .toBe('prepared');
    const [run] = await app.records();

    await archive();
    await expect(page.getByRole('status')).toHaveText('Job archived');
    await expect(library).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'More job actions' }),
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
