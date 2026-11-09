import { expect, test } from '@playwright/test';
import { copyFile, mkdir, readFile, unlink } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace } from './runHarness';

test('review opens the exact same-name media item for audio and caption repairs', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((entry) => entry.family === 'h3')!;
    const source = path.join(app.fixture, 'datasets', 'h3');
    const second = path.join(app.fixture, 'datasets', 'second');
    await mkdir(second);
    await copyFile(
      path.join(source, 'motion.mp4'),
      path.join(second, 'motion.mp4'),
    );
    await copyFile(
      path.join(source, 'motion.wav'),
      path.join(second, 'chosen.wav'),
    );
    await unlink(path.join(source, 'motion.txt'));
    const dataset = (await app.post('/api/datasets', {
      name: 'Second',
      path: second,
    })) as { id: string };
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `${app.url}/api/datasets/${dataset.id}/items`,
          );
          return ((await response.json()) as { valid: boolean }[]).filter(
            (item) => item.valid,
          ).length;
        },
        { timeout: 240_000 },
      )
      .toBe(1);
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page
      .getByRole('combobox', { name: 'Add a dataset' })
      .selectOption(dataset.id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(review).toContainText('Review failed', { timeout: 240_000 });
    const audio = review.getByRole('link', {
      name: /Second.*motion.mp4: audio policy requires/,
    });
    await expect(audio).toBeVisible();
    await audio.focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('heading', { name: 'Second', exact: true }),
    ).toBeVisible();
    const audioPath = page.getByLabel('Explicit audio path');
    await expect(audioPath).toBeVisible();
    await audioPath.fill(path.join(second, 'chosen.wav'));
    await page.getByRole('button', { name: 'Apply audio source' }).click();
    await expect(page.getByLabel('H3 inspection result')).toContainText(
      'Audio source: explicit',
      { timeout: 240_000 },
    );
    const back = page.getByRole('button', { name: /Return to job and review/ });
    await back.click();
    await expect(page).toHaveURL(`${app.url}/#/jobs/${job.id}`);
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    const warning = /Second.*motion.mp4: no caption sidecar/;
    await review.getByRole('link', { name: warning }).press('Enter');
    const caption = page.getByRole('textbox', { name: 'Caption', exact: true });
    await expect(caption).toBeFocused();
    await caption.fill('The second clip has a steady tone.');
    await back.click();
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    await expect(review.getByRole('link', { name: warning })).toHaveCount(0);
    await expect(
      review.getByRole('link', {
        name: /H3 acceptance.*motion.mp4: no caption sidecar/,
      }),
    ).toBeVisible();
    expect(await readFile(path.join(second, 'motion.txt'), 'utf8')).toBe(
      'The second clip has a steady tone.',
    );
  } finally {
    await app.close();
  }
});
