import { execFile } from 'node:child_process';
import { access, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { expect, type Page } from '@playwright/test';
import { helper, localBackend, python } from './backend';

export const execute = promisify(execFile);

// A one-pixel PNG for specs that need a real image file.
export const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=',
  'base64',
);

export const exists = (file: string) =>
  access(file).then(
    () => true,
    () => false,
  );

export type RunRecord = {
  id: string;
  status: string;
  fingerprint: string;
  error?: string;
  job: { id: string; name: string; family: string; revision: number };
  directory: string;
  manifest: { items: { caption: { text: string }; absolute_path: string }[] };
  stages: { name: string; status: string }[];
  processes?: unknown[];
};

export async function runWorkspace(
  options: {
    freeBytes?: number;
    previewImages?: boolean;
    datasetMedia?: boolean;
  } = {},
) {
  const parent = await mkdtemp(path.join(tmpdir(), 'musubi-runs-'));
  const fixture = path.join(parent, 'fixture');
  await execute(python, [
    '-m',
    'musubi_gui.acceptance',
    '--workspace',
    fixture,
    '--prepare-only',
    '--skip-library-scan',
    ...(options.previewImages ? ['--preview-images'] : []),
    ...(options.datasetMedia ? ['--dataset-media'] : []),
  ]);
  const manifest = JSON.parse(
    await readFile(path.join(fixture, 'acceptance.json'), 'utf8'),
  ) as {
    state: string;
    jobs: { id: string; name: string; family: string; dataset_id: string }[];
  };
  const identityFile = path.join(parent, 'backend.json');
  const device = path.join(parent, 'device.sqlite');
  const backend = await localBackend({
    state: manifest.state,
    readyRoute: '/api/runs',
    entry: [helper, 'serve', '--identity', identityFile, '--device', device],
    env:
      options.freeBytes === undefined
        ? undefined
        : { MUSUBI_TEST_FREE_BYTES: String(options.freeBytes) },
    readyTimeout: 240_000,
  });
  const { url, start } = backend;
  const crash = async () => {
    await backend.end(() => execute(python, [helper, 'kill', identityFile]));
  };
  const post = async (route: string, body: object = {}) => {
    const result = await fetch(url + route, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const value: unknown = await result.json();
    if (!result.ok) throw new Error(JSON.stringify(value));
    return value;
  };
  const records = async () =>
    (await (await fetch(`${url}/api/runs`)).json()) as RunRecord[];
  const cleanupAction = async (run: RunRecord, action: 'stop' | 'cancel') => {
    const response = await fetch(`${url}/api/runs/${run.id}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(
        action === 'stop' ? { force: true, confirmed: true } : {},
      ),
    });
    if (response.ok) return;
    if (response.status === 409) {
      const refreshed = await fetch(`${url}/api/runs/${run.id}`);
      if (refreshed.ok) {
        const current = (await refreshed.json()) as RunRecord;
        if (
          ['prepared', 'completed', 'cancelled', 'failed', 'stopped'].includes(
            current.status,
          ) &&
          !current.processes?.length
        )
          return;
      }
    }
    throw new Error(JSON.stringify(await response.json()));
  };
  const close = async () => {
    try {
      if (!backend.running()) await start();
      for (const run of await records()) {
        if (['training', 'generating', 'stopping'].includes(run.status))
          await cleanupAction(run, 'stop');
        if (
          ['preparing', 'pending_preparation', 'pending_training'].includes(
            run.status,
          )
        )
          await cleanupAction(run, 'cancel');
        if (['interrupted', 'orphaned', 'uncertain'].includes(run.status)) {
          const current = (await post(
            `/api/runs/${run.id}/reconcile`,
          )) as RunRecord;
          if (['orphaned', 'uncertain'].includes(current.status))
            await post(`/api/runs/${run.id}/terminate-orphan`, {
              confirmed: true,
            });
        }
      }
      await expect
        .poll(async () => {
          const active = await records();
          return active.filter((run) =>
            ['preparing', 'training', 'generating', 'stopping'].includes(
              run.status,
            ),
          ).length;
        })
        .toBe(0);
    } finally {
      await crash();
      await rm(parent, {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 150,
      });
    }
  };
  // Another instance owning the GPU leaves new work pending, whatever the fixture's speed.
  const leaseGpu = async (action: 'acquire' | 'release') => {
    await execute(python, [
      helper,
      'lease',
      action,
      path.join(parent, 'other-instance'),
      device,
    ]);
  };
  await start();
  return {
    parent,
    fixture,
    manifest,
    url,
    start,
    crash,
    post,
    records,
    leaseGpu,
    close,
  };
}

// Prepares the first Wan job and returns its Run once it is ready to start.
export async function preparedRun(
  app: Awaited<ReturnType<typeof runWorkspace>>,
) {
  const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
  await app.post('/api/runs/prepare', { job_id: job.id });
  await expect
    .poll(async () => (await app.records())[0].status, { timeout: 240_000 })
    .toBe('prepared');
  return { job, run: (await app.records())[0] };
}

// Run details structure. Specs name a tab or panel by key and reach it through these helpers,
// so renaming a tab or panel heading changes only the names below (and
// run-details-layout.spec.ts, which checks the layout itself).
const runTabNames = { overview: 'Overview', samples: 'Samples' } as const;
const runPanelNames = {
  actions: 'Actions',
  resume: 'Resume training',
  caching: 'Cache preparation results',
  checkpoints: 'Checkpoints',
  compare: 'Compare with another Run',
  inputs: 'Inputs and output folder',
  samples: 'Samples',
} as const;
export type RunTabKey = keyof typeof runTabNames;
export type RunPanelKey = keyof typeof runPanelNames;

export function runTab(page: Page, tab: RunTabKey) {
  return page
    .getByRole('tablist', { name: 'Run views' })
    .getByRole('tab', { name: runTabNames[tab], exact: true });
}

export async function openRunTab(page: Page, tab: RunTabKey) {
  await runTab(page, tab).click();
}

export function runTabPanel(page: Page, tab: RunTabKey) {
  return page.getByRole('tabpanel', { name: runTabNames[tab], exact: true });
}

export function runPanel(page: Page, panel: RunPanelKey) {
  return page.getByRole('region', { name: runPanelNames[panel], exact: true });
}

// The first Overview panel: the Run's name, state, warnings, timing and originating job.
export function runSummary(page: Page) {
  return page.locator('#run-panel-overview > .panel').first();
}

// Switches Run details to Overview, expands the LoRA checkpoints table and returns its panel.
export async function openCheckpointPanel(page: Page) {
  await openRunTab(page, 'overview');
  const panel = runPanel(page, 'checkpoints');
  const table = panel.locator('details.output-checkpoints');
  await expect(table).toBeVisible({ timeout: 240_000 });
  if ((await table.getAttribute('open')) === null)
    await table.locator('summary').click();
  return panel;
}

// The file button in the Final row of the checkpoints table.
export async function finalCheckpointButton(page: Page) {
  const panel = await openCheckpointPanel(page);
  return panel
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name: 'Final', exact: true }) })
    .getByRole('button');
}

// Selects a checkpoint and returns the download link of its detail.
export async function checkpointDownload(page: Page, file: RegExp) {
  const panel = await openCheckpointPanel(page);
  await panel.getByRole('button', { name: file }).click();
  return panel
    .getByLabel('Selected checkpoint', { exact: true })
    .getByRole('link', { name: 'Download output', exact: true });
}
