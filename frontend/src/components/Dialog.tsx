import { useEffect, useId, useRef, type ReactNode } from "react";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md";
  /** Description rendered under the title and wired to aria-describedby. */
  description?: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const RESTORE_TRIES = 200;     // x 50 ms: wait up to 10 s for the request a dialog started
const RESTORE_EVERY_MS = 50;

/**
 * Give focus back to the element that opened a dialog. A dialog action
 * usually starts a request, and every button is disabled while it runs, so a
 * plain focus() on close lands on <body>. Retry until the opener is enabled
 * again, unless focus has moved somewhere else meanwhile (another dialog, the
 * user clicking on). If the opener is gone (the pick was removed), focus the
 * page's main region instead of leaving it on <body>.
 */
export function restoreFocus(opener: HTMLElement | null, tries = RESTORE_TRIES): void {
  if (!opener) return;
  const attempt = (left: number) => {
    const active = document.activeElement;
    if (active && active !== document.body && active !== opener) return; // the user has moved on
    if (!document.contains(opener)) {
      const main = document.querySelector<HTMLElement>("main, [role='main']");
      if (main) {
        if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
        main.focus();
      }
      return;
    }
    const disabled = (opener as HTMLButtonElement).disabled === true || opener.getAttribute("aria-disabled") === "true";
    if (!disabled) {
      opener.focus();
      if (document.activeElement === opener) return;
    }
    if (left > 0) window.setTimeout(() => attempt(left - 1), RESTORE_EVERY_MS);
  };
  attempt(tries);
}

/** Modal dialog: focus moves in, Tab is trapped, Escape closes, focus returns to the opener. */
export function Dialog({ title, onClose, children, footer, size = "md", description }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const el = ref.current;
    if (el) {
      const auto = el.querySelector<HTMLElement>("[data-autofocus]") ?? el.querySelector<HTMLElement>(FOCUSABLE);
      (auto ?? el).focus();
    }
    return () => restoreFocus(opener);
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      closeRef.current();
      return;
    }
    if (e.key === "Tab" && ref.current) {
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) closeRef.current();
      }}
    >
      <div
        ref={ref}
        className={`dialog dialog-${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <div className="dialog-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={() => closeRef.current()}>
            ×
          </button>
        </div>
        {description && (
          <p id={descId} className="dialog-desc">
            {description}
          </p>
        )}
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

interface ConfirmProps {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  danger?: boolean;
}

export function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel, danger }: ConfirmProps) {
  return (
    <Dialog
      title={title}
      onClose={onCancel}
      size="sm"
      description={message}
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className={danger ? "btn btn-danger" : "btn btn-primary"} onClick={onConfirm} data-autofocus>
            {confirmLabel}
          </button>
        </>
      }
    >
      {null}
    </Dialog>
  );
}
