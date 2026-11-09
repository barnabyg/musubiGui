import { expect, test } from '@playwright/test';
import type { Job } from '../../frontend/jobTypes';
import { runWorkspace } from './runHarness';

test('job and Run links preserve identity, frozen revisions and downloads through history and archiving', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const jobs = app.manifest.jobs.filter((job) => job.family === 'wan');
    const [first, second] = jobs;
    const firstRun = (await app.post('/api/runs/prepare', {
      job_id: first.id,
      start: true,
    })) as { id: string };
    await expect
      .poll(
        async () =>
          (await app.records()).find((run) => run.id === firstRun.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('completed');
    const secondRun = (await app.post('/api/runs/prepare', {
      job_id: second.id,
    })) as { id: string };
    await expect
      .poll(
        async () =>
          (await app.records()).find((run) => run.id === secondRun.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('prepared');

    await page.goto(`${app.url}/#/jobs`);
    const table = page.getByRole('table', { name: 'Saved jobs' });
    for (const [job, run] of [
      [first, firstRun],
      [second, secondRun],
    ] as const) {
      const row = table.getByRole('row').filter({
        has: page.getByRole('button', {
          name: `${job.name} · WAN · r1`,
          exact: true,
        }),
      });
      const link = row.getByRole('link', { name: /Open last Run/ });
      await expect(link).toHaveAttribute('href', `#/runs/${run.id}`);
      await link.focus();
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(`${app.url}/#/runs/${run.id}`);
      const origin = page.getByRole('region', { name: 'Originating job' });
      await expect(origin).toContainText(`Frozen for this Run: ${job.name}`);
      await expect(origin).toContainText('WAN · r1');
      const current = origin.getByRole('link', { name: /Open current job/ });
      await expect(current).toHaveAttribute('href', `#/jobs/${job.id}`);
      await current.press('Enter');
      await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
        job.name,
      );
      await page.goBack();
      await expect(page).toHaveURL(`${app.url}/#/runs/${run.id}`);
      await page.goBack();
      await expect(page).toHaveURL(`${app.url}/#/jobs`);
    }

    const savedJobs = async () =>
      (await (await page.request.get(`${app.url}/api/jobs`)).json()) as Job[];
    expect(
      (await savedJobs())
        .filter((job) => jobs.some(({ id }) => id === job.id))
        .map((job) => job.revision),
    ).toEqual([1, 1]);
    expect((await app.records()).map((run) => run.status).sort()).toEqual([
      'completed',
      'prepared',
    ]);

    await page.goto(`${app.url}/#/runs/${firstRun.id}`);
    const origin = page.getByRole('region', { name: 'Originating job' });
    await origin.getByRole('link', { name: /Open current job/ }).click();
    await page
      .getByLabel('Job name', { exact: true })
      .fill('WAN acceptance revised');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('revision 2');
    await page.goBack();
    await expect(origin).toContainText(
      `Frozen for this Run: ${first.name} · WAN · r1`,
    );
    await expect(
      origin.getByRole('link', { name: /Open current job/ }),
    ).toContainText('WAN acceptance revised · WAN · r2');
    const download = page.getByRole('link', {
      name: 'Download final LoRA',
      exact: true,
    });
    await expect(download).toBeVisible();
    const downloadFile = page.waitForEvent('download');
    await download.click();
    const file = await downloadFile;
    expect(file.suggestedFilename()).toMatch(/\.safetensors$/);
    expect(await file.failure()).toBeNull();

    await app.post(`/api/jobs/${first.id}/archive`);
    await page.reload();
    await expect(origin).toContainText('Archived job');
    await expect(origin).toContainText('WAN acceptance revised · WAN · r2');
    const archived = origin.getByRole('link', {
      name: /Restore in Archived jobs/,
    });
    await archived.press('Enter');
    await expect(page).toHaveURL(`${app.url}/#/jobs`);
    await page.getByText(/^Archived jobs \(/).click();
    await expect(
      page.getByRole('button', {
        name: 'Restore WAN acceptance revised',
        exact: true,
      }),
    ).toBeVisible();
    await page.goBack();
    await expect(download).toBeVisible();

    // Older or externally changed state can leave a Run without an available job.
    // Exercise these HTTP boundary failures without deleting the job's saved history.
    await page.route('**/api/jobs', (route) => route.fulfill({ json: [] }));
    await page.route('**/api/jobs/archived', (route) =>
      route.fulfill({ json: [] }),
    );
    await page.reload();
    await expect(origin).toContainText('This job is no longer available.');
    await expect(origin).toContainText(`${first.name} · WAN · r1`);
    const unavailableDownload = page.waitForEvent('download');
    await download.click();
    expect(await (await unavailableDownload).failure()).toBeNull();

    await page.unroute('**/api/jobs');
    await page.unroute('**/api/jobs/archived');
    await page.route('**/api/jobs/archived', (route) =>
      route.fulfill({
        status: 503,
        json: { detail: 'Job storage temporarily unavailable.' },
      }),
    );
    await page.reload();
    await expect(origin).toContainText('Job storage temporarily unavailable.');
    await expect(download).toBeVisible();
    await page.unroute('**/api/jobs/archived');
    await origin.getByRole('button', { name: 'Retry job lookup' }).click();
    await expect(origin).toContainText('Archived job');
    expect((await app.records()).map((run) => run.status).sort()).toEqual([
      'completed',
      'prepared',
    ]);
  } finally {
    await app.close();
  }
});
