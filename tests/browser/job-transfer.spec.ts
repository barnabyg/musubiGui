import { test, expect } from '@playwright/test';
import { jobAction, jobWorkspace, openJobs } from './jobHarness';

test('duplication presets cancelled conversion and versioned export import keep independent drafts', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await page.getByLabel('Job name', { exact: true }).fill('Reusable');

    await page
      .getByRole('tab', { name: 'LoRA & training', exact: true })
      .click();
    await page.getByLabel('LoRA rank', { exact: true }).fill('16');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    await page.getByLabel('Preset name', { exact: true }).fill('Sixteen');
    await page
      .getByRole('button', { name: 'Save as preset', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText('Preset saved');
    await jobAction(page, 'Duplicate job');
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'Reusable copy',
    );
    await page
      .getByRole('tab', { name: 'LoRA & training', exact: true })
      .click();
    await page.getByLabel('LoRA rank', { exact: true }).fill('32');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption({ label: 'Sixteen' });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await page
      .getByRole('tab', { name: 'LoRA & training', exact: true })
      .click();
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await page.getByLabel('Training steps', { exact: true }).fill('800');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption({
        label: 'Measured · Wan 832×480 · 33 frames · rank 16',
      });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText(
      'Preset applied as a new revision',
    );
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Default training width\b/ }),
    ).toHaveValue('832');
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('33');
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('800');
    await jobAction(page, 'Change model to H3 T2VA');
    await expect(
      page.getByRole('dialog', { name: 'Confirm model conversion' }),
    ).toContainText('frames: 33 → 124');
    await page
      .getByRole('button', { name: 'Cancel conversion', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(page.getByRole('combobox', { name: /^Recipe/ })).toHaveValue(
      'alternating',
    );
    await jobAction(page, 'Import / export configuration');
    const download = page.waitForEvent('download');
    await page
      .getByRole('button', { name: 'Export configuration', exact: true })
      .click();
    expect((await download).suggestedFilename()).toMatch(/^musubi-job-/);
    const exported = JSON.parse(
      await page
        .getByRole('textbox', { name: 'Configuration JSON', exact: true })
        .inputValue(),
    ) as { version: number; draft: { name: string } };
    expect(exported.version).toBe(2);
    exported.draft.name = 'Imported draft';
    await page
      .getByRole('textbox', { name: 'Configuration JSON', exact: true })
      .fill(JSON.stringify(exported));
    await page
      .getByRole('button', { name: 'Import configuration', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'Imported draft',
    );
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();
    await page.getByRole('button', { name: /Reusable · WAN/ }).click();
    await page
      .getByRole('tab', { name: 'LoRA & training', exact: true })
      .click();
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
    await jobAction(page, 'Change model to H3 T2VA');
    await page
      .getByRole('button', { name: 'Accept conversion', exact: true })
      .click();
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('124');
  } finally {
    await app.close();
  }
});
