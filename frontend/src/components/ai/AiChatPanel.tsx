import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../ui/Icon";
import { Badge } from "../ui/controls";
import { PromptInput } from "../ui/ai-chat-input";
import { api, ApiError } from "../../lib/api";

interface ChatAction {
  label: string;
  tool: string;
  arguments: Record<string, unknown>;
  effect: "QUERY" | "MUTATION";
  approvalRequired: boolean;
}

interface ActionResult {
  status: "running" | "succeeded" | "failed";
  summary: string;
}

interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  at?: number;
  provenance?: string;
  model?: string;
  actions?: ChatAction[];
  actionResults?: Record<number, ActionResult>;
  hidden?: boolean;
}

interface ChatConfig {
  provider: { status: string; model: string | null; baseUrl: string | null; lastError: string | null };
  models: string[];
  defaultModel: string | null;
  efforts: string[];
  tools: { name: string; description: string; effect: string; approvalRequired: boolean }[];
}

interface ChatResponse {
  reply: string;
  actions: ChatAction[];
  provenance: string;
  model: string;
}

const STORAGE_KEY = "robotworld.ai-chat.thread";

/** PromptInput display labels → backend reasoning effort values. */
const EFFORT_MAP: Record<string, string> = { Low: "low", Medium: "medium", High: "high", XHigh: "xhigh", Max: "max", Ultra: "ultra" };
const EFFORT_LABELS = ["Low", "Medium", "High", "XHigh", "Max", "Ultra"];

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function timeLabel(at?: number): string {
  return at ? new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

function summarizeToolData(data: Record<string, unknown>): string {
  const entries = Object.entries(data).slice(0, 6);
  if (!entries.length) return "completed with no payload";
  return entries
    .map(([key, value]) => {
      const text = typeof value === "object" ? JSON.stringify(value) : String(value);
      return `${key}: ${text.length > 90 ? `${text.slice(0, 90)}…` : text}`;
    })
    .join(" · ");
}

export function AiChatPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [config, setConfig] = useState<ChatConfig | null>(null);
  const [thread, setThread] = useState<ChatMsg[]>(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]") as ChatMsg[];
      return Array.isArray(stored) ? stored.filter((m) => m && (m.role === "user" || m.role === "assistant")) : [];
    } catch {
      return [];
    }
  });
  const [thinking, setThinking] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const loadConfig = useCallback(async () => {
    try {
      setConfig(await api.get<ChatConfig>("/chat/config"));
    } catch {
      /* status strip falls back to defaults; chat replies surface the real error */
    }
  }, []);

  useEffect(() => {
    if (open) void loadConfig();
  }, [open, loadConfig]);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(thread.slice(-60)));
  }, [thread]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread, thinking, open]);

  const patchMsg = (id: string, patch: Partial<ChatMsg> | ((msg: ChatMsg) => Partial<ChatMsg>)) => {
    setThread((current) => current.map((msg) => (msg.id === id ? { ...msg, ...(typeof patch === "function" ? patch(msg) : patch) } : msg)));
  };

  const send = async (text: string, meta: { model: string; effort: string; attachments: File[] }) => {
    let content = text.trim();
    if (meta.attachments.length) {
      content += `\n\n[Attached images: ${meta.attachments.map((f) => f.name).join(", ")} — describe what to do with them]`;
    }
    if (!content || thinking) return;
    const userMsg: ChatMsg = { id: newId(), role: "user", content: text.trim(), at: Date.now() };
    const assistantId = newId();
    const history = [...thread, { ...userMsg, content }];
    setThread([...thread, userMsg, { id: assistantId, role: "assistant", content: "", at: Date.now() }]);
    setThinking(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await api.post<ChatResponse>(
        "/chat",
        {
          messages: history.slice(-24).map((m) => ({ role: m.role, content: m.content })),
          model: meta.model || null,
          effort: EFFORT_MAP[meta.effort] ?? null,
        },
        controller.signal,
      );
      patchMsg(assistantId, { content: response.reply, actions: response.actions, provenance: response.provenance, model: response.model });
    } catch (e) {
      if (controller.signal.aborted) {
        patchMsg(assistantId, (msg) => ({ content: msg.content || "Stopped." }));
      } else {
        patchMsg(assistantId, { content: `Request failed: ${e instanceof ApiError ? e.message : String(e)}`, provenance: "error:client" });
      }
    } finally {
      setThinking(false);
      abortRef.current = null;
    }
  };

  const runAction = async (msg: ChatMsg, index: number) => {
    const action = msg.actions?.[index];
    if (!action) return;
    patchMsg(msg.id, (m) => ({ actionResults: { ...(m.actionResults ?? {}), [index]: { status: "running", summary: "Requesting…" } } }));
    try {
      let approvalDecisionId: string | null = null;
      if (action.approvalRequired) {
        const approval = await api.post<{ id: string }>("/agent/approvals", {
          toolName: action.tool,
          arguments: action.arguments,
          approved: true,
          reason: "Approved from RobotWorld AI chat",
          decidedBy: "user",
        });
        approvalDecisionId = approval.id;
      }
      const result = await api.post<{ status: string; data: Record<string, unknown>; error?: string | null }>("/agent/tools/invoke", {
        toolName: action.tool,
        arguments: action.arguments,
        autonomyMode: action.approvalRequired ? "EXECUTE_WITH_APPROVAL" : "OBSERVE_ONLY",
        approvalDecisionId,
        actor: "ai-chat",
      });
      const summary = summarizeToolData(result.data ?? {});
      const resultSummary = summary || "completed";
      const updated = thread.map((item) => item.id === msg.id ? {
        ...item,
        actionResults: { ...(item.actionResults ?? {}), [index]: { status: "succeeded" as const, summary: resultSummary } },
      } : item);
      const toolMessage: ChatMsg = {
        id: newId(),
        role: "user",
        content: `Authoritative RobotWorld tool result — ${action.tool} succeeded: ${resultSummary.slice(0, 900)}. Continue from the refreshed workspace state and propose only the next necessary action.`,
        at: Date.now(),
        hidden: true,
      };
      const assistantId = newId();
      const history = [...updated, toolMessage];
      setThread([...history, { id: assistantId, role: "assistant", content: "", at: Date.now() }]);
      setThinking(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const continuation = await api.post<ChatResponse>(
          "/chat",
          {
            messages: history.slice(-24).map((item) => ({ role: item.role, content: item.content })),
            model: msg.model || config?.defaultModel || null,
            effort: "medium",
          },
          controller.signal,
        );
        patchMsg(assistantId, { content: continuation.reply, actions: continuation.actions, provenance: continuation.provenance, model: continuation.model });
      } catch (continuationError) {
        patchMsg(assistantId, {
          content: `The tool completed, but automatic planning could not continue: ${continuationError instanceof ApiError ? continuationError.message : String(continuationError)}`,
          provenance: "error:continuation",
        });
      } finally {
        setThinking(false);
        abortRef.current = null;
      }
    } catch (e) {
      const message = e instanceof ApiError ? e.message : String(e);
      patchMsg(msg.id, (m) => ({ actionResults: { ...(m.actionResults ?? {}), [index]: { status: "failed", summary: message } } }));
    }
  };

  const clearThread = () => {
    abortRef.current?.abort();
    setThread([]);
    window.localStorage.removeItem(STORAGE_KEY);
  };

  return (
    <>
      {open && <div className="copilot-scrim" onClick={onClose} />}
      <aside className={`copilot ${open ? "open" : ""}`} aria-hidden={!open}>
        <header className="copilot-head">
          <span className="copilot-brand">
            <span className="copilot-ico"><Icon name="spark" size={14} /></span>
            <span className="col" style={{ gap: 0 }}>
              <b style={{ fontSize: 13 }}>RobotWorld AI</b>
              <span className="micro t3">Plans, evaluates, and builds with real workspace state</span>
            </span>
          </span>
          <span className="row" style={{ gap: 4 }}>
            <button className="icon-btn btn-sm" title="Clear conversation" onClick={clearThread}><Icon name="trash" size={12} /></button>
            <button className="icon-btn btn-sm" title="Close (Esc)" onClick={onClose}><Icon name="x" size={13} /></button>
          </span>
        </header>

        <div className="copilot-statusbar">
          <span className="mono">{config?.defaultModel ?? "gpt-5.6-luna"}</span>
          <span className="t3">·</span>
          <span className="t3">{config?.provider.status === "healthy" ? "online" : config?.provider.status ?? "connecting"}</span>
        </div>

        <div className="copilot-scroll" ref={scrollRef}>
          {thread.length === 0 && (
            <div className="copilot-idle">
              <span className="copilot-idle-ico"><Icon name="spark" size={18} /></span>
              <span className="copilot-idle-title">RobotWorld AI</span>
              <span className="micro t3">Ask anything — it answers with real workspace state.</span>
            </div>
          )}

          {thread.filter((msg) => !msg.hidden).map((msg) => (
            <div key={msg.id} className={`copilot-msg ${msg.role}`}>
              <div className="copilot-msg-meta" style={msg.role === "user" ? { justifyContent: "flex-end" } : undefined}>
                {msg.role === "assistant" && <Icon name="spark" size={10} />}
                {msg.role === "assistant" && msg.model && <span className="mono">{msg.model}</span>}
                {msg.role === "assistant" && msg.provenance && <span className="mono t3">{msg.provenance}</span>}
                {msg.at && <span className="mono t3">{timeLabel(msg.at)}</span>}
              </div>
              <div className="copilot-bubble">
                {msg.role === "assistant" && thinking && msg.content === "" && !msg.actions?.length ? (
                  <span className="copilot-typing"><i /><i /><i /></span>
                ) : (
                  <span style={{ whiteSpace: "pre-wrap" }}>{msg.content}</span>
                )}
              </div>
              {msg.role === "assistant" && msg.actions && msg.actions.length > 0 && (
                <div className="copilot-actions">
                  {msg.actions.map((action, index) => {
                    const result = msg.actionResults?.[index];
                    const argText = JSON.stringify(action.arguments ?? {});
                    return (
                      <div key={`${action.tool}-${index}`} className="copilot-action-card">
                        <div className="row" style={{ gap: 7, minWidth: 0 }}>
                          <Icon name={action.effect === "MUTATION" ? "zap" : "search"} size={12} style={{ color: action.effect === "MUTATION" ? "var(--amber)" : "var(--teal)", flex: "none" }} />
                          <span className="col grow" style={{ gap: 1, minWidth: 0 }}>
                            <b className="small" style={{ overflowWrap: "anywhere" }}>{action.label}</b>
                            <span className="micro mono t3" style={{ overflowWrap: "anywhere" }}>{action.tool}{argText !== "{}" ? ` · ${argText.slice(0, 120)}${argText.length > 120 ? "…" : ""}` : ""}</span>
                          </span>
                          <Badge tone={action.effect === "MUTATION" ? "amber" : "teal"}>{action.effect === "MUTATION" ? "approval" : "read"}</Badge>
                        </div>
                        {result && (
                          <div className={`micro copilot-action-result ${result.status}`} style={{ marginTop: 6 }}>
                            {result.status === "running" ? "Running…" : result.status === "succeeded" ? `✓ ${result.summary}` : `✗ ${result.summary}`}
                          </div>
                        )}
                        <div className="row" style={{ marginTop: 7 }}>
                          <button
                            className="btn btn-secondary btn-sm"
                            disabled={thinking || result?.status === "running"}
                            onClick={() => void runAction(msg, index)}
                          >
                            <Icon name="play" size={10} /> {result?.status === "running" ? "Running…" : result?.status === "succeeded" ? "Run again" : action.effect === "MUTATION" ? "Approve & run" : "Run"}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="copilot-composer">
          <PromptInput
            onSubmit={(value, meta) => void send(value, meta)}
            placeholder="Talk to RobotWorld AI…"
            models={config?.models.length ? config.models : ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]}
            efforts={EFFORT_LABELS}
          />
        </div>
      </aside>
    </>
  );
}
