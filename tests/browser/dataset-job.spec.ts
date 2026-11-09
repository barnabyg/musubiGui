import { expect, test } from '@playwright/test';
import { mkdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace } from './runHarness';

test.use({ actionTimeout: 240_000 });

test('a failed caption save keeps the gallery draft and prevents creating a job', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    await page.goto(`${app.url}/#/datasets/${wan.dataset_id}`);
    const caption = page.getByLabel('Caption for motion.mp4', { exact: true });
    await expect(caption).toHaveValue(/blue field/);
    await writeFile(
      path.join(app.fixture, 'datasets/wan/motion.txt'),
      'External caption',
    );
    await caption.fill('Retained caption draft');
    await page.getByText('Create training job', { exact: true }).click();
    await page
      .getByRole('button', { name: 'Create Wan job', exact: true })
      .click();
    await expect(
      page.getByText(
        'Resolve unsaved captions before creating a training job.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'Retained caption draft',
    );
    expect(
      await (await page.request.get(`${app.url}/api/jobs`)).json(),
    ).toHaveLength(4);
  } finally {
    await app.close();
  }
});

test('empty and missing dataset folders explain why a job cannot start', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const folder = path.join(app.fixture, 'datasets/empty');
    await mkdir(folder);
    const empty = (await (
      await page.request.post(`${app.url}/api/datasets`, {
        data: { name: 'Empty dataset', path: folder },
      })
    ).json()) as { id: string };
    await app.post(`/api/datasets/${empty.id}/scan?wait=true`);
    await page.goto(`${app.url}/#/datasets/${empty.id}`);
    await page.getByText('Create training job', { exact: true }).click();
    await expect(
      page.getByText(
        'Add valid media or include excluded media, then re-scan if needed.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Create Wan job', exact: true }),
    ).toBeDisabled();
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    await rename(
      path.join(app.fixture, 'datasets/wan'),
      path.join(app.fixture, 'datasets/wan-moved'),
    );
    await page.goto(`${app.url}/#/datasets/${wan.dataset_id}`);
    await expect(
      page.getByRole('heading', { name: 'WAN acceptance', exact: true }),
    ).toBeVisible();
    await page.getByText('Create training job', { exact: true }).click();
    await expect(
      page.getByText(
        'Restore the missing folder and re-scan in Dataset settings.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Create H3 job', exact: true }),
    ).toBeDisabled();
    expect(
      await (await page.request.get(`${app.url}/api/jobs`)).json(),
    ).toHaveLength(4);
  } finally {
    await app.close();
  }
});

test('the H3 handoff waits for caption publication and creates no duplicate when revisiting the gallery', async ({
  page,
}) => {
  const app = await runWorkspace();
  let release = () => {};
  try {
    const h3 = app.manifest.jobs.find((job) => job.family === 'h3')!;
    await page.goto(`${app.url}/#/datasets/${h3.dataset_id}`);
    const caption = page.getByLabel('Caption for motion.mp4', { exact: true });
    await expect(caption).toHaveValue(/blue field/);
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT') await held;
      await route.continue();
    });
    await caption.fill('Published before the H3 draft.');
    await page.getByText('Create training job', { exact: true }).click();
    await page
      .getByRole('button', { name: 'Create H3 job', exact: true })
      .click();
    await expect(
      page.getByText('Saving captions and creating draft…'),
    ).toBeVisible();
    expect(
      await (await page.request.get(`${app.url}/api/jobs`)).json(),
    ).toHaveLength(4);
    release();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'H3 acceptance · H3',
    );
    const newUrl = page.url();
    await page.goBack();
    await expect(
      page.getByRole('heading', { name: 'H3 acceptance', exact: true }),
    ).toBeVisible();
    await page.goForward();
    await expect(page).toHaveURL(newUrl);
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'H3 acceptance · H3',
    );
    expect(
      await (await page.request.get(`${app.url}/api/jobs`)).json(),
    ).toHaveLength(5);
  } finally {
    release();
    await app.close();
  }
});

test('a dataset hands saved captions to one independent Wan draft and keeps ordered datasets and repeats', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    const h3 = app.manifest.jobs.find((job) => job.family === 'h3')!;
    await page.goto(`${app.url}/#/datasets/${wan.dataset_id}`);
    await page
      .getByLabel('Caption for motion.mp4', { exact: true })
      .fill('Caption saved before starting a new job.');
    const handoff = page.getByText('Create training job', { exact: true });
    await expect(handoff).toBeVisible();
    await handoff.focus();
    await page.keyboard.press('Enter');
    await page
      .getByRole('button', { name: 'Create Wan job', exact: true })
      .focus();
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'WAN acceptance · Wan',
    );
    const rows = page
      .getByRole('list', { name: 'Datasets in this job' })
      .getByRole('listitem');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('WAN acceptance');
    await page
      .getByRole('combobox', { name: 'Add a dataset' })
      .selectOption(h3.dataset_id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    await page
      .getByLabel('Repeats for WAN acceptance', { exact: true })
      .fill('3');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(
      page.getByText('Draft saved — revision 2', { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText('WAN acceptance');
    await expect(rows.nth(1)).toContainText('H3 acceptance');
    await expect(
      page.getByLabel('Repeats for WAN acceptance', { exact: true }),
    ).toHaveValue('3');
    const jobs = (await (
      await page.request.get(`${app.url}/api/jobs`)
    ).json()) as { id: string; name: string; revision: number }[];
    expect(jobs).toHaveLength(5);
    expect(jobs.find((job) => job.id === wan.id)?.revision).toBe(1);
    const items = (await (
      await page.request.get(`${app.url}/api/datasets/${wan.dataset_id}/items`)
    ).json()) as { caption: { text: string } }[];
    expect(items[0].caption.text).toBe(
      'Caption saved before starting a new job.',
    );
  } finally {
    await app.close();
  }
});
