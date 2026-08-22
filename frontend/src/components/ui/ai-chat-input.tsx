/**
 * AI chat prompt input — morphing expandable composer.
 *
 * Native port of the shadcn/Tailwind `ai-chat-input` component to the
 * RobotWorld design system (no Tailwind dependency): identical interaction
 * physics (spring max-width/height morph, shared-element gallery, morphing
 * labels, sliding attachment tab, voice visualizer) with styles in
 * `frontend/src/styles/ai-chat.css`.
 */
import * as React from "react";
import { useRef, useState, useEffect, useCallback } from "react";
import { cn } from "../../lib/utils";

// ----------------------------------------------------------------------
// Transition Physics
// ----------------------------------------------------------------------
const SPRING_TRANSITION = "max-width 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), height 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)";
const SMOOTH_HEIGHT_TRANSITION = "max-width 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), height 0.15s ease-out";

// ----------------------------------------------------------------------
// Types
// ----------------------------------------------------------------------
interface Attachment {
  id: string;
  file: File;
  url: string;
  name: string;
  width?: number;
  height?: number;
}

// ----------------------------------------------------------------------
// Sub-components
// ----------------------------------------------------------------------
function MorphingText({ text }: { text: string }) {
  const [width, setWidth] = useState<number | "auto">("auto");
  const spanRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (spanRef.current) {
      setWidth(spanRef.current.offsetWidth);
    }
  }, [text]);

  return (
    <span className="ai-morph" style={{ width }}>
      <span ref={spanRef} className="ai-morph-measure">
        {text}
      </span>
      <span key={text} className="ai-morph-current">
        {text}
      </span>
    </span>
  );
}

const MODEL_ICONS: Record<string, string> = {
  cursor: "https://res.cloudinary.com/drhx7imeb/image/upload/v1781695268/cursor-ai-code-icon_j4vnux.svg",
  gemini: "https://res.cloudinary.com/drhx7imeb/image/upload/v1781695268/google-gemini-icon_l6kk5q.svg",
  gpt: "https://res.cloudinary.com/drhx7imeb/image/upload/v1781695269/openai-icon_zozuib.svg",
  claude: "https://res.cloudinary.com/drhx7imeb/image/upload/v1781695268/Claude_AI_symbol_yqfzlc.svg",
  glm: "https://res.cloudinary.com/drhx7imeb/image/upload/v1781695269/z-ai-icon_xi4xvo.svg",
};

function modelIconFor(model: string): string {
  const lower = model.toLowerCase();
  if (lower.includes("claude") || lower.includes("opus")) return MODEL_ICONS.claude;
  if (lower.includes("gemini")) return MODEL_ICONS.gemini;
  if (lower.includes("glm")) return MODEL_ICONS.glm;
  if (lower.includes("composer") || lower.includes("cursor")) return MODEL_ICONS.cursor;
  return MODEL_ICONS.gpt;
}

function ModelIcon({ model }: { model: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="ai-model-ico-fallback" aria-hidden="true">
        {model.slice(0, 1).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={modelIconFor(model)}
      alt=""
      className={cn("ai-model-ico", modelIconFor(model) === MODEL_ICONS.gpt && "ai-model-ico-invert")}
      onError={() => setFailed(true)}
    />
  );
}

function ArrowUpIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M7 12V2M7 2L2.5 6.5M7 2L11.5 6.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M7 2.5V11.5M2.5 7H11.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="9" height="9" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M2.5 2.5L11.5 11.5M11.5 2.5L2.5 11.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function DynamicBarsIcon({ level, levels }: { level: string; levels: string[] }) {
  const index = Math.max(0, levels.indexOf(level));
  const fill = Math.max(1, Math.ceil(((index + 1) / levels.length) * 4));
  const bars: [number, number][] = [
    [1, 8.5],
    [4.33, 6],
    [7.66, 3.5],
    [10.99, 1],
  ];
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      {bars.map(([x, y], i) => (
        <rect
          key={x}
          x={x}
          y={y}
          width={2.2}
          height={12.5 - y}
          rx={1}
          fill="currentColor"
          className="ai-bars"
          opacity={i < fill ? 1 : 0.3}
        />
      ))}
    </svg>
  );
}

