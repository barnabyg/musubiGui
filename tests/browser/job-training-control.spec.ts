import { test, expect, type Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace, type RunRecord } from './runHarness';

const NEW_PROMPT = 'A green circle drifts over a white field.';

type Workspace = Awaited<ReturnType<typeof runWorkspace>>;

async function noHorizontalScroll(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
}

// A paced 200-step Wan Job, so Save and stop lands mid-training.
async function pacedJob(app: Workspace, config: Record<string, unknown> = {}) {
  const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
  const saved = await (await fetch(`${app.url}/api/jobs/${job.id}`)).json();
  const response = await fetch(`${app.url}/api/jobs/${job.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...saved,
      version: undefined,
      id: undefined,
      config: {
        ...saved.config,
        steps: 200,
        save_every: 200,
        save_state: true,
        preview_every: 0,
        sample_enabled: false,
        ...config,
      },
    }),
  });
  expect(response.ok).toBeTruthy();
  return job;
}

async function reviewAndStart(page: Page) {
  await page
    .getByRole('button', { name: 'Review configuration', exact: true })
    .click();
  await expect(page.getByText(/^Review passed for revision \d+$/)).toBeVisible({
    timeout: 240_000,
  });
  await page
    .getByRole('button', { name: 'Prepare and start', exact: true })
    .click();
}

// Starts the Job from its page and stops it with Save and stop there; returns the stop step.
async function startAndSaveAndStop(page: Page, app: Workspace, jobId: string) {
  await page.goto(`${app.url}/#/jobs/${jobId}`);
  await reviewAndStart(page);
  const panel = page.getByRole('region', { name: 'Current training' });
  await expect(panel.getByText('State: Training', { exact: true })).toBeVisible(
    { timeout: 240_000 },
  );
  await expect(panel.getByLabel('Training progress')).toContainText(
    /Step \d+\/200/,
    { timeout: 240_000 },
  );
  await noHorizontalScroll(page);
  await panel
    .getByRole('button', { name: 'Save and stop', exact: true })
    .click();
  await expect(panel.getByText('State: Stopped', { exact: true })).toBeVisible({
    timeout: 240_000,
  });
  await expect(page).toHaveURL(`${app.url}/#/jobs/${jobId}`);
  const [run] = (await app.records()) as (RunRecord & {
    controls: { action: string; step: number }[];
  })[];
  const step = run.controls.at(-1)!.step;
  expect(step).toBeLessThan(200);
  return { run, step, panel };
}

// The resumed segment needn't pace its steps.
async function instantSteps(app: Workspace) {
  const settings = (await (await fetch(`${app.url}/api/settings`)).json()) as {
    checkout: string;
  };
  await writeFile(path.join(settings.checkout, 'instant-steps'), '');
}

test('Start on the Job page resumes a stopped training with an edited prompt and steps', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    // A final sample shows the edited prompt; Save and stop skips it.
    const job = await pacedJob(app, { sample_enabled: true });
    await page.setViewportSize({ width: 375, height: 812 });
    const { run, step, panel } = await startAndSaveAndStop(page, app, job.id);

    // The banner says where training stopped and where it last saved; the steps read as a total.
    const banner = panel.getByRole('group', { name: 'Resumable training' });
    await expect(banner).toContainText(`Training stopped at step ${step}.`);
    await expect(banner).toContainText(
      `Its last state was saved at step ${step}.`,
    );
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    const steps = page.getByLabel(
      `Training steps (total; training is at step ${step})`,
    );
    await expect(steps).toHaveValue('200');
    // Fields a resumed training can't take are marked, and stay editable.
    const rank = page.getByLabel('LoRA rank', { exact: true });
    await expect(rank).toBeEnabled();
    await expect(rank.locator('xpath=..')).toContainText(
      'Needs a new Run: changing this starts a fresh training.',
    );
    await expect(steps.locator('xpath=..')).not.toContainText(
      'Needs a new Run',
    );
    // This training kept state, so only turning state saving off needs a new Run.
    await page.getByRole('tab', { name: 'Checkpoints', exact: true }).click();
    const saveState = page.getByRole('checkbox', {
      name: /^Save optimizer\/training state/,
    });
    await expect(saveState).toBeChecked();
    await expect(saveState.locator('xpath=..')).not.toContainText(
      'Needs a new Run',
    );
    await saveState.uncheck();
    await expect(saveState.locator('xpath=..')).toContainText(
      'Needs a new Run: changing this starts a fresh training.',
    );
    await saveState.check();
    await expect(saveState.locator('xpath=..')).not.toContainText(
      'Needs a new Run',
    );
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await noHorizontalScroll(page);

    await steps.fill('210');
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    await page.getByLabel('Sample prompt').fill(NEW_PROMPT);
    await instantSteps(app);
    await reviewAndStart(page);
    // A plain resume needs no confirmation: no warned change, no re-prepare.
    await expect(
      page.getByText(`Resuming from step ${step} until step 210`, {
        exact: true,
      }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(
      panel.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(panel).toContainText('Segment 2 of 2');
    await expect(panel.getByLabel('Training progress')).toContainText(
      'Step 210/210',
    );
    await expect(page).toHaveURL(`${app.url}/#/jobs/${job.id}`);

    const records = await app.records();
    expect(records).toHaveLength(2);
    const segment = records.find((record) => record.id !== run.id)!;
    const detail = await (
      await fetch(`${app.url}/api/runs/${segment.id}`)
    ).json();
    expect(detail.resume_of).toBe(run.id);
    expect(detail.segment.step_offset).toBe(step);
    expect(detail.segment.target_steps).toBe(210);
    expect(detail.job.config.sample_prompt).toBe(NEW_PROMPT);
    // The Job page shows the training's newest sample and checkpoint.
    const latest = panel.getByRole('group', { name: 'Latest outputs' });
    await expect(latest).toContainText('Latest preview:', { timeout: 240_000 });
    await expect(latest).toContainText('Latest checkpoint:');
    expect(records.find((record) => record.id === run.id)!.status).toBe(
      'stopped',
    );
    await noHorizontalScroll(page);
  } finally {
    await app.close();
  }
});

test('an edit that needs a new Run makes Start name the fields and start fresh only once confirmed', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    // The Job keeps no state; Save and stop saved one anyway.
    const job = await pacedJob(app, { save_state: false });
    await page.setViewportSize({ width: 375, height: 812 });
    const { run } = await startAndSaveAndStop(page, app, job.id);

    // Turning state saving on resumes, so it isn't marked either way.
    await page.getByRole('tab', { name: 'Checkpoints', exact: true }).click();
    const saveState = page.getByRole('checkbox', {
      name: /^Save optimizer\/training state/,
    });
    await expect(saveState).not.toBeChecked();
    for (const checked of [true, false]) {
      await expect(saveState.locator('xpath=..')).not.toContainText(
        'Needs a new Run',
      );
      await saveState.setChecked(checked);
    }
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await page.getByLabel('LoRA rank', { exact: true }).fill('16');
    await reviewAndStart(page);
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Start a fresh training?', {
      timeout: 240_000,
    });
    await expect(
      dialog.getByRole('list', { name: 'Changed fields that need a new Run' }),
    ).toHaveText('LoRA rank: 8 → 16');
    await expect(dialog).toContainText('The previous training stays in Runs.');
    await noHorizontalScroll(page);
    await dialog.getByRole('button', { name: 'Keep editing' }).click();
    await expect(
      page.getByText('Start cancelled', { exact: true }),
    ).toBeVisible();
    expect(await app.records()).toHaveLength(1);

    await page
      .getByRole('button', { name: 'Prepare and start', exact: true })
      .click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Start fresh', exact: true })
      .click();
    const panel = page.getByRole('region', { name: 'Current training' });
    await expect
      .poll(async () => (await app.records()).length, { timeout: 240_000 })
      .toBe(2);
    const fresh = (await app.records()).find((record) => record.id !== run.id)!;
    await expect(
      panel.getByRole('link', { name: 'Open Run details' }),
    ).toHaveAttribute('href', `#/runs/${fresh.id}`);
    // A fresh training: no segment of the stopped one, which stays in history.
    await expect(panel).not.toContainText('Segment');
    const detail = await (
      await fetch(`${app.url}/api/runs/${fresh.id}`)
    ).json();
    expect(detail.resume_of).toBeUndefined();
    expect(detail.job.config.rank).toBe(16);
    expect(
      (await app.records()).find((record) => record.id === run.id)!.status,
    ).toBe('stopped');
  } finally {
    await app.close();
  }
});

