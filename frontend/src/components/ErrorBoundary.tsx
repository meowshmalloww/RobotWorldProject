import { Component, type ErrorInfo, type ReactNode } from "react";
import { reportFrontendDiagnostic } from "../lib/runtimeDiagnostics";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  stack: string | null;
}

export class AppErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null, stack: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      error,
      stack: error.stack ?? null,
    };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    if (typeof document !== "undefined") {
      document.body.classList.remove("col-resizing", "row-resizing");
    }
    this.setState({
      error,
      stack: errorInfo.componentStack ? `${error.stack ?? ""}\n${errorInfo.componentStack}` : error.stack ?? null,
    });
    reportFrontendDiagnostic({
      source: "react",
      message: error.message,
      stack: error.stack,
      componentStack: errorInfo.componentStack ?? undefined,
    });
  }

  recover = () => {
    if (typeof document !== "undefined") {
      document.body.classList.remove("col-resizing", "row-resizing");
    }
    this.setState({ hasError: false, error: null, stack: null });
  };

  render() {
    const { hasError, error, stack } = this.state;
    if (hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div style={{
          width: "100%",
          height: "100%",
          minHeight: 0,
          display: "grid",
          placeItems: "center",
          background: "#101113",
          color: "#d8dde6",
          padding: "16px",
        }}
        >
          <div style={{
            width: "min(780px, 90vw)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 10,
            background: "#181a1e",
            padding: 16,
            boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
          }}
          >
            <h2 style={{ marginTop: 0, marginBottom: 8 }}>UI rendering stalled</h2>
            <p style={{ margin: "6px 0", color: "#96a3b3" }}>
              A runtime error interrupted the interface. This usually happens during drag interactions or fast layout updates.
              Use <b>Retry</b> to restore the shell in-place, or <b>Reload</b> if needed.
            </p>
            <p style={{ margin: "8px 0", fontSize: 12, color: "#8391a1", wordBreak: "break-word" }}>
              {error ? error.message : "Unknown error"}
            </p>
            <pre style={{ margin: "8px 0", padding: 10, background: "#101114", borderRadius: 8, maxHeight: 150, overflow: "auto", fontSize: 11 }}>
              {stack ?? "No stack trace available"}
            </pre>
            <div style={{ display: "flex", gap: 10 }}>
              <button type="button" onClick={this.recover} style={{ padding: "8px 12px", borderRadius: 6, border: 0, background: "#3b82f6", color: "#fff" }}>
                Retry
              </button>
              <button type="button" onClick={() => window.location.reload()} style={{ padding: "8px 12px", borderRadius: 6, border: 0, background: "#334155", color: "#fff" }}>
                Reload
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
