import { test, expect } from '@playwright/test';
import { jobAction, jobWorkspace, openJobs } from './jobHarness';

test('the action bar stays visible, identity comes first and rare actions are in a keyboard menu', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const save = page.getByRole('button', { name: 'Save draft', exact: true });
    const inView = async () => {
      const box = await save.boundingBox();
      return (
        box !== null &&
        box.y >= 0 &&
        box.y + box.height <= page.viewportSize()!.height
      );
    };
    await expect.poll(inView).toBe(true);
    await page.evaluate(() =>
      window.scrollTo(0, document.documentElement.scrollHeight),
    );
    await expect.poll(inView).toBe(true);

    // Tabbing to a field below the bar scrolls it clear of the bar.

    const seed = page.getByLabel('Seed', { exact: true });
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    await seed.focus();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByLabel('Seed', { exact: true }).focus();
    const bar = await page.locator('.job-action-bar').boundingBox();
    const field = await seed.boundingBox();
    expect(field!.y).toBeGreaterThanOrEqual(bar!.y + bar!.height);

    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(page.getByRole('heading', { level: 2 })).toHaveText([
      'Job & dataset',
    ]);

    const menu = page.getByRole('button', { name: 'More job actions' });
    await menu.focus();
    await page.keyboard.press('Enter');
    const items = page.getByRole('menuitem');
    await expect(items).toHaveText([
      'Duplicate job',
      'Change model to H3 T2VA',
      'Archive job',
      'Delete job',
      'Import / export configuration',
    ]);
    await expect(items.first()).toBeFocused();
    await page.keyboard.press('ArrowUp');
    await expect(items.last()).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(items.first()).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(items).toHaveCount(0);
    await expect(menu).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('heading', { name: 'Import / export configuration' }),
    ).toBeVisible();
    await expect.poll(inView).toBe(true);
  } finally {
    await app.close();
  }
});

test('the job collection sorts, opens separate editors and keeps new draft names apart', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    const create = page.getByRole('button', {
      name: 'New Wan job',
      exact: true,
    });
    await create.click();
    const name = page.getByLabel('Job name', { exact: true });
    await expect(name).toHaveValue('New Wan job');
    await expect(name).toBeFocused();
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();
    await create.click();
    await expect(name).toHaveValue('New Wan job 2');
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();
    await page.getByRole('button', { name: 'New H3 job', exact: true }).click();
    await expect(name).toHaveValue('New H3 job');
    await expect(page.getByRole('table', { name: 'Saved jobs' })).toHaveCount(
      0,
    );
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();

    const table = page.getByRole('table', { name: 'Saved jobs' });
    const rows = table.locator('tbody tr');
    await expect(rows).toHaveCount(3);
    await expect(rows.first()).toContainText('New H3 job');
    await expect(rows.first()).toContainText('None selected');
    await expect(rows.first()).toContainText('r1');
    await expect(rows.first()).toContainText('No Runs');
    await expect(table.getByRole('columnheader')).toHaveText([
      'Name',
      'Model',
      'Dataset',
      'Revision',
      'Last Run',
      /^Last edited/,
    ]);
    await expect(
      table.getByRole('columnheader', { name: 'Last edited' }),
    ).toHaveAttribute('aria-sort', 'descending');
    await table.getByRole('button', { name: 'Name', exact: true }).click();
    await expect(rows.first()).toContainText('New H3 job');
    await expect(rows.last()).toContainText('New Wan job 2');
    await table.getByRole('button', { name: 'Name', exact: true }).click();
    await expect(rows.first()).toContainText('New Wan job 2');
  } finally {
    await app.close();
  }
});

test('review leads with the verdict, comes into view and collapses technical detail', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    const verdict = review.locator('.review-verdict');
    await expect(verdict).toHaveClass(/failed/);
    await expect(verdict).toContainText(/^Review failed: resolve \d+ errors/);
    await expect(verdict).toContainText('0 included items');
    await expect(verdict).toContainText('Disk:');
    await expect(page.getByRole('status')).toContainText(
      /^Review failed for revision 1: \d+ errors/,
    );
    await expect(review).toBeInViewport();
    await expect(page.getByTestId('dataset-toml')).toBeHidden();
    await expect(page.getByText('Resolved paths')).toBeHidden();
    await expect(page.getByText('latent_cache arguments')).toBeHidden();
    await page.getByText('Advanced', { exact: true }).click();
    await expect(page.getByTestId('dataset-toml')).toBeVisible();
  } finally {
    await app.close();
  }
});