test('live progress on the Job page matches Run details', async ({
  page,
  context,
}) => {
  const app = await runWorkspace();
  try {
    const job = await pacedJob(app);
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await reviewAndStart(page);
    const panel = page.getByRole('region', { name: 'Current training' });
    await expect(
      panel.getByText('State: Training', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const [run] = await app.records();
    const details = await context.newPage();
    await details.goto(`${app.url}/#/runs/${run.id}`);
    const jobProgress = panel.getByLabel('Training progress');
    const runProgress = details.getByLabel('Training progress');
    await expect(jobProgress).toContainText(/Step \d+\/200/, {
      timeout: 240_000,
    });
    await expect(runProgress).toContainText(/Step \d+\/200/, {
      timeout: 240_000,
    });
    // Both read the same Run's progress; Save now from the Job page shows in both.
    await panel.getByRole('button', { name: 'Save now', exact: true }).click();
    await expect(panel).toContainText(/Save now ran at step \d+\./, {
      timeout: 240_000,
    });
    await expect(
      details.getByRole('region', { name: 'Actions' }),
    ).toContainText(/Save now ran at step \d+\./, { timeout: 240_000 });
    await details
      .getByRole('region', { name: 'Actions' })
      .getByRole('button', { name: 'Save and stop', exact: true })
      .click();
    await expect(
      panel.getByText('State: Stopped', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(
      details.getByText('State: Stopped', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    // Once stopped, both show the same step.
    const step = (
      (await (await fetch(`${app.url}/api/runs/${run.id}`)).json()) as {
        controls: { step: number }[];
      }
    ).controls.at(-1)!.step;
    for (const progress of [jobProgress, runProgress])
      await expect(progress.getByText(/^Step \d+\/200$/)).toHaveText(
        `Step ${step}/200`,
        { timeout: 240_000 },
      );
  } finally {
    await app.close();
  }
});
