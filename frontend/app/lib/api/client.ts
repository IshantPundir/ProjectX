const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { token?: string } = {},
): Promise<T> {
  const { token, ...fetchOptions } = options;

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
    throw new Error(message);
  }

  return res.json();
}
