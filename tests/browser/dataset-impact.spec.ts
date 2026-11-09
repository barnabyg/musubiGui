import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace } from './runHarness';

test.use({ actionTimeout: 240_000 });

test('returning from a new training draft refreshes the affected jobs before exclusion', async ({
  page,
}) => {
  const app = await runWorkspace();
  let release = () => {};
  try {
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    await page.goto(`${app.url}/#/datasets/${wan.dataset_id}`);
    await page
      .getByRole('button', { name: 'Bulk actions', exact: true })
      .click();
    const impact = page.getByRole('region', {
      name: 'Jobs using this dataset',
    });
    await expect(impact.getByRole('link')).toHaveCount(2);
    await page.getByText('Create training job', { exact: true }).click();
    await page
      .getByRole('button', { name: 'Create Wan job', exact: true })
      .click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'WAN acceptance · Wan',
    );
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/jobs', async (route) => {
      await held;
      await route.continue();
    });
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await expect(impact).toContainText('Loading affected jobs…');
    await page.getByLabel('Select motion.mp4', { exact: true }).focus();
    await page.keyboard.press('Space');
    await page
      .getByText('Selection and bulk caption actions', { exact: true })
      .click();
    const exclude = page.getByRole('button', {
      name: 'Exclude selection',
      exact: true,
    });
    await expect(exclude).toBeDisabled();
    release();
    await expect(impact.getByRole('link')).toHaveCount(3);
    await expect(
      impact.getByRole('link', {
        name: 'WAN acceptance · Wan · WAN · r1',
        exact: true,
      }),
    ).toBeVisible();
    await expect(exclude).toBeEnabled();
  } finally {
    release();
    await app.close();
  }
});

