import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refetch: () => void;
}

/**
 * Fetch a REST endpoint on mount and whenever `path` changes.
 * Pass `path = null` to skip fetching. Aborts in-flight requests on
 * unmount/re-fetch and ignores stale responses.
 */
export function useApi<T>(path: string | null, deps: unknown[] = []): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(!!path);
  const seq = useRef(0);

  const run = useCallback(() => {
    if (!path) return;
    const id = ++seq.current;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    api
      .get<T>(path, ctrl.signal)
      .then((d) => {
        if (seq.current !== id) return; // stale
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (seq.current !== id || (e instanceof DOMException && e.name === "AbortError")) return;
        setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
        setLoading(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => run(), [run]);

  const refetch = useCallback(() => {
    run();
  }, [run]);

  return { data, error, loading, refetch };
}
