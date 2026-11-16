import { test, expect } from '@playwright/test';
import { jobWorkspace, openJobs } from './jobHarness';

test('the job form uses short fields for short values and shares rows between related ones', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const box = async (label: string) => {
      await page
        .getByRole('tab', {
          name:
            label === 'Blocks to swap'
              ? 'Memory'
              : label === 'Training steps'
                ? 'Training'
                : 'Dataset preparation',
          exact: true,
        })
        .click();
      return (await page
        .getByRole('spinbutton', { name: new RegExp(`^${label}`) })
        .boundingBox())!;
    };
    // The compact setup stays shorter than the previous expanded form.
    expect(
      await page.evaluate(() => document.documentElement.scrollHeight),
    ).toBeLessThan(3200);

    for (const label of [
      'Blocks to swap',
      'Default training width',
      'Training steps',
    ])
      expect((await box(label)).width).toBeLessThan(200);
    const width = await box('Default training width');
    expect((await box('Default training height')).y).toBe(width.y);
    expect((await box('Frames')).y).toBeGreaterThan(width.y);
    // The header and its repeated title are gone.
    await expect(page.locator('main header')).toHaveCount(0);
  } finally {
    await app.close();
  }
});

for (const family of ['wan', 'h3'] as const) {
  test(`${family} job tabs show disjoint panels, support keyboard navigation and keep unsaved edits`, async ({
    page,
  }) => {
    const app = await jobWorkspace();
    try {
      await page.setViewportSize({ width: 1000, height: 800 });
      await openJobs(page, app.url);
      await page
        .getByRole('button', {
          name: family === 'wan' ? 'New Wan job' : 'New H3 job',
          exact: true,
        })
        .click();
      const menu = page.getByRole('tablist', { name: 'Job sections' });
      const go = (name: string) =>
        menu.getByRole('tab', { name, exact: true }).click();
      const current = menu.getByRole('tab', { selected: true });
      const labels = [
        'Job & dataset',
        'Training',
        'Samples',
        'Dataset preparation',
        'Memory',
        'Checkpoints',
        'Presets',
        'Review',
      ];
      const fields = new Set<string>();
      for (const label of labels) {
        await go(label);
        await expect(current).toHaveText(label);
        const panel = page.getByRole('tabpanel');
        await expect(panel).toHaveCount(1);
        await expect(panel).toHaveAccessibleName(label);
        for (const id of await panel
          .locator('input[id], select[id], textarea[id]')
          .evaluateAll((elements) => elements.map((element) => element.id))) {
          expect(
            fields.has(id),
            `Field ${id} appears in more than one tab`,
          ).toBe(false);
          fields.add(id);
        }
        // Scrolling within a tab never switches its selected group.
        await page.evaluate(() =>
          window.scrollTo(0, document.documentElement.scrollHeight),
        );
        await expect(current).toHaveText(label);
      }
      await current.focus();
      await page.keyboard.press('Home');
      await expect(current).toHaveText('Job & dataset');
      await expect(current).toBeFocused();
      await page.keyboard.press('ArrowLeft');
      await expect(current).toHaveText('Review');
      await page.keyboard.press('ArrowRight');
      await expect(current).toHaveText('Job & dataset');
      await page.keyboard.press('End');
      await expect(current).toHaveText('Review');

      await page.keyboard.press('Tab');
      await expect(page.getByRole('tabpanel')).toBeFocused();

      await go('Memory');
      const blocks = page.getByRole('spinbutton', { name: /^Blocks to swap/ });
      await blocks.fill('12');
      await go('Training');
      await go('Memory');
      await expect(blocks).toHaveValue('12');
      await expect(page.getByRole('status')).toHaveText('Unsaved draft');

      expect(
        await page.evaluate(
          () =>
            document.documentElement.scrollWidth <=
            document.documentElement.clientWidth,
        ),
      ).toBe(true);
    } finally {
      await app.close();
    }
  });
}