test('dataset inclusion and removal name dependent drafts while prepared Runs keep their snapshot', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.filter((job) => job.family === 'wan');
    await page.goto(`${app.url}/#/jobs/${wan[0].id}`);
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      wan[0].name,
    );
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(
      page.getByText('Review passed for revision 1', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const run = (await app.records())[0];
    await page.goto(`${app.url}/#/datasets/${wan[0].dataset_id}`);
    await page
      .getByRole('button', { name: 'Bulk actions', exact: true })
      .click();
    const impact = page.getByRole('region', {
      name: 'Jobs using this dataset',
    });
    await expect(impact).toContainText('Prepared Runs retain their snapshot');
    for (const job of wan)
      await expect(
        impact.getByRole('link', {
          name: `${job.name} · WAN · r1`,
          exact: true,
        }),
      ).toHaveAttribute('href', `#/jobs/${job.id}`);
    await page.getByLabel('Select motion.mp4', { exact: true }).check();
    await page
      .getByText('Selection and bulk caption actions', { exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Exclude selection', exact: true })
      .click();
    await expect(
      page.getByText(
        'Selection excluded. Choose Include selection to reverse this change.',
        { exact: true },
      ),
    ).toBeVisible();
    for (const job of wan) {
      const review = (await app.post(`/api/jobs/${job.id}/review`)) as {
        valid: boolean;
        effective_count: number;
      };
      expect(review.valid).toBe(false);
      expect(review.effective_count).toBe(0);
    }
    await page
      .getByRole('button', { name: 'Include selection', exact: true })
      .click();
    await expect(
      page.getByText('Selection included.', { exact: true }),
    ).toBeVisible();
    for (const job of wan) {
      const review = (await app.post(`/api/jobs/${job.id}/review`)) as {
        valid: boolean;
        effective_count: number;
      };
      expect(review.valid).toBe(true);
      expect(review.effective_count).toBe(1);
    }
    await page
      .getByRole('button', { name: 'Back to datasets', exact: true })
      .click();
    await page
      .getByRole('button', {
        name: 'Remove WAN acceptance from library',
        exact: true,
      })
      .click();
    const dialog = page.getByRole('dialog', {
      name: 'Remove "WAN acceptance" from the dataset library?',
    });
    await expect(dialog).toBeVisible();
    for (const job of wan)
      await expect(
        dialog.getByRole('link', {
          name: `${job.name} · WAN · r1`,
          exact: true,
        }),
      ).toBeVisible();
    await expect(dialog).toContainText(
      'Media files, captions and recovery history stay on disk',
    );
    await expect(
      dialog.getByRole('button', { name: 'Cancel', exact: true }),
    ).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('link').last()).toBeFocused();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page
      .getByRole('button', {
        name: 'Remove WAN acceptance from library',
        exact: true,
      })
      .click();
    await dialog
      .getByRole('button', { name: 'Remove from library', exact: true })
      .click();
    await expect(
      page.getByText(
        'Removed "WAN acceptance" from the library. Files are preserved.',
        { exact: true },
      ),
    ).toBeVisible();
    expect(
      (await readFile(path.join(app.fixture, 'datasets/wan/motion.mp4')))
        .length,
    ).toBeGreaterThan(0);
    expect(
      await readFile(path.join(app.fixture, 'datasets/wan/motion.txt'), 'utf8'),
    ).toContain('blue field');
    expect(
      (await app.records()).find((record) => record.id === run.id)?.manifest
        .items,
    ).toHaveLength(1);
    for (const job of wan) {
      await page.goto(`${app.url}/#/jobs/${job.id}`);
      await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
        job.name,
      );
      await expect(
        page
          .getByRole('list', { name: 'Datasets in this job' })
          .getByText('Deleted dataset', { exact: true }),
      ).toBeVisible();
    }
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(
      page.getByRole('link', { name: 'Download final LoRA', exact: true }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('dataset impact distinguishes archived jobs, recovers failed lookups and explains an unused dataset', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.filter((job) => job.family === 'wan');
    await app.post(`/api/jobs/${wan[1].id}/archive`);
    await page.route('**/api/jobs', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'Affected jobs temporarily unavailable.',
        }),
      });
    });
    await page.goto(`${app.url}/#/datasets/${wan[0].dataset_id}`);
    await page
      .getByRole('button', { name: 'Bulk actions', exact: true })
      .click();
    const impact = page.getByRole('region', {
      name: 'Jobs using this dataset',
    });
    await expect(impact).toContainText(
      'Affected jobs temporarily unavailable.',
    );
    await page.getByLabel('Select motion.mp4', { exact: true }).check();
    await page
      .getByText('Selection and bulk caption actions', { exact: true })
      .click();
    const exclude = page.getByRole('button', {
      name: 'Exclude selection',
      exact: true,
    });
    await expect(exclude).toBeDisabled();
    await page.unroute('**/api/jobs');
    await impact
      .getByRole('button', { name: 'Retry affected jobs', exact: true })
      .click();
    await expect(impact).toContainText('Active draft');
    await expect(impact).toContainText('Archived; restore in Archived jobs');
    const active = impact.getByRole('link', {
      name: `${wan[0].name} · WAN · r1`,
      exact: true,
    });
    await expect(active).toHaveAttribute('href', `#/jobs/${wan[0].id}`);
    await expect(
      impact.getByRole('link', {
        name: `${wan[1].name} · WAN · r1`,
        exact: true,
      }),
    ).toHaveAttribute('href', '#/jobs');
    await expect(exclude).toBeEnabled();
    await active.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      wan[0].name,
    );
    expect(
      (await (
        await page.request.get(`${app.url}/api/jobs/${wan[0].id}`)
      ).json()) as { revision: number },
    ).toMatchObject({ revision: 1 });
    const settings = (await (
      await page.request.get(`${app.url}/api/settings`)
    ).json()) as { dataset_roots: string[] };
    const unused = (await (
      await page.request.post(`${app.url}/api/datasets/managed`, {
        data: { name: 'Unused dataset', root: settings.dataset_roots[0] },
      })
    ).json()) as { id: string };
    await page.goto(`${app.url}/#/datasets/${unused.id}`);
    await expect(
      page.getByRole('heading', { name: 'Unused dataset', exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    await expect(impact).toContainText('No saved jobs reference this dataset.');
    expect(await app.records()).toHaveLength(0);
  } finally {
    await app.close();
  }
});
