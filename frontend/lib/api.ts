/**
 * API client.
 *
 * Requests go to the app's own /api/* path, which Next rewrites to FastAPI. The
 * frontend never learns the backend origin and never handles CORS.
 */

import type { Envelope } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });

  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const err = (body as { error?: { message?: string; code?: string } } | null)?.error;
    throw new ApiError(
      err?.message ?? `Request failed (${response.status})`,
      err?.code ?? "HTTP_ERROR",
      response.status,
    );
  }

  return (body as Envelope<T>)?.data as T;
}

function withParams(path: string, params?: Record<string, string | number | boolean | undefined | null>) {
  if (!params) return path;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `${path}?${qs}` : path;
}

export const api = {
  get: <T>(path: string, params?: Record<string, string | number | boolean | undefined | null>) =>
    request<T>(withParams(path, params)),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
};

/** Paginated endpoints need the meta block, which `api.get` discards. */
export async function getPaginated<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>,
): Promise<{ data: T[]; meta: { total: number; page: number; page_size: number; pages: number } }> {
  const response = await fetch(withParams(path, params), { cache: "no-store" });
  if (!response.ok) throw new ApiError(`Request failed (${response.status})`, "HTTP_ERROR", response.status);
  return response.json();
}
