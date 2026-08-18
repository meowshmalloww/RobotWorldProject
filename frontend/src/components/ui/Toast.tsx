import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export type ToastKind = "ok" | "err" | "info";
export interface ToastItem { id: number; kind: ToastKind; title: string; msg?: string }

const ToastCtx = createContext<{ push: (kind: ToastKind, title: string, msg?: string) => void }>({ push: () => {} });

export const useToast = () => useContext(ToastCtx);

const ICONS: Record<ToastKind, IconName> = { ok: "check", err: "warning", info: "info" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = useCallback((kind: ToastKind, title: string, msg?: string) => {
    const id = nextId.current++;
    setItems((xs) => [...xs.slice(-3), { id, kind, title, msg }]);
    setTimeout(() => dismiss(id), 4200);
  }, [dismiss]);

  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="toast-stack">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role="status">
            <span className="t-ico"><Icon name={ICONS[t.kind]} size={14} /></span>
            <div className="t-body">
              <div className="t-title">{t.title}</div>
              {t.msg && <div className="t-msg">{t.msg}</div>}
            </div>
            <button className="t-x" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <Icon name="x" size={12} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
