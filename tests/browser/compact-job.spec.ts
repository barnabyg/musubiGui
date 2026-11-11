import { execFile } from 'node:child_process';
import path from 'node:path';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { helper, python } from './backend';
import { runWorkspace } from './runHarness';
import type { Job, JobConfig } from '../../frontend/jobTypes';

const execute = promisify(execFile);

for (const family of ['wan', 'h3'] as const) {
  test(`a new ${family} job reaches passing review from its compact required choices`, async ({
    page,
  }) => {
    const app = await runWorkspace();
    try {
      const dataset = app.manifest.jobs.find((job) => job.family === family)!;
      if (family === 'wan')
        await execute(python, [
          helper,
          'video',
          path.join(app.fixture, 'datasets/wan/motion.mp4'),
          '33',
          '16',
        ]);
      await app.post(`/api/datasets/${dataset.dataset_id}/scan?wait=true`);
      await page.setViewportSize({ width: 1366, height: 768 });
      await page.goto(`${app.url}/#/jobs`);
      await page
        .getByRole('button', {
          name: family === 'wan' ? 'New Wan job' : 'New H3 job',
          exact: true,
        })
        .click();
      const advanced = page.getByText('Advanced controls', { exact: true });
      await expect(advanced).toBeVisible();
      await expect(page.getByLabel('LoRA rank', { exact: true })).toBeHidden();
      const required = page.getByRole('region', { name: 'Required choices' });
      await expect(required).toContainText('Select at least one dataset');
      await expect(required).toContainText(
        'Enter a sample prompt or turn off the final sample',
      );
      await required
        .getByRole('button', { name: 'Select at least one dataset' })
        .press('Enter');
      const add = page.getByRole('combobox', { name: 'Add a dataset' });
      await expect(add).toBeFocused();
      await add.selectOption(dataset.dataset_id);
      await page.keyboard.press('Tab');
      await page.keyboard.press('Enter');
      await expect(
        page.getByRole('list', { name: 'Datasets in this job' }),
      ).toContainText(family === 'wan' ? 'WAN acceptance' : 'H3 acceptance');
      const prompt = page.getByRole('textbox', { name: 'Sample prompt' });
      await prompt.fill('A red square moves across a blue field.');
      const sample = page.getByRole('checkbox', {
        name: 'Generate a final sample',
        exact: true,
      });
      await sample.focus();
      await sample.press('Space');
      await expect(prompt).toBeHidden();
      await expect(required).toContainText('Required choices are complete');
      if (family === 'wan') {
        await sample.press('Space');
        await expect(prompt).toHaveValue(
          'A red square moves across a blue field.',
        );
      }
      await expect(
        page.getByLabel('Training steps', { exact: true }),
      ).toHaveValue('600');
      expect(
        await page
          .locator(
            'main input:visible, main select:visible, main textarea:visible',
          )
          .count(),
      ).toBeLessThanOrEqual(family === 'wan' ? 16 : 14);
      await page.setViewportSize({ width: 700, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      await expect(
        page.getByRole('region', { name: 'Configuration review' }),
      ).toContainText('Review passed', { timeout: 240_000 });
      await expect(page.getByLabel('LoRA rank', { exact: true })).toBeHidden();
      expect(await app.records()).toHaveLength(0);
    } finally {
      await app.close();
    }
  });
}

test('compact saved and imported jobs retain every advanced value and reveal hidden review errors', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const fixtureJob = app.manifest.jobs.find((job) => job.family === 'wan')!;
    const load = async (id: string) =>
      (await (
        await page.request.get(`${app.url}/api/jobs/${id}`)
      ).json()) as Job;
    const original = await load(fixtureJob.id);
    await page.goto(`${app.url}/#/jobs/${fixtureJob.id}`);
    const advanced = page.getByText('Advanced controls', { exact: true });
    await expect(page.getByLabel('LoRA rank', { exact: true })).toBeHidden();
    await advanced.press('Enter');
    await page.getByLabel('LoRA rank', { exact: true }).fill('16');
    await page.getByLabel('Seed', { exact: true }).fill('123');
    await page.getByRole('spinbutton', { name: /^Blocks to swap/ }).fill('12');
    await advanced.press('Enter');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('revision 2');
    const expected = {
      ...original.config,
      rank: 16,
      seed: 123,
      blocks_to_swap: 12,
    };
    expect((await load(fixtureJob.id)).config).toEqual(expected);
    await page.reload();
    await expect(page.getByLabel('LoRA rank', { exact: true })).toBeHidden();
    await advanced.press('Enter');
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
    await expect(page.getByLabel('Seed', { exact: true })).toHaveValue('123');
    await expect(
      page.getByRole('spinbutton', { name: /^Blocks to swap/ }),
    ).toHaveValue('12');
    await advanced.press('Enter');

    await page.getByRole('button', { name: 'More job actions' }).click();
    await page
      .getByRole('menuitem', {
        name: 'Import / export configuration',
        exact: true,
      })
      .click();
    const download = page.waitForEvent('download');
    await page
      .getByRole('button', { name: 'Export configuration', exact: true })
      .click();
    await download;
    const json = page.getByRole('textbox', {
      name: 'Configuration JSON',
      exact: true,
    });
    const exported = JSON.parse(await json.inputValue()) as {
      draft: { name: string; config: JobConfig };
    };
    exported.draft.name = 'Imported advanced values';
    exported.draft.config = {
      ...expected,
      width: 250,
      gradient_accumulation: 3,
    };
    await json.fill(JSON.stringify(exported));
    await page
      .getByRole('button', { name: 'Import configuration', exact: true })
      .click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'Imported advanced values',
    );
    await expect(page.getByLabel('LoRA rank', { exact: true })).toBeHidden();
    const id = new URL(page.url()).hash.split('/').at(-1)!;
    const imported = await load(id);
    expect(imported.config).toEqual(exported.draft.config);
    await page
      .getByRole('region', { name: 'Import or export configuration' })
      .getByRole('button', { name: 'Close', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page.getByRole('button', { name: /^Geometry:/ }).click();
    await expect(page.getByLabel('Width', { exact: true })).toBeFocused();
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
    await expect(
      page.getByLabel('Gradient accumulation', { exact: true }),
    ).toHaveValue('3');
    await advanced.press('Enter');
    await advanced.press('Enter');
    expect((await load(id)).config).toEqual(imported.config);
    expect((await load(id)).revision).toBe(1);
  } finally {
    await app.close();
  }
});
