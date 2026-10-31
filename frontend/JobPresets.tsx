import type { Job, Preset } from './jobTypes';

export function JobPresets({
  job,
  busy,
  dirty,
  presets,
  presetName,
  setPresetName,
  presetId,
  setPresetId,
  savePreset,
  applyPreset,
}: {
  job: Job;
  busy: boolean;
  dirty: boolean;
  presets: Preset[];
  presetName: string;
  setPresetName: (name: string) => void;
  presetId: string;
  setPresetId: (id: string) => void;
  savePreset: () => Promise<void>;
  applyPreset: () => Promise<void>;
}) {
  const savePresetReason = dirty
    ? 'Save your edits to save them as a preset.'
    : !presetName.trim()
      ? 'Enter a preset name to save one.'
      : '';
  const applyPresetReason = dirty
    ? 'Save your edits to apply a preset.'
    : !presetId
      ? 'Select a preset to apply it.'
      : '';
  return (
    <section className="panel">
      <h2>Presets</h2>
      <div className="columns">
        <label className="field">
          Preset name
          <input
            disabled={busy}
            value={presetName}
            onChange={(event) => setPresetName(event.target.value)}
          />
        </label>
        <label className="field">
          Compatible preset
          <select
            disabled={busy}
            value={presetId}
            onChange={(event) => setPresetId(event.target.value)}
          >
            <option value="">Select a preset</option>
            {presets
              .filter((preset) => preset.family === job.family)
              .map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
          </select>
        </label>
      </div>
      <p className="hint">
        Measured presets set only size, frames, rank and memory controls
        qualified on the target PC. Smoke tests also set steps, checkpoints and
        the sample, to check that training runs. Saved presets replace all
        settings.
      </p>
      <div className="actions">
        <button
          disabled={busy || !!savePresetReason}
          title={savePresetReason || undefined}
          aria-describedby={savePresetReason ? 'save-preset-reason' : undefined}
          onClick={() => void savePreset()}
        >
          Save as preset
        </button>
        <button
          disabled={busy || !!applyPresetReason}
          title={applyPresetReason || undefined}
          aria-describedby={
            applyPresetReason ? 'apply-preset-reason' : undefined
          }
          onClick={() => void applyPreset()}
        >
          Apply preset
        </button>
      </div>
      {savePresetReason && (
        <p id="save-preset-reason" className="hint">
          {savePresetReason}
        </p>
      )}
      {applyPresetReason && (
        <p id="apply-preset-reason" className="hint">
          {applyPresetReason}
        </p>
      )}
    </section>
  );
}
