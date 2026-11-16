import { test, expect } from '@playwright/test';
import { writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { openRunTab, runWorkspace } from './runHarness';

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
      await openRunTab(page, 'samples');
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
      await openRunTab(page, 'overview');
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
