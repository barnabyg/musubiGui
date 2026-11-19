import { test, expect, type Page } from '@playwright/test';
import { access } from 'node:fs/promises';
import {
  openRunTab,
  runPanel,
  runTabPanel,
  runWorkspace,
  type RunRecord,
} from './runHarness';

type App = Awaited<ReturnType<typeof runWorkspace>>;
type Detail = RunRecord & { controls?: { action: string; step?: number }[] };
type State = { id: string; valid: boolean; resume_step?: number };

async function configure(app: App, jobId: string, config: object) {
  const saved = await (await fetch(`${app.url}/api/jobs/${jobId}`)).json();
  const updated = await fetch(`${app.url}/api/jobs/${jobId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...saved,
      version: undefined,
      id: undefined,
      config: { ...saved.config, ...config },
    }),
  });
  expect(updated.ok).toBeTruthy();
}

const detail = async (app: App, id: string) =>
  (await (await fetch(`${app.url}/api/runs/${id}`)).json()) as Detail;

async function waitFor(app: App, id: string, status: string) {
  await expect
    .poll(async () => (await detail(app, id)).status, { timeout: 240_000 })
    .toBe(status);
}

// Waits for a preview of this segment's own, then saves and stops at the next step.
async function previewThenSaveAndStop(app: App, id: string) {
  await expect
    .poll(
      async () => {
        const { outputs } = (await (
          await fetch(`${app.url}/api/runs/${id}/outputs`)
        ).json()) as { outputs: { kind?: string }[] };
        return outputs.filter((item) =>
          ['image', 'video'].includes(item.kind ?? ''),
        ).length;
      },
      { timeout: 240_000 },
    )
    .toBeGreaterThan(0);
  await app.post(`/api/runs/${id}/control`, { action: 'save_and_stop' });
  await waitFor(app, id, 'stopped');
  return (await detail(app, id)).controls!.at(-1)!.step!;
}

// Restores the furthest valid saved state as a new segment that trains until `target`.
async function resume(app: App, id: string, target: number) {
  const states = (await (
    await fetch(`${app.url}/api/runs/${id}/states`)
  ).json()) as State[];
  const state = states
    .filter((item) => item.valid)
    .sort((a, b) => b.resume_step! - a.resume_step!)[0];
  return (await app.post(`/api/runs/${id}/restore-state`, {
    state_id: state.id,
    target_steps: target,
    confirmed: true,
  })) as RunRecord;
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  );

test('a training stopped and resumed twice is one row with every preview in step order, and deletes as one', async ({
  page,
}) => {
  test.setTimeout(30 * 60_000);
  const app = await runWorkspace({ previewImages: true });
  try {
    const [first, second] = app.manifest.jobs.filter(
      (item) => item.family === 'wan',
    );
    // Previews every other step; only Save and stop saves state, so nothing else piles up.
    await configure(app, first.id, {
      steps: 200,
      save_every: 200,
      preview_every: 2,
      save_state: false,
      sample_enabled: false,
    });
    const root = (await app.post('/api/runs/prepare', {
      job_id: first.id,
      start: true,
    })) as RunRecord;
    const stoppedAt = await previewThenSaveAndStop(app, root.id);
    const middle = await resume(app, root.id, 200);
    const resumedAt = await previewThenSaveAndStop(app, middle.id);
    const latest = await resume(app, middle.id, resumedAt + 4);
    await waitFor(app, latest.id, 'completed');
    const evens = (from: number, to: number) =>
      Array.from({ length: to }, (_, index) => index + 1).filter(
        (step) => step > from && step % 2 === 0,
      );
    const expected = [
      ...evens(0, stoppedAt),
      ...evens(stoppedAt, resumedAt),
      ...evens(resumedAt, resumedAt + 4),
    ];

    // One row in Runs, with the latest status, expanding to each segment.
    await page.goto(`${app.url}/#/runs`);
    const history = page.locator('.run-history');
    const row = history.getByLabel(`Training ${first.name}`, { exact: true });
    await expect(row).toContainText('3 segments');
    await expect(row).toContainText('Completed');
    await expect(
      history.getByRole('button', { name: `${first.name} · WAN · r2` }),
    ).toHaveCount(1);
    await row
      .getByRole('button', { name: 'Show 3 segments', exact: true })
      .click();
    const listed = row.getByRole('list', { name: 'Segments' });
    await expect(listed.getByRole('listitem')).toHaveCount(3);
    await expect(listed.getByRole('listitem').nth(0)).toContainText(
      `Segment 1 · steps 0–${stoppedAt}`,
    );
    await expect(listed.getByRole('listitem').nth(2)).toContainText(
      `Segment 3 · steps ${resumedAt}–${resumedAt + 4}`,
    );
    // Only the latest segment can go on its own.
    await expect(
      listed.getByRole('button', { name: 'Delete segment', exact: true }),
    ).toHaveCount(1);
    await page.setViewportSize({ width: 375, height: 812 });
    expect(await noHorizontalScroll(page)).toBe(true);
    await page.setViewportSize({ width: 1366, height: 768 });

    // Opening the row shows the latest segment, with the training's segments and its previews merged.
    await row
      .getByRole('button', { name: `${first.name} · WAN · r2`, exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`#/runs/${latest.id}$`));
    const segments = runPanel(page, 'segments');
    await expect(segments.getByRole('listitem')).toHaveCount(3);
    await expect(segments).toContainText(
      `Step ${resumedAt + 4} of ${resumedAt + 4} across 3 segments`,
    );
    await openRunTab(page, 'samples');
    const samples = runTabPanel(page, 'samples');
    await expect(samples).toContainText(
      'Samples from all 3 segments of this training, in step order.',
    );
    const buttons = samples
      .getByLabel('Output files', { exact: true })
      .getByRole('button');
    await expect(buttons).toHaveCount(expected.length, { timeout: 240_000 });
    const names = await buttons.allTextContents();
    expect(
      names.map((name) => Number(/^Step (\d+) preview/.exec(name)![1])),
    ).toEqual(expected);
    const files = samples.getByLabel('Output files', { exact: true });
    await expect(files.locator('span').first()).toContainText('Segment 1');
    await expect(files.locator('span').last()).toContainText('Segment 3');
    await buttons.first().click();
    await expect(
      samples.getByLabel('Selected sample', { exact: true }),
    ).toContainText(`from Run ${root.id.slice(0, 8)} · segment 1 · step 2`);
    await page.setViewportSize({ width: 375, height: 812 });
    expect(await noHorizontalScroll(page)).toBe(true);
    await openRunTab(page, 'overview');
    expect(await noHorizontalScroll(page)).toBe(true);
    await page.setViewportSize({ width: 1366, height: 768 });
    // An earlier segment shows the same training, and can only be deleted with it.
    await segments
      .getByRole('link', { name: 'Segment 1', exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`#/runs/${root.id}$`));
    const actions = runPanel(page, 'actions');
    await expect(
      actions.getByRole('button', { name: 'Delete training', exact: true }),
    ).toBeVisible();
    await expect(
      actions.getByRole('button', { name: /^Delete (run|segment)$/ }),
    ).toHaveCount(0);

    // A second training, resumed once after it completed.
    await configure(app, second.id, {
      steps: 4,
      save_every: 4,
      preview_every: 2,
      save_state: true,
      sample_enabled: false,
    });
    const other = (await app.post('/api/runs/prepare', {
      job_id: second.id,
      start: true,
    })) as RunRecord;
    await waitFor(app, other.id, 'completed');
    const otherLatest = await resume(app, other.id, 6);
    await waitFor(app, otherLatest.id, 'completed');

    // Delete the second training as a whole from its row.
    await page.goto(`${app.url}/#/runs`);
    const otherRow = page.getByLabel(`Training ${second.name}`, {
      exact: true,
    });
    await otherRow
      .getByRole('button', { name: 'Delete training', exact: true })
      .click();
    const dialog = page.getByRole('dialog');
    const doomed = dialog.getByRole('list', { name: 'Segments to delete' });
    await expect(doomed.getByRole('listitem')).toHaveCount(2);
    await expect(doomed).toContainText(`Run ${other.id.slice(0, 8)}`);
    await expect(doomed).toContainText(`Run ${otherLatest.id.slice(0, 8)}`);
    await dialog
      .getByRole('button', { name: 'Delete training', exact: true })
      .click();
    await expect(page.getByText(/^Training deleted\. Freed /)).toBeVisible({
      timeout: 240_000,
    });
    await expect(otherRow).toHaveCount(0);
    const remaining = (await app.records()).map((run) => run.id);
    expect(remaining).not.toContain(other.id);
    expect(remaining).not.toContain(otherLatest.id);
    expect(remaining).toHaveLength(3);
    for (const folder of [other.directory, otherLatest.directory])
      await expect(access(folder)).rejects.toThrow();
  } finally {
    await app.close();
  }
});

test('a completed segment with no sample offers earlier previews only on Samples', async ({
  page,
}) => {
  const app = await runWorkspace({ previewImages: true });
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'h3')!;
    await configure(app, job.id, {
      steps: 2,
      preview_every: 2,
      sample_enabled: false,
      save_every: 2,
      save_state: true,
    });
    const root = (await app.post('/api/runs/prepare', {
      job_id: job.id,
      start: true,
    })) as RunRecord;
    await waitFor(app, root.id, 'completed');
    const next = await resume(app, root.id, 3);
    await waitFor(app, next.id, 'completed');
    await page.goto(`${app.url}/#/runs/${next.id}`);
    await openRunTab(page, 'samples');
    await page.getByRole('button', { name: /^Step 2 preview/ }).click();
    await expect(
      page.getByLabel('Selected sample', { exact: true }),
    ).toContainText(root.id.slice(0, 8));
  } finally {
    await app.close();
  }
});