// ----------------------------------------------------------------------
// Attachment Thumbnail
// ----------------------------------------------------------------------
function AttachmentThumb({
  attachment,
  index,
  onRemove,
  onOpen,
  registerRef,
}: {
  attachment: Attachment;
  index: number;
  onRemove: (id: string) => void;
  onOpen: (attachment: Attachment, rect: DOMRect) => void;
  registerRef: (id: string, el: HTMLButtonElement | null) => void;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  return (
    <button
      ref={(el) => {
        btnRef.current = el;
        registerRef(attachment.id, el);
      }}
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={(e) => {
        e.stopPropagation();
        if (btnRef.current) {
          onOpen(attachment, btnRef.current.getBoundingClientRect());
        }
      }}
      style={{ animationDelay: `${index * 35}ms` }}
      className="ai-thumb"
      aria-label={`Open preview of ${attachment.name}`}
    >
      <img src={attachment.url} alt={attachment.name} className="ai-thumb-img" draggable={false} />
      <span className={cn("ai-thumb-veil", isHovered && "on")}>
        <span
          role="button"
          tabIndex={-1}
          onMouseDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          onClick={(e) => {
            e.stopPropagation();
            onRemove(attachment.id);
          }}
          className={cn("ai-thumb-remove", isHovered ? "on" : "")}
          aria-label={`Remove ${attachment.name}`}
        >
          <CloseIcon />
        </span>
      </span>
    </button>
  );
}

// ----------------------------------------------------------------------
// Shared-Element Gallery Modal
// ----------------------------------------------------------------------
function AttachmentGalleryModal({
  attachment,
  originRect,
  onClose,
}: {
  attachment: Attachment;
  originRect: DOMRect;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<"opening" | "open" | "closing">("opening");
  const [targetRect, setTargetRect] = useState<{
    top: number;
    left: number;
    width: number;
    height: number;
    radius: number;
  } | null>(null);

  useEffect(() => {
    const maxW = Math.min(window.innerWidth * 0.86, 560);
    const maxH = Math.min(window.innerHeight * 0.78, 720);

    const naturalW = attachment.width || 800;
    const naturalH = attachment.height || 600;
    const scale = Math.min(maxW / naturalW, maxH / naturalH, 1.6);

    const width = naturalW * scale;
    const height = naturalH * scale;

    setTargetRect({
      top: (window.innerHeight - height) / 2,
      left: (window.innerWidth - width) / 2,
      width,
      height,
      radius: 20,
    });

    const raf = requestAnimationFrame(() => setPhase("open"));
    return () => cancelAnimationFrame(raf);
  }, [attachment]);

  const handleClose = useCallback(() => setPhase("closing"), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [handleClose]);

  const isOpen = phase === "open";
  const isClosing = phase === "closing";

  const geometry =
    isOpen && targetRect
      ? targetRect
      : { top: originRect.top, left: originRect.left, width: originRect.width, height: originRect.height, radius: 12 };

  const animEasing = isClosing ? "ease-out" : "cubic-bezier(0.175, 0.885, 0.32, 1.275)";
  const animDur = isClosing ? "0.3s" : "0.45s";
  const flipTransition = `top ${animDur} ${animEasing}, left ${animDur} ${animEasing}, width ${animDur} ${animEasing}, height ${animDur} ${animEasing}, border-radius ${animDur} ${animEasing}`;

  return (
    <div className="ai-gallery" onClick={handleClose} role="dialog" aria-modal="true">
      <div className="ai-gallery-veil" style={{ opacity: isOpen ? 1 : 0 }} />
      <div
        style={{
          position: "fixed",
          top: geometry.top,
          left: geometry.left,
          width: geometry.width,
          height: geometry.height,
          borderRadius: geometry.radius,
          transition: flipTransition,
          overflow: "hidden",
          boxShadow: isOpen ? "0 24px 60px -12px rgb(0 0 0 / 0.35)" : "0 0px 0px 0px rgb(0 0 0 / 0)",
        }}
        className="ai-gallery-frame"
        onTransitionEnd={() => {
          if (phase === "closing") onClose();
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <img src={attachment.url} alt={attachment.name} className="ai-gallery-img" draggable={false} />
      </div>

      <button
        type="button"
        aria-label="Close image gallery"
        onClick={handleClose}
        style={{ opacity: isOpen ? 1 : 0, transform: isOpen ? "scale(1)" : "scale(0.7)" }}
        className={cn("ai-gallery-close", !isOpen && "off")}
      >
        <span style={{ transform: "scale(1.5)", display: "flex" }}>
          <CloseIcon />
        </span>
      </button>
    </div>
  );
}

// ----------------------------------------------------------------------
// Main Component
// ----------------------------------------------------------------------

export interface PromptInputProps {
  onSubmit?: (value: string, meta: { model: string; effort: string; attachments: File[] }) => void;
  placeholder?: string;
  className?: string;
  models?: string[];
  efforts?: string[];
  defaultValue?: string;
  value?: string;
  onChange?: (value: string) => void;
  maxAttachments?: number;
}

export const PromptInput = React.forwardRef<HTMLDivElement, PromptInputProps>(
  (
    {
      onSubmit,
      placeholder = "Ask anything",
      className,
      models = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"],
      efforts = ["Low", "Medium", "High", "XHigh", "Max", "Ultra"],
      defaultValue = "",
      value: controlledValue,
      onChange,
      maxAttachments = 6,
    },
    ref,
  ) => {
    const [expanded, setExpanded] = useState(false);
    const [isSmoothResize, setIsSmoothResize] = useState(false);
    const [localValue, setLocalValue] = useState(defaultValue);
    const [selectedModel, setSelectedModel] = useState(models[0]);
    const [effortIndex, setEffortIndex] = useState(1);
    const [isModelSelectOpen, setIsModelSelectOpen] = useState(false);

    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const [activeAttachment, setActiveAttachment] = useState<{ attachment: Attachment; rect: DOMRect } | null>(null);

    const [hoverStyle, setHoverStyle] = useState({ opacity: 0, transform: "translateY(0px) scale(0.95)", transition: "none" });
    const [containerHeight, setContainerHeight] = useState(116);
    const [textareaHeight, setTextareaHeight] = useState(68);
    const [isScrolling, setIsScrolling] = useState(false);

    const isControlled = controlledValue !== undefined;
    const value = isControlled ? controlledValue : localValue;
    const hasValue = value.trim() !== "" || attachments.length > 0;
    const hasAttachments = attachments.length > 0;

    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const internalContainerRef = useRef<HTMLDivElement>(null);
    const topFadeRef = useRef<HTMLDivElement>(null);
    const bottomFadeRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const thumbRefs = useRef<Map<string, HTMLButtonElement | null>>(new Map());

    // If the models list arrives after mount (fetched config), keep selection valid.
    useEffect(() => {
      if (models.length && !models.includes(selectedModel)) setSelectedModel(models[0]);
    }, [models, selectedModel]);

    const updateFades = () => {
      const el = textareaRef.current;
      if (!el) return;
      const { scrollTop, scrollHeight, clientHeight } = el;
      if (topFadeRef.current) {
        topFadeRef.current.style.opacity = Math.min(scrollTop / 20, 1).toString();
      }
      if (bottomFadeRef.current) {
        const bottomScroll = scrollHeight - clientHeight - scrollTop;
        bottomFadeRef.current.style.opacity = Math.min(Math.max(bottomScroll - 16, 0) / 10, 1).toString();
      }
    };

    const handleValueChange = useCallback(
      (val: string) => {
        setIsSmoothResize(true);
        if (!isControlled) setLocalValue(val);
        onChange?.(val);
      },
      [isControlled, onChange],
    );

    const expand = () => {
      setIsSmoothResize(false);
      setExpanded(true);
    };

    useEffect(() => {
      if ((value.trim() !== "" || hasAttachments) && !expanded) {
        setIsSmoothResize(false);
        setExpanded(true);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value, expanded, hasAttachments]);

    useEffect(() => {
      if (expanded) {
        const timer = setTimeout(() => {
          if (textareaRef.current) {
            textareaRef.current.focus();
            const length = textareaRef.current.value.length;
            textareaRef.current.setSelectionRange(length, length);
          }
        }, 50);
        return () => clearTimeout(timer);
      }
    }, [expanded]);

    // ONLY updates height on value/text change. Adding attachments leaves this isolated.
    useEffect(() => {
      if (!textareaRef.current) return;
      const el = textareaRef.current;

      const currentHeight = el.style.height;
      el.style.transition = "none";
      el.style.height = "0px";
      const scrollHeight = el.scrollHeight;
      el.style.height = currentHeight;
      void el.offsetHeight;
      el.style.transition = "";

      const newHeight = Math.max(68, Math.min(scrollHeight, 160));
      el.style.height = `${newHeight}px`;

      setTextareaHeight(newHeight);
      setIsScrolling(scrollHeight > 160);

      setTimeout(updateFades, 0);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value, expanded]);

    useEffect(() => {
      setContainerHeight(Math.max(116, textareaHeight + 48));
      setTimeout(updateFades, 0);
    }, [textareaHeight]);

    useEffect(() => {
      if (!isModelSelectOpen) return;
      const handleOutsideClick = (e: MouseEvent) => {
        if (internalContainerRef.current && !internalContainerRef.current.contains(e.target as Node)) {
          setIsModelSelectOpen(false);
        }
      };
      document.addEventListener("mousedown", handleOutsideClick);
      return () => document.removeEventListener("mousedown", handleOutsideClick);
    }, [isModelSelectOpen]);

    const handleBlur = (e: React.FocusEvent<HTMLDivElement>) => {
      if (internalContainerRef.current && internalContainerRef.current.contains(e.relatedTarget as Node)) return;
      if (value.trim() === "" && !hasAttachments) {
        setIsSmoothResize(false);
        setExpanded(false);
        setIsModelSelectOpen(false);
      }
    };

    const handleSubmit = () => {
      if (value.trim() === "" && !hasAttachments) return;
      setIsSmoothResize(false);
      onSubmit?.(value, { model: selectedModel, effort: efforts[effortIndex], attachments: attachments.map((a) => a.file) });
      handleValueChange("");
      attachments.forEach((a) => URL.revokeObjectURL(a.url));
      setAttachments([]);
      setExpanded(false);
      setIsModelSelectOpen(false);
    };

    const cycleEffort = (e: React.MouseEvent) => {
      e.stopPropagation();
      setEffortIndex((prev) => (prev + 1) % efforts.length);
    };

    const openFileChooser = (e: React.MouseEvent) => {
      e.stopPropagation();
      fileInputRef.current?.click();
    };

    const addAttachment = (file: File, url: string, width: number, height: number) => {
      const id = `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`;
      setAttachments((prev) => [...prev, { id, file, url, name: file.name, width, height }]);
    };

    const handleFilesChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []).filter((f) => f.type.startsWith("image/"));
      e.target.value = "";

      if (files.length === 0) return;
      const room = Math.max(0, maxAttachments - attachments.length);
      const accepted = files.slice(0, room);

      if (!expanded) {
        setIsSmoothResize(false);
        setExpanded(true);
      } else {
        setIsSmoothResize(true);
      }

      for (const file of accepted) {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => addAttachment(file, url, img.naturalWidth, img.naturalHeight);
        img.onerror = () => addAttachment(file, url, 800, 600);
        img.src = url;
      }
    };

    const removeAttachment = (id: string) => {
      setIsSmoothResize(true);
      setAttachments((prev) => {
        const target = prev.find((a) => a.id === id);
        if (target) URL.revokeObjectURL(target.url);
        return prev.filter((a) => a.id !== id);
      });
      thumbRefs.current.delete(id);
    };

    // Send button state: always the arrow, dimmed until there is content.
    const showArrow = hasValue;

    const onActionButtonClick = (e: React.MouseEvent) => {
      e.preventDefault();
      if (hasValue) handleSubmit();
    };

    return (
      <>
        {/* Outer Wrapper for positioning and max-width scaling */}
        <div
          ref={(node) => {
            if (typeof ref === "function") ref(node);
            else if (ref) (ref as React.MutableRefObject<HTMLDivElement | null>).current = node;
            internalContainerRef.current = node;
          }}
          onBlur={handleBlur}
          className={cn("ai-wrap", className)}
          style={{
            maxWidth: expanded ? 480 : 320,
            transition: isSmoothResize
              ? "max-width 0.15s ease-out"
              : "max-width 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)",
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            onChange={handleFilesChosen}
            className="ai-hidden"
            tabIndex={-1}
            aria-hidden="true"
          />

          {/* Independent Attachment Tab (slides up from behind the prompt input) */}
          <div
            aria-hidden={!hasAttachments}
            style={{
              height: hasAttachments && expanded ? 68 : 0,
              transition: isSmoothResize
                ? "height 0.15s ease-out"
                : "height 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)",
            }}
            className="ai-attach-zone"
          >
            <div
              style={{
                position: "absolute",
                bottom: -8,
                left: 20,
                right: 20,
                height: 68,
                transform: hasAttachments && expanded ? "translateY(0)" : "translateY(100%)",
                opacity: hasAttachments && expanded ? 1 : 0,
                transition: isSmoothResize
                  ? "transform 0.15s ease-out, opacity 0.15s ease-out"
                  : "transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.3s ease-out",
              }}
              className="ai-attach-strip prompt-scrollbar"
            >
              {attachments.map((attachment, index) => (
                <AttachmentThumb
                  key={attachment.id}
                  attachment={attachment}
                  index={index}
                  onRemove={removeAttachment}
                  onOpen={(a, rect) => setActiveAttachment({ attachment: a, rect })}
                  registerRef={(id, el) => thumbRefs.current.set(id, el)}
                />
              ))}
            </div>
          </div>

          {/* Main Input Card */}
          <div
            onMouseDown={(e) => {
              const isTextarea = e.target === textareaRef.current;
              if (expanded && !isTextarea) {
                e.preventDefault();
                textareaRef.current?.focus();
              }
            }}
            style={{
              borderRadius: 24,
              height: expanded ? containerHeight : 48,
              transition: isSmoothResize ? SMOOTH_HEIGHT_TRANSITION : SPRING_TRANSITION,
              overflow: expanded ? "visible" : "hidden",
            }}
            className={cn("ai-card", expanded ? "cursor-text" : "cursor-default")}
          >
            <textarea
              ref={textareaRef}
              value={value}
              onChange={(e) => handleValueChange(e.target.value)}
              onScroll={updateFades}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
                if (e.key === "Escape" && value.trim() === "" && !hasAttachments) {
                  setIsSmoothResize(false);
                  setExpanded(false);
                  setIsModelSelectOpen(false);
                }
              }}
              placeholder={placeholder}
              aria-label="Prompt"
              style={{
                transition: isSmoothResize
                  ? "height 0.15s ease-out"
                  : "opacity 0.3s ease-out, transform 0.3s ease-out, height 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)",
              }}
              className={cn(
                "ai-textarea prompt-scrollbar",
                expanded ? "on" : "off",
                isScrolling ? "scrollable" : "clipped",
              )}
            />

            <div ref={topFadeRef} className="ai-fade ai-fade-top" />
            <div
              ref={bottomFadeRef}
              className="ai-fade ai-fade-bottom"
              style={{
                opacity: 0,
                top: `${textareaHeight - 32}px`,
                transition: isSmoothResize
                  ? "top 0.15s ease-out"
                  : "top 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)",
              }}
            />

            <button
              type="button"
              onClick={expand}
              style={{ transition: isSmoothResize ? "none" : "all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)" }}
              className={cn("ai-collapsed-btn", !expanded ? "on" : "off")}
              aria-label="Open prompt input"
            >
              {placeholder}
            </button>

            {/* Bottom Actions */}
            <div className={cn("ai-actions", expanded ? "on" : "off")}>
              <div style={{ position: "relative" }}>
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsModelSelectOpen((prev) => !prev);
                  }}
                  className={cn("ai-model-btn", isModelSelectOpen && "open")}
                  aria-label={`Select model. Current: ${selectedModel}`}
                >
                  <ModelIcon model={selectedModel} />
                  <span className="ai-label">
                    <MorphingText text={selectedModel} />
                  </span>
                </button>

                <div
                  style={{ transformOrigin: "bottom left" }}
                  onMouseLeave={() => {
                    setHoverStyle((prev) => ({
                      ...prev,
                      opacity: 0,
                      transform: prev.transform.replace("scale(1)", "scale(0.95)"),
                      transition: "opacity 0.2s ease-in, transform 0.2s ease-out",
                    }));
                  }}
                  className={cn("ai-model-pop", isModelSelectOpen ? "on" : "off")}
                >
                  <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: 2 }}>
                    <div style={hoverStyle} className="ai-hover-bar" />
                    {models.map((model, idx) => (
                      <button
                        key={model}
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onMouseEnter={() => {
                          setHoverStyle((prev) => ({
                            opacity: 1,
                            transform: `translateY(${idx * 34}px) scale(1)`,
                            transition:
                              prev.opacity === 0
                                ? "opacity 0.15s ease-out"
                                : "transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.15s ease",
                          }));
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedModel(model);
                          setIsModelSelectOpen(false);
                        }}
                        className="ai-model-item"
                      >
                        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <ModelIcon model={model} />
                          {model}
                        </span>
                        {model === selectedModel && <span className="ai-model-check">✓</span>}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={cycleEffort} className="ai-effort-btn">
                <DynamicBarsIcon level={efforts[effortIndex]} levels={efforts} />
                <span className="ai-label">
                  <MorphingText text={efforts[effortIndex]} />
                </span>
              </button>

              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={openFileChooser}
                disabled={attachments.length >= maxAttachments}
                className="ai-plus-btn"
                aria-label="Attach images"
              >
                <PlusIcon />
              </button>
            </div>

            <button
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
              }}
              onClick={onActionButtonClick}
              aria-label="Send prompt"
              className={cn("ai-send-btn", !showArrow && "dim")}
            >
              <ArrowUpIcon />
            </button>
          </div>
        </div>

        {activeAttachment && (
          <AttachmentGalleryModal
            attachment={activeAttachment.attachment}
            originRect={activeAttachment.rect}
            onClose={() => setActiveAttachment(null)}
          />
        )}
      </>
    );
  },
);

PromptInput.displayName = "PromptInput";
