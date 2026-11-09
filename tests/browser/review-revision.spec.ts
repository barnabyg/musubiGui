import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('a second tab changing a reviewed job blocks both prepare actions until the new revision is reviewed', async ({
  page,
  context,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const writer = await context.newPage();
    const url = `${app.url}/#/jobs/${job.id}`;
    await Promise.all([page.goto(url), writer.goto(url)]);
    const review = page.getByRole('button', { name: 'Review configuration' });
    await review.click();
    await expect(page.getByText('Review passed for revision 1')).toBeVisible();

    for (const [index, action] of [
      'Prepare and start',
      'Prepare run',
    ].entries()) {
      const revision = index + 2;
      const name = `Renamed in tab B ${revision}`;
      await writer.getByLabel('Job name').fill(name);
      await writer.getByRole('button', { name: 'Save draft' }).click();
      await expect(
        writer.getByText(`Draft saved — revision ${revision}`),
      ).toBeVisible();

      await page.getByRole('button', { name: action, exact: true }).click();
      await expect(page.getByRole('alert')).toContainText(
        `changed from revision ${revision - 1} to revision ${revision}`,
      );
      await expect(
        page.getByRole('button', { name: 'Prepare run', exact: true }),
      ).toBeDisabled();
      expect(await app.records()).toHaveLength(0);

      await review.click();
      await expect(
        page.getByText(`Review passed for revision ${revision}`),
      ).toBeVisible();
      await expect(page.getByLabel('Job name')).toHaveValue(name);
    }

    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Run history' }),
    ).toBeVisible();
    const recorded = page.getByRole('button', {
      name: 'Renamed in tab B 3 · WAN · r3',
      exact: true,
    });
    await expect(recorded).toHaveAttribute('aria-pressed', 'true');
    expect((await app.records()).map((run) => run.job)).toMatchObject([
      { id: job.id, name: 'Renamed in tab B 3', revision: 3 },
    ]);
  } finally {
    await app.close();
  }
});
