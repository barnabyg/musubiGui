import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Ellipsis } from 'lucide-react';

type Item = {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
};

// A menu button following the WAI-ARIA menu pattern: arrows move, Escape closes and returns focus.
export function ActionMenu({
  label,
  items,
  note,
  disabled,
}: {
  label: string;
  items: Item[];
  note?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const entries = () => [
    ...(root.current?.querySelectorAll<HTMLButtonElement>(
      '[role="menuitem"]:not(:disabled)',
    ) ?? []),
  ];
  useEffect(() => {
    if (!open) return;
    entries()[0]?.focus();
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  function close() {
    setOpen(false);
    trigger.current?.focus();
  }
  function move(event: KeyboardEvent) {
    const all = entries();
    const index = all.indexOf(document.activeElement as HTMLButtonElement);
    const target = {
      ArrowDown: all[(index + 1) % all.length],
      ArrowUp: all[(index - 1 + all.length) % all.length],
      Home: all[0],
      End: all[all.length - 1],
    }[event.key];
    if (target) {
      event.preventDefault();
      target.focus();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close();
    } else if (event.key === 'Tab') setOpen(false);
  }
  return (
    <div className="action-menu" ref={root}>
      <button
        ref={trigger}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <Ellipsis />
      </button>
      {open && (
        <div role="menu" aria-label={label} onKeyDown={move}>
          {items.map((item) => (
            <button
              key={item.label}
              role="menuitem"
              className={item.danger ? 'danger' : ''}
              disabled={item.disabled}
              onClick={() => {
                close();
                item.onSelect();
              }}
            >
              {item.label}
            </button>
          ))}
          {note && <p className="hint">{note}</p>}
        </div>
      )}
    </div>
  );
}
