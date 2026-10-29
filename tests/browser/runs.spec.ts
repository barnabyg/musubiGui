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
      await page
        .getByRole('button', { name: 'Inspect saved states', exact: true })
        .click();
      await expect(page.getByText(/optimizer step 2/)).toBeVisible();
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
