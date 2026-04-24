const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

/**
 * Error thrown by apiFetch when the backend returns a non-OK response.
 *
 * Carries the HTTP status code alongside the parsed detail message so
 * callers can branch on status (e.g. 404 => "not found, return null")
 * without resorting to fragile substring matching on err.message.
 *
 * Extends the built-in Error, so any existing `catch (err) { err.message }`
 * code continues to work unchanged.
 */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { token?: string; signal?: AbortSignal } = {},
): Promise<T> {
  const { token, signal, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${path}`, {
    ...fetchOptions,
    headers,
    signal,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    // FastAPI 422 returns detail as an array of validation errors, not a string.
    // e.g. [{"loc": ["body","title"], "msg": "field required", "type": "..."}]
    const detail = body.detail;
    const message = Array.isArray(detail)
      ? detail.map((e: { msg: string }) => e.msg).join(', ')
      : typeof detail === 'string'
        ? detail
        : `API error: ${res.status}`;
    throw new ApiError(message, res.status);
  }

  // 204 No Content has an empty body — calling res.json() would throw.
  if (res.status === 204) return undefined as T;

  return res.json();
}
