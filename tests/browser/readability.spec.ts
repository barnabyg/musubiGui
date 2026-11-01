import { test, expect, type Page } from '@playwright/test';
import { runWorkspace } from './runHarness';

type Finding = { text: string; size?: number; ratio?: number };

// Every visible text node on the page: too small, or below 4.5:1 on the colour behind it.
// Disabled controls are exempt from the contrast standard.
function readabilityFindings(page: Page) {
  return page.evaluate(() => {
    const channels = (value: string) => {
      const parts = value.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0, 0];
      return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
    };
    const over = (
      top: ReturnType<typeof channels>,
      under: ReturnType<typeof channels>,
    ) => ({
      r: top.r * top.a + under.r * (1 - top.a),
      g: top.g * top.a + under.g * (1 - top.a),
      b: top.b * top.a + under.b * (1 - top.a),
      a: 1,
    });
    const backdrop = (element: Element) => {
      const layers = [];
      for (let at: Element | null = element; at; at = at.parentElement)
        layers.push(channels(getComputedStyle(at).backgroundColor));
      return layers.reduceRight(
        (under, layer) => over(layer, under),
        channels(getComputedStyle(document.documentElement).backgroundColor),
      );
    };
    const luminance = ({ r, g, b }: ReturnType<typeof channels>) => {
      const linear = [r, g, b].map((value) => {
        const unit = value / 255;
        return unit <= 0.03928 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    };
    const findings: Finding[] = [];
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
    );
    while (walker.nextNode()) {
      const text = walker.currentNode.textContent?.trim();
      const element = walker.currentNode.parentElement;
      if (!text || !element?.checkVisibility({ visibilityProperty: true }))
        continue;
      if (['SCRIPT', 'STYLE', 'OPTION'].includes(element.tagName)) continue;
      // Screen-reader-only text is clipped to a single pixel.
      if (element.getBoundingClientRect().width <= 1) continue;
      const style = getComputedStyle(element);
      const size = Number.parseFloat(style.fontSize);
      if (size < 12) findings.push({ text, size });
      if (element.closest(':disabled')) continue;
      const back = backdrop(element);
      const front = over(channels(style.color), back);
      const [light, dark] = [luminance(front), luminance(back)].sort(
        (a, b) => b - a,
      );
      const ratio = (light + 0.05) / (dark + 0.05);
      if (ratio < 4.5)
        findings.push({ text, ratio: Math.round(ratio * 100) / 100 });
    }
    return findings;
  });
}

function overflowing(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth >
      document.documentElement.clientWidth,
  );
}

test('main screens keep text readable and fit the window at every supported width', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await app.post('/api/runs/prepare', { job_id: job.id });
    await page.goto(app.url);
    const screens: [string, () => Promise<void>][] = [
      [
        'Settings',
        async () => {
          await page.goto(`${app.url}/#/settings`);
          const diagnostics = page.getByRole('button', {
            name: 'Run diagnostics',
          });
          await diagnostics.click();
          await expect(page.locator('#diagnostics-report')).toBeVisible();
        },
      ],
      [
        'Datasets',
        async () => {
          await page.goto(`${app.url}/#/datasets`);
          await expect(
            page.getByRole('heading', { name: 'Datasets', exact: true }),
          ).toBeVisible();
        },
      ],
      [
        'Dataset editor',
        async () => {
          await page.goto(`${app.url}/#/datasets/${job.dataset_id}`);
          await expect(page.locator('.dataset-gallery')).toBeVisible();
        },
      ],
      [
        'Jobs',
        async () => {
          await page.goto(`${app.url}/#/jobs/${job.id}`);
          await expect(page.getByLabel('Job name')).toBeVisible();
          // Also checks the dataset rows and the review's Training sections table.
          await page
            .getByRole('button', { name: 'Review configuration', exact: true })
            .click();
          await expect(
            page.getByRole('table', { name: 'Training sections' }),
          ).toBeVisible();
        },
      ],
      [
        'Runs',
        async () => {
          const [run] = await app.records();
          await page.goto(`${app.url}/#/runs/${run.id}`);
          await expect(page.getByLabel('Run stages')).toBeVisible();
        },
      ],
    ];
    for (const width of [1440, 1000, 760]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [name, open] of screens) {
        await open();
        await expect
          .soft(await readabilityFindings(page), `${name} at ${width}px`)
          .toEqual([]);
        expect
          .soft(await overflowing(page), `${name} overflows at ${width}px`)
          .toBe(false);
      }
    }
  } finally {
    await app.close();
  }
});
