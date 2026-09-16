import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface SidePanelProps {
  id: string;
  kicker: string;
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  bodyClassName?: string;
}

const OPEN_EVENT = "range-planner:side-panel-open";

export function SidePanel({ id, kicker, title, open, onOpenChange, children, bodyClassName = "" }: SidePanelProps) {
  const triggerRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(() => window.innerWidth <= 900);
  const [position, setPosition] = useState({ top: 12, left: 12, width: 380 });

  useEffect(() => {
    const closeWhenAnotherPanelOpens = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== id) onOpenChange(false);
    };
    window.addEventListener(OPEN_EVENT, closeWhenAnotherPanelOpens);
    return () => window.removeEventListener(OPEN_EVENT, closeWhenAnotherPanelOpens);
  }, [id, onOpenChange]);

  useEffect(() => {
    if (open) window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: id }));
  }, [id, open]);

  useLayoutEffect(() => {
    if (!open) return;

    const place = () => {
      const isCompact = window.innerWidth <= 900;
      setCompact(isCompact);
      if (isCompact || !triggerRef.current) return;

      const trigger = triggerRef.current.getBoundingClientRect();
      const availableWidth = Math.max(280, window.innerWidth - trigger.right - 24);
      const width = Math.min(390, availableWidth);
      const panelHeight = popoverRef.current?.getBoundingClientRect().height ?? 0;
      const maxTop = Math.max(12, window.innerHeight - panelHeight - 12);
      setPosition({
        top: Math.min(Math.max(12, trigger.top), maxTop),
        left: trigger.right + 12,
        width,
      });
    };

    place();
    const frame = window.requestAnimationFrame(place);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  const body = (
    <div className={`panel-card__body ${bodyClassName}`.trim()}>{children}</div>
  );

  return (
    <div className={`panel-card panel-card--menu ${open ? "is-open" : ""}`} ref={triggerRef}>
      <button
        type="button"
        className="panel-card__header"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        onClick={() => onOpenChange(!open)}
      >
        <span className="panel-card__kicker">{kicker}</span>
        <h2>{title}</h2>
        <span className="panel-card__toggle" aria-hidden="true">{open ? "−" : "+"}</span>
      </button>

      {open && compact && <div id={`${id}-panel`} className="panel-card__inline">{body}</div>}
      {open && !compact && createPortal(
        <section
          id={`${id}-panel`}
          ref={popoverRef}
          className="panel-popover"
          style={position}
          aria-label={title}
        >
          <header className="panel-popover__header">
            <div>
              <span className="panel-card__kicker">{kicker}</span>
              <h2>{title}</h2>
            </div>
            <button type="button" onClick={() => onOpenChange(false)} aria-label={`Close ${title}`}>×</button>
          </header>
          {body}
        </section>,
        document.body,
      )}
    </div>
  );
}
