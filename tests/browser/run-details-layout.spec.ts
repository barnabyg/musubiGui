import { test, expect, type Page } from '@playwright/test';
import { preparedRun, runWorkspace } from './runHarness';

// Run details is a flat stack of bordered top-level panels, each headed by an h2, with no
// divider lines inside them. Offenders are reported by class and accessible name.
async function expectFlatPanels(page: Page) {
  const layout = await page.evaluate(() => {
    const root = document.querySelector('.run-details')!;
    const shown = (element: HTMLElement) => element.offsetParent !== null;
    const name = (element: HTMLElement) =>
      `${element.tagName.toLowerCase()}.${element.className} ${element.getAttribute('aria-label') ?? element.textContent!.slice(0, 40)}`;
    const bordered = (element: HTMLElement, side: 'top' | 'bottom') => {
      const style = getComputedStyle(element);
      return (
        style.getPropertyValue(`border-${side}-style`) !== 'none' &&
        parseFloat(style.getPropertyValue(`border-${side}-width`)) > 0
      );
    };
    const panels = [...root.querySelectorAll<HTMLElement>('.panel')];
    const visible = panels.filter(shown);
    return {
      nested: panels
        .filter((panel) => panel.parentElement!.closest('.panel'))
        .map(name),
      subHeadings: root.querySelectorAll('.panel :is(h3, h4, h5, h6)').length,
      unheaded: visible
        .filter(
          (panel) => !panel.querySelector(':scope > h2, :scope > summary > h2'),
        )
        .map(name),
      unframed: visible
        .filter(
          (panel) =>
            !bordered(panel, 'top') ||
            parseFloat(getComputedStyle(panel).paddingTop) === 0,
        )
        .map(name),
      dividers: [
        ...root.querySelectorAll<HTMLElement>(
          '.panel :is(details, div, section, p)',
        ),
      ]
        .filter(shown)
        .filter(
          (element) => bordered(element, 'top') || bordered(element, 'bottom'),
        )
        .map(name),
    };
  });
  expect(layout).toEqual({
    nested: [],
    subHeadings: 0,
    unheaded: [],
    unframed: [],
    dividers: [],
  });
}

test('Run details keeps a tight header, flat Overview panels and a samples-only Samples tab', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const { job, run } = await preparedRun(app);
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });

    // The browser note shares one line with the GPU status, which is still announced on its own.
    await expect(page.getByText(/Each Run keeps the job settings/)).toHaveCount(
      0,
    );
    const line = page.locator('.run-status-line');
    await expect(line).toHaveText('GPU available · Background training');
    await expect(line.getByRole('status')).toHaveText('GPU available');

    // Overview: summary first, Originating job last within it, then one panel per section.
    const overview = page.locator('#run-panel-overview');
    await expect(
      overview.locator(
        ':scope > .panel > h2, :scope > details.panel > summary > h2',
      ),
    ).toHaveText([
      job.name,
      'Actions',
      'Resume from an earlier save',
      'Caching',
      'Compare with another Run',
      'Inputs and output folder',
      'Stage logs',
    ]);
    const summary = overview.locator('.panel').first();
    await expect(summary.locator(':scope > *').last()).toHaveAttribute(
      'aria-label',
      'Originating job',
    );
    await expect(page.getByRole('region', { name: 'Run result' })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole('button', { name: 'Refresh outputs', exact: true }),
    ).toBeVisible();
    await expectFlatPanels(page);

    // The resume explanation is reference text, collapsed until asked for.
    const resume = page.getByRole('region', {
      name: 'Resume from an earlier save',
    });
    await expect(
      resume.getByText(/^Restoration starts a new segment/),
    ).toBeHidden();
    await expect(
      resume.getByRole('button', { name: 'Inspect saved states' }),
    ).toBeVisible();
    await resume.getByText('How resuming works', { exact: true }).click();
    await expect(
      resume.getByText(/^Restoration starts a new segment/),
    ).toBeVisible();

    // Samples holds samples and nothing else.
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    const samples = page.locator('#run-panel-samples');
    await expect(samples.locator(':scope > *')).toHaveCount(1);
    await expect(samples.locator(':scope > .panel > h2')).toHaveText('Samples');
    await expect(samples).toContainText('No generated samples recorded');
    for (const hidden of [
      job.name,
      'Refresh outputs',
      'Compare inputs',
      'Originating job',
    ])
      await expect(page.getByText(hidden, { exact: true })).toBeHidden();
    await expectFlatPanels(page);

    // Narrow screens wrap the status line instead of scrolling sideways.
    await page.setViewportSize({ width: 360, height: 800 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  } finally {
    await app.close();
  }
});

test('Result warnings and settled sample errors appear in the Run summary', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const { run } = await preparedRun(app);
    await page.route('**/api/runs/*/outputs', (route) =>
      route.fulfill({
        json: {
          run_id: run.id,
          job: { name: 'Mocked', family: 'wan', revision: 1 },
          status: 'failed',
          warnings: ['The paired LoRA outputs are incomplete.'],
          note: 'These files belong to this Run.',
          outputs: [
            {
              id: 'sample',
              run_id: run.id,
              name: 'sample.mp4',
              path: 'samples/sample.mp4',
              kind: 'video',
              status: 'incomplete',
              error: 'Sample has no audio.',
              file_url: '/nowhere',
              bytes: 2000,
            },
          ],
        },
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const summary = page.locator('#run-panel-overview > .panel').first();
    await expect(summary).toContainText(
      'The paired LoRA outputs are incomplete.',
    );
    await expect(summary).toContainText('sample.mp4: Sample has no audio.');
    await expect(page.getByRole('region', { name: 'Run result' })).toHaveCount(
      0,
    );
  } finally {
    await app.close();
  }
});
