import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';

type Question = {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
};
type Pending = Question & { answer: (confirmed: boolean) => void };

let show: ((question: Pending) => void) | undefined;

// Resolves true only for the named confirm button; Cancel and Escape resolve false.
export function confirmDialog(question: Question): Promise<boolean> {
  return new Promise((resolve) => {
    if (show) show({ ...question, answer: resolve });
    else resolve(false);
  });
}

// The control may still be disabled for a moment while its screen finishes reacting to the answer.
function returnFocus(target: HTMLElement | null, tries: number) {
  if (!target?.isConnected || document.activeElement !== document.body) return;
  if ((target as HTMLButtonElement).disabled) {
    if (tries > 0) window.setTimeout(() => returnFocus(target, tries - 1), 50);
  } else target.focus();
}

export function ConfirmDialogHost() {
  const [pending, setPending] = useState<Pending | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  const titleId = useId();
  useEffect(() => {
    show = setPending;
    // Tracked here because a screen's busy state can disable the control before the dialog opens.
    const remember = (event: FocusEvent) => {
      if (!dialog.current?.contains(event.target as Node))
        lastFocus.current = event.target as HTMLElement;
    };
    document.addEventListener('focusin', remember);
    return () => {
      show = undefined;
      document.removeEventListener('focusin', remember);
    };
  }, []);
  useEffect(() => {
    if (!pending) return;
    dialog.current?.showModal();
    // The safe choice takes focus, so a stray Enter can't confirm anything.
    cancel.current?.focus();
  }, [pending]);
  function finish(confirmed: boolean) {
    pending?.answer(confirmed);
    setPending(null);
    // After the dialog has left the page, while it still holds focus now.
    const target = lastFocus.current;
    window.setTimeout(() => returnFocus(target, 20), 0);
  }
  // A modal dialog would otherwise let Tab leave for the browser's own controls.
  function trap(event: KeyboardEvent) {
    if (event.key !== 'Tab') return;
    const buttons = [
      ...(dialog.current?.querySelectorAll<HTMLElement>('button') ?? []),
    ];
    const last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === buttons[0]) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      buttons[0].focus();
    }
  }
  if (!pending) return null;
  return (
    <dialog
      ref={dialog}
      className="confirm-dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        finish(false);
      }}
      onKeyDown={trap}
    >
      <h2 id={titleId}>{pending.title}</h2>
      <div className="confirm-body">{pending.body}</div>
      <div className="actions">
        <button ref={cancel} onClick={() => finish(false)}>
          {pending.cancelLabel ?? 'Cancel'}
        </button>
        <button
          className={pending.danger ? 'danger solid' : 'primary'}
          onClick={() => finish(true)}
        >
          {pending.confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
