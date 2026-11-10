import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('paired Wan previews show total steps and their own expert counts', async ({
  page,
}) => {
  const app = await runWorkspace({ previewImages: true });
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const saved = (await (
      await page.request.get(`${app.url}/api/jobs/${job.id}`)
    ).json()) as { config: Record<string, unknown> } & Record<string, unknown>;
    const updated = await page.request.put(`${app.url}/api/jobs/${job.id}`, {
      data: {
        ...saved,
        version: undefined,
        id: undefined,
        config: {
          ...saved.config,
          recipe: 'alternating',
          expert_interval: 5,
          steps: 24,
          preview_every: 4,
        },
      },
    });
    expect(updated.ok()).toBe(true);
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await expect(
      page.getByText(/Extra boundaries restart the shuffled dataset pass/),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Prepare and start', exact: true })
      .click();
    const first = page.getByRole('button', {
      name: /^Step 4 preview · paired Wan · low 4 \/ high 0 trained steps.*available$/,
    });
    await expect(first).toBeVisible({ timeout: 240_000 });
    await first.click();
    const playing = page.getByLabel('Output video', { exact: true });
    const firstSource = await playing.getAttribute('src');
    await playing.evaluate(async (element) => {
      element.setAttribute('data-preview-kept', 'yes');
      const media = element as HTMLVideoElement;
      media.muted = true;
      media.loop = true;
      await media.play();
    });
    await expect(
      page.getByRole('button', {
        name: /^Step 12 preview · paired Wan · low 7 \/ high 5 trained steps.*available$/,
      }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(first).toHaveAttribute('aria-pressed', 'true');
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const retained = page.locator(`video[src="${firstSource}"]`);
    await expect(retained).toHaveAttribute('data-preview-kept', 'yes');
    await expect
      .poll(() =>
        retained.evaluate((element) => !(element as HTMLVideoElement).paused),
      )
      .toBe(true);
    await expect(
      page.getByRole('button', {
        name: /^Step 24 preview · paired Wan · low 14 \/ high 10 trained steps.*available$/,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole('link', { name: 'Download low-noise LoRA', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('link', { name: 'Download high-noise LoRA', exact: true }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

for (const family of ['wan', 'h3'] as const) {
  test(`${family} image-run previews appear automatically and polling keeps a selected video playing`, async ({
    page,
  }) => {
    const app = await runWorkspace({ previewImages: true });
    try {
      const job = app.manifest.jobs.find((item) => item.family === family)!;
      await page.goto(`${app.url}/#/jobs/${job.id}`);
      await page
        .getByRole('spinbutton', { name: 'Training steps', exact: true })
        .fill('60');
      await page
        .getByRole('spinbutton', { name: 'Preview every N steps', exact: true })
        .fill('5');
      await page
        .getByRole('checkbox', { name: 'Generate a final sample', exact: true })
        .uncheck();
      await expect(
        page.getByRole('textbox', { name: 'Sample prompt' }),
      ).toBeVisible();
      const saved = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/jobs/${job.id}`) &&
          response.request().method() === 'PUT',
      );
      await page
        .getByRole('button', { name: 'Save draft', exact: true })
        .click();
      expect((await saved).ok()).toBe(true);
      const run = (await app.post('/api/runs/prepare', {
        job_id: job.id,
        start: true,
      })) as { id: string };
      await page.goto(`${app.url}/#/runs/${run.id}`);
      const first = page.getByRole('button', {
        name: /^Step 5 preview.*available$/,
      });
      await expect(first).toBeVisible({ timeout: 240_000 });
      await expect(
        page.getByText('State: Training', { exact: true }),
      ).toBeVisible();
      await first.click();
      const video = page.getByLabel('Output video', { exact: true });
      await video.evaluate(async (element) => {
        element.setAttribute('data-kept', 'yes');
        const media = element as HTMLVideoElement;
        media.muted = true;
        media.loop = true;
        await media.play();
      });
      await expect(
        page.getByRole('button', { name: /^Step 20 preview.*available$/ }),
      ).toBeVisible({ timeout: 240_000 });
      await expect(first).toHaveAttribute('aria-pressed', 'true');
      await expect(video).toHaveAttribute('data-kept', 'yes');
      await expect
        .poll(() =>
          video.evaluate((element) => !(element as HTMLVideoElement).paused),
        )
        .toBe(true);
      await app.post(`/api/runs/${run.id}/stop`);
      await expect
        .poll(
          async () =>
            (await app.records()).find((item) => item.id === run.id)?.status,
          { timeout: 240_000 },
        )
        .toBe('stopped');
      await expect(first).toBeVisible();
    } finally {
      await app.close();
    }
  });
}
