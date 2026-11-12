import type { JobFields } from './jobFields';
import { fieldId } from './jobRules';
import type { DatasetOption, Job } from './jobTypes';

export function JobDatasetResolutions({
  job,
  busy,
  fields,
  edit,
  datasets,
}: {
  job: Job;
  busy: boolean;
  fields: JobFields;
  edit: (next: Job) => void;
  datasets: DatasetOption[];
}) {
  function setResolution(row: number, resolution: [number, number] | null) {
    edit({
      ...job,
      datasets: job.datasets.map((entry, index) =>
        index === row ? { ...entry, resolution } : entry,
      ),
    });
  }
  return (
    <div>
      <h3>Dataset training resolutions</h3>
      <p className="hint">
        Each dataset uses the default or a custom training size for this job.
        With bucketing enabled, actual sizes vary with aspect ratio.
      </p>
      {job.datasets.length === 0 && (
        <p>
          Add a dataset in Job &amp; dataset to choose its training resolution.
        </p>
      )}
      {job.datasets.map((entry, row) => {
        const dataset = datasets.find((item) => item.id === entry.dataset_id);
        if (!dataset) return null;
        const resolution = entry.resolution ?? [
          job.config.width,
          job.config.height,
        ];
        return (
          <fieldset
            className="job-dataset-resolution"
            key={`${entry.dataset_id}-${row}`}
          >
            <legend>{dataset.name} training resolution</legend>
            <p aria-live="polite">
              Effective training resolution: {resolution[0]} × {resolution[1]}
              {' · '}
              {entry.resolution == null ? 'default' : 'custom'}
            </p>
            <label className="job-toggle">
              <input
                type="checkbox"
                disabled={busy}
                aria-label={`Use default training resolution for ${dataset.name}`}
                checked={entry.resolution == null}
                onChange={(event) =>
                  setResolution(
                    row,
                    event.target.checked
                      ? null
                      : [job.config.width, job.config.height],
                  )
                }
              />
              Use default training resolution ({job.config.width} ×{' '}
              {job.config.height})
            </label>
            {entry.resolution != null && (
              <div className="number-row">
                {(['width', 'height'] as const).map((dimension, index) => {
                  const key = `resolution-${entry.dataset_id}-${dimension}`;
                  return (
                    <label className="field" key={dimension}>
                      Training {dimension}
                      <input
                        id={fieldId(key)}
                        type="number"
                        step="1"
                        disabled={busy}
                        aria-label={`Training ${dimension} for ${dataset.name}`}
                        value={entry.resolution![index]}
                        {...fields.problemProps(key)}
                        onChange={(event) => {
                          const next: [number, number] = [...entry.resolution!];
                          next[index] = Number(event.target.value);
                          setResolution(row, next);
                        }}
                      />
                      {fields.problemText(key)}
                    </label>
                  );
                })}
              </div>
            )}
          </fieldset>
        );
      })}
    </div>
  );
}
