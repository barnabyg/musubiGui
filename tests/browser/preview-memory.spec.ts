import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('generation memory is saved and frozen independently from training', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'h3')!;
    const saved = (await (
      await page.request.get(`${app.url}/api/jobs/${job.id}`)
    ).json()) as { config: Record<string, unknown> } & Record<string, unknown>;
    const changed = await page.request.put(`${app.url}/api/jobs/${job.id}`, {
      data: {
        ...saved,
        id: undefined,
        version: undefined,
        config: { ...saved.config, blocks_to_swap: 40, preview_every: 1 },
      },
    });
    expect(changed.ok()).toBe(true);
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    const policy = page.getByLabel('Generation memory policy', { exact: true });
    await policy.selectOption('conservative');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await page.reload();
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    await expect(policy).toHaveValue('conservative');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByLabel('Preview memory settings')).toContainText(
      'Conservative family preset selected',
    );
    await page
      .getByRole('button', { name: 'Prepare and start', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const records = (await (
      await page.request.get(`${app.url}/api/runs`)
    ).json()) as { id: string }[];
    const run = (await (
      await page.request.get(`${app.url}/api/runs/${records[0].id}`)
    ).json()) as {
      stages: {
        name: string;
        args: string[];
        preview_memory?: {
          mode: string;
          blocks_to_swap: number;
          reason: string;
        };
      }[];
    };
    const generate = run.stages.find((stage) => stage.name === 'generate')!;
    const training = run.stages.find((stage) => stage.name === 'train')!;
    expect(training.args[training.args.indexOf('--blocks_to_swap') + 1]).toBe(
      '40',
    );
    expect(training.preview_memory?.mode).toBe('training-residency');
    expect(generate.preview_memory?.mode).toBe('fallback');
    expect(generate.preview_memory?.blocks_to_swap).toBe(48);
    expect(generate.args[generate.args.indexOf('--blocks_to_swap') + 1]).toBe(
      '48',
    );
    await app.crash();
    await app.start();
    const reopened = (await (
      await page.request.get(`${app.url}/api/runs/${records[0].id}`)
    ).json()) as typeof run;
    expect(
      reopened.stages.find((stage) => stage.name === 'generate')
        ?.preview_memory,
    ).toEqual(generate.preview_memory);
  } finally {
    await app.close();
  }
});
