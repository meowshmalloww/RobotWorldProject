/* Typed REST client for the RobotWorld FastAPI backend.
   Base URL is the relative `/api` prefix — the vite dev proxy forwards it
   to http://127.0.0.1:8000. */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const configuredOrigin = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, "") ?? "";
const BASE = `${configuredOrigin}/api`;

/** Same-origin in development/production; explicit origin for a remote Spark API. */
export function websocketUrl(path: string): string {
  if (configuredOrigin) {
    const url = new URL(configuredOrigin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = `/ws${path}`;
    return url.toString();
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws${path}`;
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "RobotWorld API is offline. Start the local service or check the configured API origin.");
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      const detail = data?.detail ?? data?.message;
      if (typeof detail === "string") message = detail;
      else if (detail) message = JSON.stringify(detail);
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
};

export async function uploadBinary<T>(path: string, file: File): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}${path.includes("?") ? "&" : "?"}filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
  } catch {
    throw new ApiError(0, "RobotWorld API is offline.");
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try { const data = await res.json(); message = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? data); } catch { /* keep status */ }
    throw new ApiError(res.status, message);
  }
  return await res.json() as T;
}

/** Download a backend file as a blob (e.g. USD artifacts). */
export async function downloadApiFile(path: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") message = data.detail;
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
