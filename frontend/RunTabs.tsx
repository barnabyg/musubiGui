export type RunTab = 'overview' | 'samples';

// The Overview and Samples tabs of Run details, with arrow, Home and End key movement.
export function RunTabs({
  tab,
  onChange,
}: {
  tab: RunTab;
  onChange: (tab: RunTab) => void;
}) {
  return (
    <div role="tablist" aria-label="Run views" className="run-tabs">
      {(['overview', 'samples'] as const).map((value) => (
        <button
          key={value}
          role="tab"
          id={`run-tab-${value}`}
          aria-controls={`run-panel-${value}`}
          aria-selected={tab === value}
          tabIndex={tab === value ? 0 : -1}
          onClick={() => onChange(value)}
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
              return;
            event.preventDefault();
            const next =
              event.key === 'Home'
                ? 'overview'
                : event.key === 'End'
                  ? 'samples'
                  : value === 'overview'
                    ? 'samples'
                    : 'overview';
            onChange(next);
            document.getElementById(`run-tab-${next}`)?.focus();
          }}
        >
          {value === 'overview' ? 'Overview' : 'Samples'}
        </button>
      ))}
    </div>
  );
}
