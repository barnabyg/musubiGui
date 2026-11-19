import { expect, test } from '@playwright/test';
import type { Job } from '../../frontend/jobTypes';
import { openRunDiagnostics, runWorkspace } from './runHarness';

test('job and Run links preserve identity, frozen revisions and the output folder through history and archiving', async ({
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
      await expect(
        page.getByRole('heading', { name: job.name, exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText(`Run ${run.id.slice(0, 8)}`, { exact: true }),
      ).toBeVisible();
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

    await page.goto(`${app.url}/#/jobs/${first.id}`);
    await page
      .getByLabel('Job name', { exact: true })
      .fill('WAN acceptance revised');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('revision 2');

    const record = (await app.records()).find((run) => run.id === firstRun.id)!;
    await app.post(`/api/jobs/${first.id}/archive`);
    await page.goto(`${app.url}/#/runs/${firstRun.id}`);
    const heading = page.getByRole('heading', {
      name: first.name,
      exact: true,
    });
    const folder = page.getByLabel('Output folder', { exact: true });
    await expect(heading).toBeVisible();
    await openRunDiagnostics(page);
    await expect(folder).toHaveText(record.directory);
    await page.reload();
    await expect(heading).toBeVisible();
    await openRunDiagnostics(page);
    await expect(folder).toHaveText(record.directory);
    expect((await app.records()).map((run) => run.status).sort()).toEqual([
      'completed',
      'prepared',
    ]);
  } finally {
    await app.close();
  }
});
