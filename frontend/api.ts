// JSON requests to the backend. A failure reports the server's message when it sent one,
// otherwise the caller's fallback, which may name the response status.
type Fallback = string | ((status: number) => string);

export function apiClient(fallback: Fallback) {
  return async function request<T>(
    url: string,
    method = 'GET',
    body?: unknown,
  ): Promise<T> {
    const response = await fetch(url, {
      method,
      headers:
        body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const value = (await response.json().catch(() => null)) as {
        detail?: unknown;
      } | null;
      if (typeof value?.detail === 'string') throw new Error(value.detail);
      throw new Error(
        typeof fallback === 'string' ? fallback : fallback(response.status),
      );
    }
    return response.json() as Promise<T>;
  };
}
