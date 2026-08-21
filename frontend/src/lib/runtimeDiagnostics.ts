export interface FrontendDiagnostic {
  source: "react" | "window" | "promise" | "api";
  message: string;
  stack?: string;
  componentStack?: string;
  route?: string;
  userAgent?: string;
}

const SECRET_PATTERN = /(sk-(?:proj-)?[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})/gi;

function safeText(value: unknown, limit: number): string {
  return String(value ?? "")
    .replace(SECRET_PATTERN, "[redacted]")
    .slice(0, limit);
}

export function reportFrontendDiagnostic(input: FrontendDiagnostic): void {
  const payload = {
    source: input.source,
    message: safeText(input.message, 2000),
    stack: safeText(input.stack, 12000),
    componentStack: safeText(input.componentStack, 8000),
    route: safeText(input.route ?? `${location.pathname}${location.hash}`, 500),
    userAgent: safeText(input.userAgent ?? navigator.userAgent, 500),
  };
  // Diagnostics must never cause another render failure. keepalive preserves
  // the report if a user reloads directly from the recovery screen.
  void fetch("/api/diagnostics/frontend-errors", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    keepalive: true,
  }).catch(() => undefined);
}

export function installGlobalDiagnostics(): () => void {
  const onError = (event: ErrorEvent) => reportFrontendDiagnostic({
    source: "window",
    message: event.message || "Unhandled browser error",
    stack: event.error instanceof Error ? event.error.stack : undefined,
  });
  const onRejection = (event: PromiseRejectionEvent) => {
    const reason = event.reason;
    reportFrontendDiagnostic({
      source: "promise",
      message: reason instanceof Error ? reason.message : safeText(reason, 2000),
      stack: reason instanceof Error ? reason.stack : undefined,
    });
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
