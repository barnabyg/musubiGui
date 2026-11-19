import { test, expect } from '@playwright/test';
import {
  openRunTab,
  preparedRun,
  PNG,
  runSummary,
  runTab,
  runWorkspace,
} from './runHarness';

test('outputs appear when a Run completes and a playing preview survives a refresh', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await app.post('/api/runs/prepare', { job_id: job.id });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(page.getByRole('button', { name: 'View sample' })).toHaveCount(
      0,
    );
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    const sample = page.getByRole('button', {
      name: 'sample.mp4 · available',
      exact: true,
    });
    await openRunTab(page, 'samples');
    await expect(sample).toBeVisible({ timeout: 240_000 });
    await sample.click();
    const video = page.getByLabel('Output video', { exact: true });
    await video.evaluate(async (element) => {
      const media = element as HTMLVideoElement;
      element.setAttribute('data-kept', 'yes');
      media.muted = true;
      media.loop = true;
      await media.play();
    });
    await openRunTab(page, 'overview');
    await expect(
      page.getByRole('button', { name: 'Refresh outputs', exact: true }),
    ).toBeHidden();
    await openRunTab(page, 'samples');
    await page
      .getByRole('button', { name: 'Refresh outputs', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Refresh outputs', exact: true }),
    ).toBeEnabled();
    await expect(sample).toHaveAttribute('aria-pressed', 'true');
    await expect(video).toHaveAttribute('data-kept', 'yes');
    await expect
      .poll(() =>
        video.evaluate((element) => !(element as HTMLVideoElement).paused),
      )
      .toBe(true);
  } finally {
    await app.close();
  }
});

test('a finished Run shows elapsed time in Overview and preserves playback across tabs', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    await page.setViewportSize({ width: 1280, height: 720 });
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await app.post('/api/runs/prepare', { job_id: job.id });
    await expect
      .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
      .toBe('prepared');
    const [run] = await app.records();
    await app.post(`/api/runs/${run.id}/start`);
    await expect
      .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
      .toBe('completed');
    await page.goto(`${app.url}/#/runs/${run.id}`);

    await expect(page.getByRole('region', { name: 'Run result' })).toHaveCount(
      0,
    );
    await expect(page.getByLabel('Output video', { exact: true })).toHaveCount(
      0,
    );

    await expect(runSummary(page).getByLabel('Run elapsed')).toBeInViewport();
    await expect(runSummary(page)).not.toContainText(
      /ETA|Estimated Run remaining/,
    );
    await expect(runTab(page, 'samples')).toBeInViewport();
    await openRunTab(page, 'samples');
    const sample = page.getByRole('button', {
      name: 'sample.mp4 · available',
      exact: true,
    });
    await sample.click();
    const video = page.getByLabel('Output video', { exact: true });
    await expect(video).toBeVisible();
    await video.evaluate((element) =>
      element.setAttribute('data-tab-kept', 'yes'),
    );
    await openRunTab(page, 'overview');
    await expect(video).toBeHidden();
    await runTab(page, 'overview').press('ArrowRight');
    await runTab(page, 'overview').press('ArrowRight');
    await expect(runTab(page, 'samples')).toBeFocused();
    await expect(video).toHaveAttribute('data-tab-kept', 'yes');
    await page.setViewportSize({ width: 700, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  } finally {
    await app.close();
  }
});

function outputList(
  run: { id: string },
  status: string,
  outputs: Record<string, unknown>[],
) {
  return {
    run_id: run.id,
    job: { name: 'Mocked', family: 'wan', revision: 1 },
    status,
    warnings: [],
    note: 'These files belong to this Run.',
    outputs: outputs.map((output) => ({
      run_id: run.id,
      file_url: '/nowhere',
      bytes: 2000,
      ...output,
    })),
  };
}

test('a damaged final LoRA is called out in the Run summary', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const { run } = await preparedRun(app);
    await page.route('**/api/runs/*/outputs', (route) =>
      route.fulfill({
        json: outputList(run, 'completed', [
          {
            id: 'final',
            name: 'wan.safetensors',
            path: 'wan.safetensors',
            kind: 'checkpoint',
            status: 'incomplete',
            error: 'Output is empty.',
          },
        ]),
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(runSummary(page)).toContainText(
      'wan.safetensors: Output is empty.',
    );
    await expect(
      page.getByRole('button', { name: /wan\.safetensors/ }),
    ).toHaveCount(0);
  } finally {
    await app.close();
  }
});

test('a partial Run shows the outputs it has, with their problems', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const { run } = await preparedRun(app);
    await page.route('**/api/runs/*/outputs', (route) =>
      route.fulfill({
        json: outputList(run, 'failed', [
          {
            id: 'early',
            name: 'wan-step00000002.safetensors',
            path: 'wan-step00000002.safetensors',
            kind: 'checkpoint',
            status: 'available',
          },
          {
            id: 'later',
            name: 'wan-step00000010.safetensors',
            path: 'wan-step00000010.safetensors',
            kind: 'checkpoint',
            status: 'available',
          },
          {
            id: 'sample',
            name: 'sample.mp4',
            path: 'samples/sample.mp4',
            kind: 'video',
            status: 'incomplete',
            error: 'Sample has no audio.',
          },
        ]),
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await openRunTab(page, 'samples');
    await page.getByRole('button', { name: 'sample.mp4 · incomplete' }).click();
    await expect(page.getByLabel('Selected sample')).toContainText(
      'Sample has no audio.',
    );
  } finally {
    await app.close();
  }
});

test('Samples keeps unsupported or undecodable files available for download', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const { run } = await preparedRun(app);
    await page.route('**/api/runs/*/outputs', (route) =>
      route.fulfill({
        json: outputList(run, 'completed', [
          {
            id: 'tiff',
            path: 'samples/newest.tiff',
            name: 'newest.tiff',
            kind: 'image',
            status: 'available',
            modified_ms: 30,
          },
          {
            id: 'video',
            path: 'samples/preview.mp4',
            name: 'preview.mp4',
            kind: 'video',
            status: 'available',
            modified_ms: 20,
            file_url: '/broken-preview.mp4',
          },
          {
            id: 'other-segment',
            run_id: 'previous-segment',
            path: 'samples/earlier-segment.png',
            name: 'earlier-segment.png',
            kind: 'image',
            status: 'available',
            modified_ms: 15,
            file_url: '/still.png',
          },
          {
            id: 'png',
            path: 'samples/still.png',
            name: 'still.png',
            kind: 'image',
            status: 'available',
            modified_ms: 1,
            file_url: '/still.png',
          },
        ]),
      }),
    );
    // The backend decoded this sample, but the client's media pipeline cannot.
    await page.route('**/broken-preview.mp4', (route) =>
      route.fulfill({ contentType: 'video/mp4', body: 'invalid media' }),
    );
    await page.route('**/still.png', (route) =>
      route.fulfill({ contentType: 'image/png', body: PNG }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await openRunTab(page, 'samples');
    const selected = page.getByLabel('Selected sample', { exact: true });
    await page.getByRole('button', { name: 'still.png · available' }).click();
    await expect(
      selected.getByRole('img', { name: 'Sample still.png' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'preview.mp4 · available' }).click();
    await expect(selected).toContainText('preview.mp4');
    await expect(selected).toContainText(
      'This browser cannot preview this sample.',
    );
    await page.getByRole('button', { name: 'newest.tiff · available' }).click();
    await expect(selected).toContainText(
      'This browser cannot preview this sample.',
    );
    await expect(selected.getByRole('img')).toHaveCount(0);
    await expect(
      selected.getByRole('link', { name: 'Download output' }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});
