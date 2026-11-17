import { test, expect } from '@playwright/test';
import { jobWorkspace, openJobs } from './jobHarness';

test('invalid fields are flagged while typing and review errors lead to their field', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();

    const width = page.getByLabel('Default training width', { exact: true });
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(width).not.toHaveAttribute('aria-invalid', 'true');
    await width.fill('250');
    await expect(width).toHaveAttribute('aria-invalid', 'true');
    await expect(width).toHaveAccessibleDescription(
      'Enter at least 64, in multiples of 16.',
    );
    await width.fill('256');
    await expect(width).not.toHaveAttribute('aria-invalid', 'true');

    const frames = page.getByLabel('Frames', { exact: true });
    await frames.fill('32');
    await expect(frames).toHaveAccessibleDescription(/4\*n\+1/);
    await frames.fill('33');
    const blocks = page.getByRole('spinbutton', { name: 'Blocks to swap' });
    await page.getByRole('tab', { name: 'Advanced', exact: true }).click();
    await blocks.fill('39');
    await expect(blocks).toHaveAccessibleDescription('Enter 0 to 38.');
    await blocks.fill('1.5');
    await expect(blocks).toHaveAccessibleDescription('Enter a whole number.');
    await blocks.fill('36');

    // Nothing is blocked: an invalid draft can still be saved.
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await width.fill('250');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await width.fill('256');

    // A new draft has no dataset yet, so Review names it, beside the field and as a link.
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(page.locator('#job-field-dataset_id')).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await review
      .getByRole('button', { name: /^Dataset: select a registered/ })
      .click();
    await expect(page.locator('#job-field-dataset_id')).toBeFocused();
    await expect(page.locator('#job-field-dataset_id')).toBeInViewport();
  } finally {
    await app.close();
  }
});

test('a review error about a model component opens and focuses its field', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const message =
      'Missing wan_low; select compatible model files in Settings.';
    await page.route('**/api/jobs/*/review', async (route) => {
      const response = await route.fetch();
      const review = (await response.json()) as { errors: string[] };
      await route.fulfill({
        response,
        json: { ...review, valid: false, errors: [...review.errors, message] },
      });
    });
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    // The fixture's own review can report this too, so it may be listed twice.
    await page.getByRole('button', { name: message }).first().click();
    await expect(
      page.getByRole('textbox', { name: 'Wan low-noise DiT' }),
    ).toBeFocused();
  } finally {
    await app.close();
  }
});

test('a review size error leads to the dimension that is wrong', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();

    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await page
      .getByLabel('Default training height', { exact: true })
      .fill('250');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page.getByRole('button', { name: /^Geometry:/ }).click();
    await expect(
      page.getByLabel('Default training height', { exact: true }),
    ).toBeFocused();
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByLabel('Default training width', { exact: true }),
    ).not.toHaveAttribute('aria-invalid', 'true');
  } finally {
    await app.close();
  }
});

test('a saved value Review rejects has a control to fix it, even where the form usually hides it', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page.getByRole('button', { name: 'New H3 job', exact: true }).click();
    await expect(page).toHaveURL(/#\/jobs\/\w+/);
    const h3 = { id: page.url().split('/').at(-1)! };
    const saved = (await (
      await page.request.get(`${app.url}/api/jobs/${h3.id}`)
    ).json()) as {
      revision: number;
      name: string;
      family: string;
      datasets: unknown[];
      config: Record<string, unknown>;
    };
    const updated = await page.request.put(`${app.url}/api/jobs/${h3.id}`, {
      data: {
        name: saved.name,
        family: saved.family,
        datasets: saved.datasets,
        revision: saved.revision,
        config: { ...saved.config, fp8_scaled: true },
      },
    });
    expect(updated.ok()).toBe(true);
    await page.reload();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page
      .getByRole('button', { name: /^Memory: H3 does not support FP8/ })
      .click();
    const toggle = page.getByRole('checkbox', { name: /^Runtime FP8 scaling/ });
    await expect(toggle).toBeFocused();
    await page.getByRole('tab', { name: 'Advanced', exact: true }).click();
    await toggle.click();
    await expect(toggle).toHaveCount(0);
  } finally {
    await app.close();
  }
});
