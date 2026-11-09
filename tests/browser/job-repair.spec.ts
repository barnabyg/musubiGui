import { expect, test } from '@playwright/test';
import { copyFile, mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
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
    const back = page.getByRole('button', { name: /Return to job and review/ });
    for (const name of ['H3 acceptance', 'Second']) {
      const copy = review.getByRole('link', {
        name: `motion.mp4 in "${name}"`,
        exact: true,
      });
      await copy.press('Enter');
      await expect(
        page.getByRole('heading', { name, exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole('heading', { name: 'motion.mp4', exact: true }),
      ).toBeVisible();
      await back.click();
      await expect(review).toContainText('Review failed', { timeout: 240_000 });
    }
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

test('H3 repair inspects and applies audio against the reviewed frame count', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((entry) => entry.family === 'h3')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page.getByText('Advanced controls', { exact: true }).click();
    await page
      .getByRole('spinbutton', { name: 'Frames', exact: true })
      .fill('141');
    await page.getByRole('button', { name: 'Review configuration' }).click();
    const missing = /Audio is materially short or misaligned/;
    const problem = page.getByRole('link', { name: missing });
    await problem.press('Enter');
    await expect(
      page.getByText(/Inspect the selected 141-frame/),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Inspect H3 media' }).click();
    const result = page.getByLabel('H3 inspection result');
    await expect(result).toContainText(missing, { timeout: 240_000 });
    await page
      .getByLabel('Explicit audio path')
      .fill(path.join(app.fixture, 'datasets', 'h3', 'motion.wav'));
    await page.getByRole('button', { name: 'Apply audio source' }).click();
    await expect(result).toContainText('Audio source: explicit', {
      timeout: 240_000,
    });
    await expect(result).toContainText(missing);
    await page
      .getByRole('button', { name: /Return to job and review/ })
      .click();
    await expect(problem).toBeVisible({ timeout: 240_000 });
  } finally {
    await app.close();
  }
});

test('a repair link refreshes the open dataset after a rescan and keeps its failed caption', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    const folder = path.join(app.fixture, 'datasets/wan');
    await page.goto(`${app.url}/#/datasets/${wan.dataset_id}`);
    const caption = page.getByLabel('Caption for motion.mp4', { exact: true });
    await expect(caption).toHaveValue(/blue field/);
    await writeFile(path.join(folder, 'motion.txt'), 'New text on disk');
    await caption.fill('Kept through the rescan');
    const jobs = page.getByRole('button', { name: 'Jobs', exact: true });
    await jobs.click();

    await copyFile(
      path.join(folder, 'motion.mp4'),
      path.join(folder, 'new.mp4'),
    );
    await app.post(`/api/datasets/${wan.dataset_id}/scan`);
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `${app.url}/api/datasets/${wan.dataset_id}/items`,
          );
          const items = (await response.json()) as {
            name: string;
            valid: boolean;
          }[];
          return items.some((item) => item.name === 'new.mp4' && item.valid);
        },
        { timeout: 240_000 },
      )
      .toBe(true);
    const choose = page.getByRole('button', { name: `${wan.name} · WAN · r1` });
    await choose.click();
    await page.getByRole('button', { name: 'Review configuration' }).click();
    const warning = page.getByRole('link', {
      name: /new.mp4: no caption sidecar/,
    });
    await expect(warning).toBeVisible({ timeout: 240_000 });
    await warning.click();
    await expect(
      page.getByRole('heading', { name: 'new.mp4', exact: true }),
    ).toBeVisible();
    const editor = page.getByLabel('Caption', { exact: true });
    await expect(editor).toBeFocused();
    await jobs.click();
    await choose.click();
    await page
      .getByRole('link', { name: 'Resolve caption for motion.mp4' })
      .click();
    await expect(editor).toHaveValue('Kept through the rescan');
  } finally {
    await app.close();
  }
});
