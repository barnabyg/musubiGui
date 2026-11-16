import { test, expect } from '@playwright/test';
import { readdir } from 'node:fs/promises';
import path from 'node:path';
import { exists, runPanel, runWorkspace, type RunRecord } from './runHarness';

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

test('a stopped Run resumes saved optimizer state through the UI after a backend restart', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs[0];
    const saved = await (await fetch(`${app.url}/api/jobs/${job.id}`)).json();
    const updated = await fetch(`${app.url}/api/jobs/${job.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...saved,
        version: undefined,
        id: undefined,
        config: { ...saved.config, steps: 50, save_every: 1, save_state: true },
      }),
    });
    expect(updated.ok).toBeTruthy();
    const original = (await app.post('/api/runs/prepare', {
      job_id: job.id,
    })) as RunRecord;
    await page.goto(`${app.url}/#/runs/${original.id}`);
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    // Wait for an actual periodic saved state before stopping; Stop need not save.
    await expect
      .poll(
        async () => {
          const checkpoints = path.join(original.directory, 'checkpoints');
          const folders = await readdir(checkpoints).catch(() => []);
          const complete = await Promise.all(
            folders
              .filter((name) => name.endsWith('-state'))
              .map((name) =>
                exists(path.join(checkpoints, name, 'scheduler.bin')),
              ),
          );
          return complete.some(Boolean);
        },
        { timeout: 240_000 },
      )
      .toBeTruthy();
    await page
      .getByRole('button', { name: 'Request graceful stop', exact: true })
      .click();
    await expect(page.getByText('State: Stopped', { exact: true })).toBeVisible(
      { timeout: 240_000 },
    );
    await app.crash();
    await app.start();
    await page.reload();
    const resume = runPanel(page, 'resume');
    await resume.getByText('How resuming works', { exact: true }).click();
    await expect(
      resume.getByText(/Work since the last saved state must run again/),
    ).toBeVisible();
    await expect(
      resume.getByText(/data order restarts from the beginning/),
    ).toBeVisible();
    await resume
      .getByRole('button', { name: 'Inspect saved states', exact: true })
      .click();
    const restore = resume
      .getByRole('button', { name: 'Restore as new segment', exact: true })
      .first();
    await expect(restore).toBeEnabled({ timeout: 240_000 });
    // The first listed state is the save at step 1, so training continues to a total, not for extra steps.
    await expect(resume).toContainText('optimizer step 1');
    const target = resume.getByLabel('Train until step');
    await target.fill('1');
    await expect(restore).toBeDisabled();
    await expect(resume).toContainText('Choose a step after 1');
    await target.fill('2');
    await restore.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Training continues until step 2');
    await expect(dialog).toContainText('The data order restarts.');
    await dialog
      .getByRole('button', { name: 'Restore as new segment', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    // Progress continues the original numbering up to the chosen total.
    await expect(page.getByText('Step 2/2', { exact: true })).toBeVisible();
    const records = await app.records();
    const segment = records.find((run) => run.id !== original.id)!;
    const detail = await (
      await fetch(`${app.url}/api/runs/${segment.id}`)
    ).json();
    expect(detail.resume_of).toBe(original.id);
    expect(detail.segment).toMatchObject({
      target_steps: 2,
      step_offset: 1,
      additional_steps: 1,
    });
    const segmentCheckpoints = await readdir(
      path.join(detail.directory, 'checkpoints'),
    );
    // The segment's interval save at global step 2 repeated its final save and was removed.
    expect(detail.removed_duplicates).toEqual(
      expect.arrayContaining([expect.stringMatching(/-step00000002-state$/)]),
    );
    expect(
      segmentCheckpoints.filter((name) => name.includes('-step00000001')),
    ).toEqual([]);
    const states = await (
      await fetch(`${app.url}/api/runs/${segment.id}/states`)
    ).json();
    expect(
      states.some(
        (state: { valid: boolean; optimizer_step: number }) =>
          state.valid && state.optimizer_step > 1,
      ),
    ).toBeTruthy();
    await app.crash();
    await app.start();
    const persisted = await (
      await fetch(`${app.url}/api/runs/${segment.id}`)
    ).json();
    expect(persisted.resume_of).toBe(original.id);
    expect(persisted.status).toBe('completed');
    expect(
      (await app.records()).find((run) => run.id === original.id)?.status,
    ).toBe('stopped');

    // A Run without saved state explains how to make future Runs resumable.
    const currentJob = await (
      await fetch(`${app.url}/api/jobs/${job.id}`)
    ).json();
    const withoutState = await fetch(`${app.url}/api/jobs/${job.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...currentJob,
        version: undefined,
        id: undefined,
        config: { ...saved.config, steps: 2, save_state: false },
      }),
    });
    expect(withoutState.ok).toBeTruthy();
    const fresh = (await app.post('/api/runs/prepare', {
      job_id: job.id,
    })) as RunRecord;
    await page.goto(`${app.url}/#/runs/${fresh.id}`);
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await resume.getByText('How resuming works', { exact: true }).click();
    await expect(
      resume.getByText(/^Saved optimizer state was not enabled/),
    ).toBeVisible();
    await resume
      .getByRole('button', { name: 'Inspect saved states', exact: true })
      .click();
    await expect(resume).toContainText('No saved optimizer states found');
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
