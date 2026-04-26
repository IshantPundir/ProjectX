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
  code: string | null;
  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/**
 * Shape of a single entry in FastAPI's 422 `detail` array.
 *
 * `loc` is an array like `["body", "email"]` or `["body", "profile", "about"]`.
 * `applyApiErrorToForm` strips the leading `"body"` and walks the rest to
 * produce a dotted react-hook-form path.
 */
export interface FastApiValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/**
 * Specialised error for FastAPI 422 responses carrying structured
 * field errors. `fieldErrors` is REQUIRED — if the raw detail isn't
 * an array, `apiFetch` throws a plain `ApiError` instead.
 *
 * Callers should narrow on `err instanceof ApiValidationError` to get
 * typed, non-null access to `fieldErrors`. The `instanceof ApiError`
 * narrowing still matches (subclass), so existing 401 / 403 / 4xx
 * handlers continue to work unchanged.
 */
export class ApiValidationError extends ApiError {
  fieldErrors: FastApiValidationError[];
  constructor(message: string, fieldErrors: FastApiValidationError[]) {
    super(message, 422);
    this.name = "ApiValidationError";
    this.fieldErrors = fieldErrors;
  }
}

/**
 * Typed `fetch` wrapper for talking to Nexus.
 *
 * - Auto-injects `Authorization: Bearer <token>` when `token` is provided.
 * - Threads the optional `signal` into the underlying `fetch` so TanStack
 *   Query (or any caller) can cancel in-flight requests.
 * - Throws `ApiValidationError` on 422 responses whose `detail` is an
 *   array of `FastApiValidationError`. Throws plain `ApiError` on every
 *   other non-OK response (including 422 with a non-array detail).
 * - Returns `undefined` for 204 No Content responses. **Type the call as
 *   `apiFetch<void>('/api/...')` for endpoints that return 204** —
 *   otherwise the asserted `T` will silently be `undefined` at runtime
 *   and any property access on the result will throw.
 */
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
    const detail = body.detail;
    if (res.status === 422 && Array.isArray(detail)) {
      const fieldErrors = detail as FastApiValidationError[];
      const message = fieldErrors.map((e) => e.msg).join(', ');
      throw new ApiValidationError(message, fieldErrors);
    }
    const message = Array.isArray(detail)
      ? detail.map((e: { msg?: string }) => e.msg ?? String(e)).join(', ')
      : typeof detail === 'string'
        ? detail
        : `API error: ${res.status}`;
    const code = typeof body.code === 'string' ? body.code : null;
    throw new ApiError(message, res.status, code);
  }

  if (res.status === 204) return undefined as T;

  return res.json();
}
