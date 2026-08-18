import { useEffect, useRef } from "react";
import { websocketUrl } from "./api";

export type WsStatus = "connecting" | "open" | "closed";

interface UseWsOptions<T> {
  onMessage: (msg: T) => void;
  enabled?: boolean;
  onStatus?: (s: WsStatus) => void;
}

const MAX_BACKOFF = 30_000;

/**
 * WebSocket hook with capped exponential reconnect, jitter, and recovery when
 * the app comes online or becomes visible. Path is relative to /ws.
 * Returns a send function for client → server messages.
 */
export function useWs<T = unknown>(path: string | null, { onMessage, enabled = true, onStatus }: UseWsOptions<T>) {
  const msgRef = useRef(onMessage);
  msgRef.current = onMessage;
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;
  const sendRef = useRef<(msg: unknown) => void>(() => {});

  useEffect(() => {
    if (!path || !enabled) {
      sendRef.current = () => {};
      return;
    }
    let ws: WebSocket | null = null;
    let attempt = 0;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (stopped) return;
      statusRef.current?.("connecting");
      ws = new WebSocket(websocketUrl(path));
      sendRef.current = (msg) => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
      };
      ws.onopen = () => {
        attempt = 0;
        statusRef.current?.("open");
      };
      ws.onmessage = (ev) => {
        try {
          msgRef.current(JSON.parse(ev.data) as T);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (stopped) return;
        statusRef.current?.("closed");
        const delay = Math.min(1000 * 2 ** Math.min(attempt++, 5), MAX_BACKOFF) + Math.floor(Math.random() * 350);
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };

    const reconnectNow = () => {
      if (stopped || document.visibilityState === "hidden") return;
      if (timer) clearTimeout(timer);
      if (!ws || ws.readyState === WebSocket.CLOSED) connect();
    };
    window.addEventListener("online", reconnectNow);
    document.addEventListener("visibilitychange", reconnectNow);
    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      window.removeEventListener("online", reconnectNow);
      document.removeEventListener("visibilitychange", reconnectNow);
      sendRef.current = () => {};
      ws?.close();
    };
  }, [path, enabled]);

  // stable sender identity — callers can safely put it in dep arrays
  const send = useRef((msg: unknown) => sendRef.current(msg));
  return send.current;
}
