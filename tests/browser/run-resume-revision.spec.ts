import { test, expect, type Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { runPanel, runWorkspace, type RunRecord } from './runHarness';

const NEW_PROMPT = 'A green circle drifts over a white field.';

async function noHorizontalScroll(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
}

test('a stopped Run resumes with an edited revision of its Job, after review shows what changed', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const save = async (config: Record<string, unknown>) => {
      const current = await (
        await fetch(`${app.url}/api/jobs/${job.id}`)
      ).json();
      const response = await fetch(`${app.url}/api/jobs/${job.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...current,
          version: undefined,
          id: undefined,
          config: { ...current.config, ...config },
        }),
      });
      expect(response.ok).toBeTruthy();
      return ((await response.json()) as { revision: number }).revision;
    };
    await save({
      steps: 200,
      save_every: 200,
      save_state: true,
      preview_every: 0,
      sample_enabled: false,
    });
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
    // The resumed segment needn't pace its steps.
    const settings = (await (
      await fetch(`${app.url}/api/settings`)
    ).json()) as {
      checkout: string;
    };
    await writeFile(path.join(settings.checkout, 'instant-steps'), '');

    // A changed LoRA rank can't resume: the panel names it and offers no resume.
    const rejected = await save({ rank: 16 });
    const resume = runPanel(page, 'resume');
    const latest = resume.getByRole('region', {
      name: 'Resume with the latest revision',
    });
    await latest
      .getByRole('button', { name: 'Check latest revision', exact: true })
      .click();
    await expect(
      latest.getByRole('list', { name: 'Needs a new Run' }),
    ).toContainText('LoRA rank: 8 → 16');
    await expect(latest).toContainText(
      'This revision can’t resume the saved state.',
    );
    await expect(
      latest.getByRole('button', {
        name: `Resume with revision ${rejected}`,
        exact: true,
      }),
    ).toBeDisabled();

    // Restore the rank; edit the preview prompt and raise the total.
    const revision = await save({
      rank: 8,
      sample_prompt: NEW_PROMPT,
      preview_every: 70,
      steps: 210,
    });

    // Job review lists the changes by tier against the Job's latest resumable training.
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const compatibility = page.getByRole('region', {
      name: 'Resume compatibility',
    });
    await expect(compatibility).toContainText(
      `Run ${original.id.slice(0, 8)}`,
      { timeout: 240_000 },
    );
    await expect(compatibility).toContainText(
      'Its saved state can resume with this revision.',
    );
    const applies = compatibility.getByRole('list', {
      name: 'Applies on resume',
    });
    await expect(applies).toContainText(`Sample prompt: “`);
    await expect(applies).toContainText(NEW_PROMPT);
    await expect(applies).toContainText('Training steps: 200 → 210');
    await expect(applies).toContainText('Preview every N steps: 0 → 70');
    await expect(compatibility).toContainText('data order restarts');
    await noHorizontalScroll(page);

    await page.goto(`${app.url}/#/runs/${original.id}`);
    await latest
      .getByRole('button', { name: 'Check latest revision', exact: true })
      .click();
    await expect(latest).toContainText(
      'This revision can resume the saved state.',
      { timeout: 240_000 },
    );
    await expect(latest).toContainText(/until step 210/);
    await expect(
      latest.getByRole('list', { name: 'Applies on resume' }),
    ).toContainText('Training steps: 200 → 210');
    await noHorizontalScroll(page);
    await latest
      .getByRole('button', {
        name: `Resume with revision ${revision}`,
        exact: true,
      })
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('The data order restarts.');
    await expect(dialog).toContainText('until step 210');
    await dialog
      .getByRole('button', {
        name: `Resume with revision ${revision}`,
        exact: true,
      })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(page.getByText('Step 210/210', { exact: true })).toBeVisible();
    // The segment is its own Run of the new revision, linked to the source, which is unchanged.
    await expect(page).not.toHaveURL(new RegExp(original.id));
    const records = await app.records();
    const segment = records.find((run) => run.id !== original.id)!;
    const detail = await (
      await fetch(`${app.url}/api/runs/${segment.id}`)
    ).json();
    expect(detail.resume_of).toBe(original.id);
    expect(detail.job.revision).toBe(revision);
    expect(detail.job.config.sample_prompt).toBe(NEW_PROMPT);
    expect(detail.segment.source_revision).toBe(2);
    const logs = await (
      await fetch(`${app.url}/api/runs/${segment.id}/logs`)
    ).json();
    expect(logs.text).toContain(
      `SYNTHETIC preview complete at step 210: ${NEW_PROMPT}`,
    );
    const source = records.find((run) => run.id === original.id)!;
    expect(source.status).toBe('stopped');
    expect(source.job.revision).toBe(2);
  } finally {
    await app.close();
  }
});
