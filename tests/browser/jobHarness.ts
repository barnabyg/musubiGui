import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { localBackend } from './backend';

// A backend with empty state and every model component path configured.
export async function jobWorkspace() {
  const root = await mkdtemp(path.join(tmpdir(), 'musubi-jobs-'));
  const { url, start, stop } = await localBackend({
    state: path.join(root, 'state'),
    readyRoute: '/api/jobs',
  });
  await mkdir(path.join(root, 'models'));
  await start();
  const settings = (await (
    await fetch(`${url}/api/settings`)
  ).json()) as Record<string, unknown>;
  const configured = await fetch(`${url}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...settings,
      output_root: path.join(root, 'output'),
      cache_root: path.join(root, 'cache'),
      model_roots: [path.join(root, 'models')],
      models: Object.fromEntries(
        [
          'wan_low',
          'wan_high',
          'wan_vae',
          'wan_t5',
          'h3_dit',
          'h3_video_vae',
          'h3_audio_vae',
          'h3_text',
        ].map((key) => [key, path.join(root, 'models', `${key}.safetensors`)]),
      ),
    }),
  });
  expect(configured.status).toBe(200);
  return {
    root,
    url,
    start,
    stop,
    close: async () => {
      await stop();
      await rm(root, {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 150,
      });
    },
  };
}

export async function openJobs(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole('button', { name: 'Jobs', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Jobs', exact: true }),
  ).toBeVisible();
}

export async function jobAction(page: Page, name: string) {
  await page.getByRole('button', { name: 'More job actions' }).click();
  await page.getByRole('menuitem', { name, exact: true }).click();
}
