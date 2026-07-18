export class ApiError extends Error {
  readonly requestId: string | null;

  constructor(message: string, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.requestId = requestId;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/control${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const requestId = response.headers.get("X-Request-Id");
    throw new ApiError(body?.error?.message ?? "Request failed", requestId);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function idempotencyKey(prefix: string): string {
  return `${prefix}:${crypto.randomUUID()}`;
}
