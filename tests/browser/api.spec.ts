import { test, expect } from '@playwright/test';
import { apiClient } from '../../frontend/api';

const realFetch = globalThis.fetch;
let calls: { url: string; init?: RequestInit }[] = [];

function respondWith(response: Response) {
  calls = [];
  globalThis.fetch = (input, init) => {
    calls.push({ url: String(input), init });
    return Promise.resolve(response);
  };
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status });
}

test.afterEach(() => {
  globalThis.fetch = realFetch;
});

test('a GET sends no body and returns the parsed response', async () => {
  respondWith(jsonResponse({ id: 'a' }));
  const request = apiClient('Fallback.');
  expect(await request<{ id: string }>('/api/thing')).toEqual({ id: 'a' });
  expect(calls[0].url).toBe('/api/thing');
  expect(calls[0].init?.method).toBe('GET');
  expect(calls[0].init?.body).toBeUndefined();
  expect(calls[0].init?.headers).toBeUndefined();
});

test('a body is sent as JSON with the given method', async () => {
  respondWith(jsonResponse({}));
  await apiClient('Fallback.')('/api/thing', 'PUT', { name: 'a' });
  expect(calls[0].init?.method).toBe('PUT');
  expect(calls[0].init?.body).toBe('{"name":"a"}');
  expect(calls[0].init?.headers).toEqual({
    'Content-Type': 'application/json',
  });
});

test('an abort signal reaches the request', async () => {
  respondWith(jsonResponse({}));
  const { signal } = new AbortController();
  await apiClient('Fallback.')('/api/thing', 'GET', undefined, signal);
  expect(calls[0].init?.signal).toBe(signal);
});

test('a failed request reports the server detail', async () => {
  respondWith(jsonResponse({ detail: 'Name is taken.' }, 409));
  await expect(apiClient('Fallback.')('/api/thing')).rejects.toThrow(
    'Name is taken.',
  );
});

test('a detail that is not a message reports the fallback', async () => {
  respondWith(jsonResponse({ detail: [{ msg: 'field required' }] }, 422));
  await expect(apiClient('Fallback.')('/api/thing')).rejects.toThrow(
    /^Fallback\.$/,
  );
});

test('a failed request with a non-JSON body reports the fallback', async () => {
  respondWith(new Response('Internal Server Error', { status: 500 }));
  await expect(apiClient('Fallback.')('/api/thing')).rejects.toThrow(
    /^Fallback\.$/,
  );
});

test('a fallback can include the response status', async () => {
  respondWith(jsonResponse({}, 503));
  const request = apiClient((status) => `Request failed (${status}).`);
  await expect(request('/api/thing')).rejects.toThrow(
    /^Request failed \(503\)\.$/,
  );
});
