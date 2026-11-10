import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('in-run previews appear automatically and polling keeps a selected video playing', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
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
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
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
