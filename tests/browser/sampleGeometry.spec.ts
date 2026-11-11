import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';
import type { Job } from '../../frontend/jobTypes';

for (const family of ['wan', 'h3'] as const) {
  test(`${family} sample resolution and duration persist through export and reach final and preview media`, async ({
    page,
  }) => {
    const app = await runWorkspace();
    try {
      const job = app.manifest.jobs.find((item) => item.family === family)!;
      await page.goto(`${app.url}/#/jobs/${job.id}`);
      const samples = page.getByRole('region', {
        name: 'Samples configuration',
      });
      await expect(
        samples.getByLabel('Sample width', { exact: true }),
      ).toHaveValue('256');
      await samples.getByLabel('Sample width', { exact: true }).fill('320');
      await samples.getByLabel('Sample height', { exact: true }).fill('63');
      await expect(
        samples.getByLabel('Sample height', { exact: true }),
      ).toHaveAttribute('aria-invalid', 'true');
      await samples.getByLabel('Sample height', { exact: true }).fill('192');
      const duration = samples.getByLabel('Sample duration (seconds)', {
        exact: true,
      });
      if (family === 'h3') {
        await duration.fill('1');
        await expect(duration).toHaveAttribute('aria-invalid', 'true');
      }
      await duration.fill(family === 'wan' ? '2' : '6');
      const frames = family === 'wan' ? 33 : 141;
      await expect(samples).toContainText(`320 × 192 · ${frames} frames`);
      await samples
        .getByLabel('Preview every N steps', { exact: true })
        .fill('1');
      await page
        .getByRole('button', { name: 'Save draft', exact: true })
        .click();
      await expect(
        page.getByText('Draft saved — revision 2', { exact: true }),
      ).toBeVisible();
      await page.reload();
      await page.setViewportSize({ width: 700, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      await expect(duration).toHaveValue(family === 'wan' ? '2.0625' : '5.875');
      const exported = await (
        await page.request.get(`${app.url}/api/jobs/${job.id}/export`)
      ).json();
      const imported = (await app.post('/api/jobs/import', exported)) as Job;
      expect(imported.config.sample_frames).toBe(frames);
      expect(imported.config.sample_width).toBe(320);
      expect(imported.config.frames).toBe(family === 'wan' ? 17 : 124);
      expect(imported.config.width).toBe(256);
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      await expect(page.getByLabel('Configuration review')).toContainText(
        `Sample output: 320 × 192 · ${frames} frames`,
      );
      await page
        .getByRole('button', { name: 'Prepare and start', exact: true })
        .click();
      await expect(
        page.getByText('State: Completed', { exact: true }),
      ).toBeVisible({ timeout: 240_000 });
      const run = (await app.records()).find(
        (record) => record.job.id === job.id,
      )!;
      const list = (await (
        await page.request.get(`${app.url}/api/runs/${run.id}/outputs`)
      ).json()) as {
        outputs: {
          kind: string;
          status: string;
          metadata: {
            width: number;
            height: number;
            frames: number;
            fps: number;
            duration: number;
          };
        }[];
      };
      const videos = list.outputs.filter((output) => output.kind === 'video');
      expect(videos).toHaveLength(3);
      for (const output of videos) {
        expect(output.status).toBe('available');
        expect(output.metadata.width).toBe(320);
        expect(output.metadata.height).toBe(192);
        expect(output.metadata.frames).toBe(frames);
        expect(output.metadata.duration).toBeCloseTo(
          frames / (family === 'wan' ? 16 : 24),
          2,
        );
      }
      await page.getByRole('tab', { name: 'Samples', exact: true }).click();
      await expect(
        page.getByRole('button', { name: /Step 1 preview.*available/ }),
      ).toBeVisible();
    } finally {
      await app.close();
    }
  });
}

test('Wan image samples use independent geometry and remain available after switching tabs', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    const samples = page.getByRole('region', { name: 'Samples configuration' });
    await samples
      .getByLabel('Image sample (one frame)', { exact: true })
      .check();
    await samples.getByLabel('Sample width', { exact: true }).fill('320');
    await expect(
      samples.getByLabel('Sample duration (seconds)', { exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByLabel('Configuration review')).toContainText(
      '1 frames · image sample',
    );
    await page
      .getByRole('button', { name: 'Prepare and start', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    await page
      .getByRole('button', { name: 'sample.png · available', exact: true })
      .click();
    await expect(
      page.getByAltText('Sample sample.png', { exact: true }),
    ).toBeVisible();
    await page.getByRole('tab', { name: 'Overview', exact: true }).click();
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    await expect(
      page.getByAltText('Sample sample.png', { exact: true }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});