test('Review saves a dirty draft first, and disabled actions explain themselves', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();

    const prepare = page.getByRole('button', {
      name: 'Prepare only',
      exact: true,
    });
    const review = page.getByRole('button', {
      name: 'Review configuration',
      exact: true,
    });
    await expect(prepare).toBeDisabled();
    await expect(prepare).toHaveAccessibleDescription(
      'Choose Review configuration before preparing.',
    );
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    await expect(
      page.getByRole('button', { name: 'Save as preset', exact: true }),
    ).toHaveAccessibleDescription('Enter a preset name to save one.');
    await expect(
      page.getByRole('button', { name: 'Apply preset', exact: true }),
    ).toHaveAccessibleDescription('Select a preset to apply it.');

    const steps = page.getByLabel('Training steps', { exact: true });
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await steps.fill('700');
    await expect(prepare).toHaveAccessibleDescription(/save these edits/);
    await expect(review).toBeEnabled();

    // A failed save stops the review and leaves the edits on screen.
    await page.route('**/api/jobs/*', async (route) => {
      if (route.request().method() === 'PUT')
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'This job changed in another tab.' }),
        });
      else await route.continue();
    });
    await review.click();
    await expect(page.getByRole('alert')).toContainText('another tab');
    await expect(page.getByRole('status')).toHaveText(
      'Save failed — edits retained',
    );
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(steps).toHaveValue('700');
    await expect(
      page.getByRole('region', { name: 'Configuration review' }),
    ).toHaveCount(0);
    await page.unroute('**/api/jobs/*');

    await review.click();
    await expect(page.getByRole('status')).toContainText(
      'Review failed for revision 2',
    );
    await expect(
      page.getByRole('heading', { name: 'New Wan job', exact: true }),
    ).toBeVisible();
    await expect(prepare).toHaveAccessibleDescription(
      'Resolve the review errors before preparing.',
    );
    const stored = (await (
      await page.request.get(`${app.url}/api/jobs`)
    ).json()) as { config: { steps: number } }[];
    expect(stored[0].config.steps).toBe(700);
  } finally {
    await app.close();
  }
});

test('confirmation dialogs start on the safe choice, trap focus and cancel with Escape', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const menu = page.getByRole('button', { name: 'More job actions' });
    await jobAction(page, 'Delete job');
    const question = page.getByRole('dialog', {
      name: 'Permanently delete job New Wan job?',
    });
    await expect(question).toContainText('revision history');
    const cancel = question.getByRole('button', { name: 'Cancel' });
    const remove = question.getByRole('button', { name: 'Delete job' });
    await expect(cancel).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(remove).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(cancel).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(remove).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(question).toHaveCount(0);
    await expect(menu).toBeFocused();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'New Wan job',
    );

    // Unsaved edits ask first, on both routes away from the job.
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await page.getByLabel('Training steps', { exact: true }).fill('700');
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();
    const discard = page.getByRole('dialog', {
      name: 'Discard unsaved job edits?',
    });
    await discard.getByRole('button', { name: 'Keep editing' }).click();
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('700');
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    const leave = page.getByRole('dialog', { name: 'Leave this job?' });
    await leave.getByRole('button', { name: 'Keep editing' }).click();
    await expect(
      page.getByRole('heading', { name: 'New Wan job', exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await leave.getByRole('button', { name: 'Discard changes' }).click();
    await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
  } finally {
    await app.close();
  }
});

test('Jobs names components as Settings does and leaves out internal wording', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByText('Model components', { exact: true }).click();
    const component = page.getByRole('textbox', { name: 'Wan low-noise DiT' });
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(component).toBeVisible();
    await expect(page.getByText('wan_low', { exact: true })).toHaveCount(0);
    await expect(page.getByText('IMMUTABLE RUNS')).toHaveCount(0);
    await expect(page.getByText(/39 crashes/)).toHaveCount(0);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByText('Model component paths').click();
    await expect(
      page.getByRole('textbox', { name: 'Wan low-noise DiT' }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('job actions explain themselves with no job open, and conversion names fields readably', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await expect(
      page.getByRole('button', { name: 'More job actions' }),
    ).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Import configuration', exact: true })
      .click();
    await expect(page.getByText('Open a job to export it.')).toBeVisible();
    await expect(
      page.getByText('Choose a file or paste JSON to import it.'),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await jobAction(page, 'Change model to H3 T2VA');
    const dialog = page.getByRole('dialog', {
      name: 'Confirm model conversion',
    });
    await expect(dialog).toContainText('blocks to swap');
    await expect(dialog).not.toContainText('blocks_to_swap');
  } finally {
    await app.close();
  }
});
