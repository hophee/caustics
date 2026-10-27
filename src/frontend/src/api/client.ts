/** HTTP client for the backend API (`/api/v1`). Types come from the generated OpenAPI schema. */
import type { components } from "./schema";

export type Schemas = components["schemas"];

export interface ProblemDetail {
  code: string;
  message: string;
  pointer?: string | null;
}

/** An error response in the backend's shape: `{"error": {code, message, details[]}}`. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ProblemDetail[];

  constructor(status: number, code: string, message: string, details: ProblemDetail[]) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** The detail whose `pointer` is `pointer`, if any (field errors, warning ids). */
  detail(pointer: string): ProblemDetail | undefined {
    return this.details.find((d) => d.pointer === pointer);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

let token: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setToken(value: string | null): void {
  token = value;
}

export function getToken(): string | null {
  return token;
}

/** Called once on a 401 (expired or revoked token): the auth layer signs out. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function url(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const qs = params.toString();
  return `/api/v1${path}${qs ? `?${qs}` : ""}`;
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(url(path, query), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && token) onUnauthorized?.();
    const error = (data as { error?: { code?: string; message?: string; details?: ProblemDetail[] } } | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "http_error",
      error?.message ?? `Ошибка сервера (${response.status})`,
      error?.details ?? [],
    );
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, undefined, query),
  post: <T>(path: string, body?: unknown, query?: Query) => request<T>("POST", path, body ?? {}, query),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
};

/**
 * A fresh client-generated id for idempotent patient writes (`submission_id`).
 * `crypto.randomUUID` exists only in secure contexts (HTTPS / localhost); over plain
 * HTTP (e.g. the bundle opened by IP) fall back to a v4 UUID from `getRandomValues`.
 */
export function newSubmissionId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = ((b[6] ?? 0) & 0x0f) | 0x40;
  b[8] = ((b[8] ?? 0) & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
