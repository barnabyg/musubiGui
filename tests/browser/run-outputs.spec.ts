import { test, expect } from '@playwright/test';
import {
  checkpointDownload,
  finalCheckpointButton,
  openCheckpointPanel,
  openRunTab,
  preparedRun,
  runPanel,
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
    await expect(
      page.getByText(/^No supported outputs found yet/),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    const sample = page.getByRole('button', {
      name: 'sample.mp4 · available',
      exact: true,
    });
    await openRunTab(page, 'samples');
    await expect(sample).toBeVisible({ timeout: 240_000 });
    await expect(page.getByText(/^No supported outputs found yet/)).toHaveCount(
      0,
    );
    await sample.click();
    const video = page.getByLabel('Output video', { exact: true });
    await video.evaluate(async (element) => {
      const media = element as HTMLVideoElement;
      element.setAttribute('data-kept', 'yes');
      media.muted = true;
      media.loop = true;
      await media.play();
    });
    await expect(
      page.getByRole('button', { name: 'Refresh outputs', exact: true }),
    ).toBeHidden();
    await openRunTab(page, 'overview');
    await page
      .getByRole('button', { name: 'Refresh outputs', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Refresh outputs', exact: true }),
    ).toBeEnabled();
    await openRunTab(page, 'samples');
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

test('a finished Run lists its final LoRA with its checkpoints and samples in a separate tab', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
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

    const files = runPanel(page, 'checkpoints');
    const checkpoints = files.locator('.output-checkpoints');
    await expect(checkpoints.locator('table')).toBeHidden();
    await checkpoints.locator('summary').click();
    await expect(checkpoints.locator('tbody tr').last()).toContainText('Final');
    await expect(checkpoints).toContainText(/\d+ (KB|MB|GB)/);
    // Optimizer state weights are not offered as LoRA outputs.
    await expect(checkpoints).not.toContainText('model.safetensors');
    await expect(files).not.toContainText('-state/');
    const states = runPanel(page, 'resume');
    await expect(states).toHaveCount(1);
    await states.getByText(/^Optimizer state files/).click();
    await expect(states.locator('.state-files')).toContainText(
      'model.safetensors',
    );
    await openRunTab(page, 'samples');
    await page
      .getByRole('button', { name: 'sample.mp4 · available', exact: true })
      .click();
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

test('a damaged final LoRA is called out in the checkpoints', async ({
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
            path: 'checkpoints/wan.safetensors',
            kind: 'checkpoint',
            status: 'incomplete',
            error: 'Output is empty.',
          },
        ]),
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const final = await finalCheckpointButton(page);
    await expect(final).toHaveText('wan.safetensors · incomplete');
    await final.click();
    await expect(
      page.getByLabel('Selected checkpoint', { exact: true }),
    ).toContainText('Output is empty.');
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
            path: 'checkpoints/wan-step00000002.safetensors',
            kind: 'checkpoint',
            status: 'available',
          },
          {
            id: 'later',
            name: 'wan-step00000010.safetensors',
            path: 'checkpoints/wan-step00000010.safetensors',
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
    const summary = runSummary(page);
    await expect(summary).toContainText('sample.mp4: Sample has no audio.');
    const checkpoints = await openCheckpointPanel(page);
    await expect(checkpoints.locator('tbody tr')).toHaveText([
      /^2.*wan-step00000002\.safetensors · available$/,
      /^10.*wan-step00000010\.safetensors · available$/,
    ]);
    const download = await checkpointDownload(page, /step00000010/);
    await expect(download).toHaveAttribute('href', '/nowhere?download=true');
  } finally {
    await app.close();
  }
});
