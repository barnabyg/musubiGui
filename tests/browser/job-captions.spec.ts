import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('preparation waits for selected dataset captions and freezes the resolved draft', async ({
  page,
}) => {
  const app = await runWorkspace();
  let release = () => {};
  try {
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    const h3 = app.manifest.jobs.find((job) => job.family === 'h3')!;
    await page.goto(`${app.url}/#/datasets/${wan.dataset_id}`);
    const caption = page.getByLabel('Caption for motion.mp4', { exact: true });
    await expect(caption).toHaveValue(/blue field/);
    await writeFile(
      path.join(app.fixture, 'datasets/wan/motion.txt'),
      'External edit',
    );
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT') await held;
      await route.continue();
    });
    const draft = 'Motion caption resolved in the editor';
    await caption.fill(draft);
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    const chooseJob = async (job: typeof wan) => {
      await page
        .getByRole('button', {
          name: `${job.name} · ${job.family.toUpperCase()} · r1`,
        })
        .click();
      await page.getByRole('button', { name: 'Review configuration' }).click();
      await expect(
        page.getByText('Review passed for revision 1'),
      ).toBeVisible();
    };
    await chooseJob(wan);
    const prepare = page.getByRole('button', {
      name: 'Prepare run',
      exact: true,
    });
    const start = page.getByRole('button', {
      name: 'Prepare and start',
      exact: true,
    });
    await expect(prepare).toBeDisabled();
    await expect(start).toBeDisabled();
    release();
    const resolveCaption = page.getByRole('link', {
      name: 'Resolve caption for motion.mp4',
    });
    await expect(resolveCaption).toBeVisible();
    await expect(prepare).toBeDisabled();
    await expect(start).toBeDisabled();
    expect(await app.records()).toHaveLength(0);

    await chooseJob(h3);
    await prepare.click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await chooseJob(wan);
    await resolveCaption.click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      draft,
    );
    await page.getByRole('button', { name: 'Review current sidecar' }).click();
    await expect(
      page.getByText('External edit', { exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Save my text over this revision' })
      .click();
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await chooseJob(wan);
    await prepare.click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const run = (await app.records()).find(
      (record) => record.job.id === wan.id,
    );
    expect(run?.manifest.items[0].caption.text).toBe(draft);
  } finally {
    release();
    await app.close();
  }
});
