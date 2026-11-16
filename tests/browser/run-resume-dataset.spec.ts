import { test, expect, type Page } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { runPanel, runWorkspace, type RunRecord } from './runHarness';

const NEW_CAPTION = 'A blue field slowly brightens, captioned again.';

async function noHorizontalScroll(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
}

test('a caption edited after Save and stop resumes with a refreshed snapshot, after review shows the extra preparation and disk use', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const current = await (await fetch(`${app.url}/api/jobs/${job.id}`)).json();
    const saved = await fetch(`${app.url}/api/jobs/${job.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...current,
        version: undefined,
        id: undefined,
        config: {
          ...current.config,
          steps: 200,
          save_every: 200,
          save_state: true,
          preview_every: 0,
          sample_enabled: false,
        },
      }),
    });
    expect(saved.ok).toBeTruthy();
    const original = (await app.post('/api/runs/prepare', {
      job_id: job.id,
      start: true,
    })) as RunRecord;
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${app.url}/#/runs/${original.id}`);
    await expect(
      page.getByText('State: Training', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await runPanel(page, 'actions')
      .getByRole('button', { name: 'Save and stop', exact: true })
      .click();
    await expect(page.getByText('State: Stopped', { exact: true })).toBeVisible(
      { timeout: 240_000 },
    );
    const settings = (await (
      await fetch(`${app.url}/api/settings`)
    ).json()) as { checkout: string };
    await writeFile(path.join(settings.checkout, 'instant-steps'), '');

    // Edit the clip's caption in the dataset, as anyone would between trainings.
    await page.goto(`${app.url}/#/datasets/${job.dataset_id}`);
    await page.getByRole('button', { name: 'motion.mp4 video' }).click();
    await page.getByLabel('Caption', { exact: true }).fill(NEW_CAPTION);
    const media = original.manifest.items[0].absolute_path;
    await expect
      .poll(() => readFile(media.replace(/\.mp4$/, '.txt'), 'utf8'), {
        timeout: 60_000,
      })
      .toBe(NEW_CAPTION);

    // Job review shows the caption edit, the caching it needs and the extra disk use.
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const compatibility = page.getByRole('region', {
      name: 'Resume compatibility',
    });
    await expect(compatibility).toContainText(
      'Its saved state can resume with this revision.',
      { timeout: 240_000 },
    );
    await expect(
      compatibility.getByRole('list', {
        name: 'Resume with re-prepare',
        exact: true,
      }),
    ).toHaveText('Dataset contents: 1 caption changed');
    const preparation = compatibility.getByRole('group', {
      name: 'Preparation before resuming',
    });
    await expect(preparation).toContainText(
      'Runs text caching for the changed items only.',
    );
    await expect(preparation).toContainText(
      'Latents: reuses all 1 items’ caches.',
    );
    await expect(preparation).toContainText(
      'Text: caches 1 item (motion.mp4), reuses 0.',
    );
    const disk = preparation.getByRole('list', { name: 'Extra disk use' });
    await expect(disk).toContainText('New media snapshot:');
    await expect(disk).toContainText('Copied caches:');
    await expect(disk).toContainText('New caches (estimated):');
    await expect(disk).toContainText('needs about');
    await noHorizontalScroll(page);

    // Run details shows the same before resuming.
    await page.goto(`${app.url}/#/runs/${original.id}`);
    const latest = runPanel(page, 'actions').getByRole('group', {
      name: 'Resume with the latest revision',
    });
    const resume = latest.getByRole('button', { name: 'Resume', exact: true });
    const dialog = page.getByRole('dialog');
    await resume.click();
    // The confirmation shows the re-prepare work; cancelling leaves the check on the panel.
    await expect(dialog).toContainText(
      'It first takes a new snapshot of the datasets (1 caption changed) and caches only the items that changed.',
      { timeout: 240_000 },
    );
    await expect(
      dialog.getByRole('group', { name: 'Preparation before resuming' }),
    ).toContainText('Text: caches 1 item (motion.mp4), reuses 0.');
    await noHorizontalScroll(page);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(latest).toContainText(
      'This revision can resume the saved state.',
    );
    await expect(
      latest.getByRole('list', { name: 'Resume with re-prepare', exact: true }),
    ).toHaveText('Dataset contents: 1 caption changed');
    await expect(
      latest.getByRole('group', { name: 'Preparation before resuming' }),
    ).toContainText('Text: caches 1 item (motion.mp4), reuses 0.');
    await noHorizontalScroll(page);
    await resume.click();
    await expect(dialog).toContainText('It first takes a new snapshot', {
      timeout: 240_000,
    });
    await dialog.getByRole('button', { name: /^Resume with revision/ }).click();

    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(page.getByText('Step 200/200', { exact: true })).toBeVisible();
    await noHorizontalScroll(page);
    const records = await app.records();
    const segment = records.find((run) => run.id !== original.id)!;
    const detail = await (
      await fetch(`${app.url}/api/runs/${segment.id}`)
    ).json();
    expect(detail.resume_of).toBe(original.id);
    expect(detail.manifest.items[0].caption.text).toBe(NEW_CAPTION);
    expect(detail.segment.dataset_changes.summary).toEqual([
      '1 caption changed',
    ]);
    const stages = Object.fromEntries(
      detail.stages.map((stage: { name: string; status: string }) => [
        stage.name,
        stage.status,
      ]),
    );
    expect(stages).toMatchObject({
      latent_cache: 'reused',
      text_cache: 'completed',
      train: 'completed',
    });
    // The source keeps its own snapshot and caption.
    const source = records.find((run) => run.id === original.id)!;
    expect(source.status).toBe('stopped');
    expect(source.manifest.items[0].caption.text).not.toBe(NEW_CAPTION);
  } finally {
    await app.close();
  }
});
