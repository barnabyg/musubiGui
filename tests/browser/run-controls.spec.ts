import { test, expect } from '@playwright/test';
import { runResume, runWorkspace, type RunRecord } from './runHarness';

test('Save now, Sample now and Save and stop act at the next training step', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const saved = await (await fetch(`${app.url}/api/jobs/${job.id}`)).json();
    // No interval save or preview falls before the end, and the Job keeps no state of its own.
    const updated = await fetch(`${app.url}/api/jobs/${job.id}`, {
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
          preview_every: 200,
          save_state: false,
        },
      }),
    });
    expect(updated.ok).toBeTruthy();
    const run = (await app.post('/api/runs/prepare', {
      job_id: job.id,
      start: true,
    })) as RunRecord;
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByText('State: Training', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });

    const actions = page.getByRole('region', { name: 'Actions' });
    for (const name of [
      'Save now',
      'Sample now',
      'Save and stop',
      'Stop now',
      'Force terminate',
    ]) {
      // Wrapped within the 375 px width rather than overflowing it.
      const box = await actions
        .getByRole('button', { name, exact: true })
        .boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(375);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);

    await actions
      .getByRole('button', { name: 'Save now', exact: true })
      .click();
    await expect(actions).toContainText(/Save now ran at step \d+\./, {
      timeout: 240_000,
    });
    await actions
      .getByRole('button', { name: 'Sample now', exact: true })
      .click();
    await expect(actions).toContainText(/Sample now ran at step \d+\./, {
      timeout: 240_000,
    });

    // Stop now says how much it would lose, and Cancel leaves training running.
    await actions
      .getByRole('button', { name: 'Stop now', exact: true })
      .click();
    await expect(page.getByRole('dialog')).toContainText('since the last save');
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Cancel' })
      .click();
    await expect(
      page.getByText('State: Training', { exact: true }),
    ).toBeVisible();

    await actions
      .getByRole('button', { name: 'Save and stop', exact: true })
      .click();
    await expect(page.getByText('State: Stopped', { exact: true })).toBeVisible(
      { timeout: 240_000 },
    );
    const detail = await (await fetch(`${app.url}/api/runs/${run.id}`)).json();
    expect(detail.stop_kind).toBe('save');
    const [save, sample, stop] = detail.controls as {
      action: string;
      step: number;
    }[];
    expect([save.action, sample.action, stop.action]).toEqual([
      'save',
      'sample',
      'save_and_stop',
    ]);
    expect(stop.step).toBeGreaterThan(sample.step);
    expect(stop.step).toBeLessThan(200);

    const resume = runResume(page);
    await resume.getByRole('button', { name: 'Resume', exact: true }).click();
    await expect(resume.getByText(/^Selected save/)).toContainText(
      `Optimizer step ${stop.step}`,
      { timeout: 240_000 },
    );
    await expect(
      resume.getByRole('button', { name: 'Review resume' }),
    ).toBeEnabled();
  } finally {
    await app.close();
  }
});
