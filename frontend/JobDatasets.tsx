import { useState } from 'react';
import { focusField, type JobFields } from './jobFields';
import { datasetField, fieldId } from './jobRules';
import type { DatasetOption, Job } from './jobTypes';
import { contentsLabel } from './labels';

// The job's datasets, in the order their sections train. Exclusions stay on each dataset,
// because every job using that dataset shares them.
export function JobDatasets({
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
  const [adding, setAdding] = useState('');
  const { problemProps, problemText } = fields;
  const available = datasets.filter(
    (dataset) => !job.datasets.some((entry) => entry.dataset_id === dataset.id),
  );
  // A dataset added to the list in another row can't still be the pending choice.
  const chosen = available.some((dataset) => dataset.id === adding)
    ? adding
    : '';
  function add() {
    edit({
      ...job,
      datasets: [...job.datasets, { dataset_id: chosen, repeats: null }],
    });
    setAdding('');
    // The button is about to be disabled, which would otherwise drop keyboard focus.
    focusField('dataset_id');
  }
  // By position: an imported job can list a dataset twice, and Review asks to remove the extra row.
  function remove(row: number) {
    edit({
      ...job,
      datasets: job.datasets.filter((_, index) => index !== row),
    });
    focusField('dataset_id');
  }
  function setRepeats(row: number, value: string) {
    edit({
      ...job,
      datasets: job.datasets.map((entry, index) =>
        index === row
          ? { ...entry, repeats: value === '' ? null : Number(value) }
          : entry,
      ),
    });
  }
  return (
    <div className="job-datasets">
      <h3>Datasets</h3>
      {job.datasets.length > 0 && (
        <ol aria-label="Datasets in this job">
          {job.datasets.map((entry, row) => {
            const dataset = datasets.find(
              (item) => item.id === entry.dataset_id,
            );
            const key = datasetField(entry.dataset_id);
            const name = dataset?.name ?? 'deleted dataset';
            return (
              <li key={`${entry.dataset_id}-${row}`}>
                <div className="job-dataset-name">
                  {/* The library may still be loading, which doesn't mean the dataset is gone. */}
                  <strong>
                    {dataset?.name ?? (busy ? 'Loading…' : 'Deleted dataset')}
                  </strong>
                  {(dataset || !busy) && (
                    <small>
                      {dataset
                        ? contentsLabel(dataset.counts)
                        : 'No longer in the library. Remove it from this job.'}
                    </small>
                  )}
                  {problemText(key)}
                </div>
                {dataset && (
                  <label className="job-dataset-repeats">
                    Repeats
                    <input
                      id={fieldId(key)}
                      aria-label={`Repeats for ${name}`}
                      type="number"
                      step="1"
                      disabled={busy}
                      placeholder={String(job.config.repeats)}
                      value={entry.repeats ?? ''}
                      onChange={(event) => setRepeats(row, event.target.value)}
                      {...problemProps(key)}
                    />
                  </label>
                )}
                <button
                  id={dataset ? undefined : fieldId(key)}
                  aria-label={`Remove ${name}`}
                  disabled={busy}
                  onClick={() => remove(row)}
                >
                  Remove
                </button>
              </li>
            );
          })}
        </ol>
      )}
      <p className="hint">
        Blank repeats use the job's {job.config.repeats}. Each dataset trains as
        its own section, and images and videos stay separate.
      </p>
      <div className="job-dataset-add">
        <label className="field">
          Add a dataset
          <select
            id={fieldId('dataset_id')}
            {...problemProps('dataset_id')}
            disabled={busy}
            value={chosen}
            onChange={(event) => setAdding(event.target.value)}
          >
            <option value="">
              {available.length
                ? 'Select a registered dataset'
                : 'No more registered datasets'}
            </option>
            {available.map((dataset) => (
              <option key={dataset.id} value={dataset.id}>
                {dataset.name} ({contentsLabel(dataset.counts)})
              </option>
            ))}
          </select>
          {problemText('dataset_id')}
        </label>
        <button disabled={busy || !chosen} onClick={add}>
          Add dataset
        </button>
      </div>
    </div>
  );
}
