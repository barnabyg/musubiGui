import { useState } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';

export type JobSummary = {
  id: string;
  edited: string;
  last_run: { id: string; status: string } | null;
};
type Row = {
  id: string;
  name: string;
  family: 'wan' | 'h3';
  revision: number;
  dataset_id: string;
};
type Column = 'name' | 'model' | 'dataset' | 'revision' | 'run' | 'edited';
const columns: { key: Column; label: string }[] = [
  { key: 'name', label: 'Name' },
  { key: 'model', label: 'Model' },
  { key: 'dataset', label: 'Dataset' },
  { key: 'revision', label: 'Revision' },
  { key: 'run', label: 'Last Run' },
  { key: 'edited', label: 'Last edited' },
];

export function JobTable<T extends Row>({
  jobs,
  summaries,
  datasets,
  selectedId,
  disabled,
  onSelect,
}: {
  jobs: T[];
  summaries: JobSummary[];
  datasets: { id: string; name: string }[];
  selectedId?: string;
  disabled: boolean;
  onSelect: (job: T) => void;
}) {
  const [sort, setSort] = useState<{ key: Column; descending: boolean }>({
    key: 'edited',
    descending: true,
  });
  const summary = (id: string) => summaries.find((item) => item.id === id);
  const dataset = (job: Row) =>
    datasets.find((item) => item.id === job.dataset_id)?.name ?? '';
  const value = (job: Row, key: Column): string | number => {
    if (key === 'name') return job.name;
    if (key === 'model') return job.family;
    if (key === 'dataset') return dataset(job);
    if (key === 'revision') return job.revision;
    if (key === 'run') return summary(job.id)?.last_run?.status ?? '';
    return summary(job.id)?.edited ?? '';
  };
  const ordered = [...jobs].sort((a, b) => {
    const [first, second] = [value(a, sort.key), value(b, sort.key)];
    const order =
      typeof first === 'number' && typeof second === 'number'
        ? first - second
        : String(first).localeCompare(String(second));
    return sort.descending ? -order : order;
  });
  return (
    <div className="job-table">
      <table aria-label="Saved jobs">
        <thead>
          <tr>
            {columns.map(({ key, label }) => (
              <th
                key={key}
                scope="col"
                aria-sort={
                  sort.key !== key
                    ? 'none'
                    : sort.descending
                      ? 'descending'
                      : 'ascending'
                }
              >
                <button
                  onClick={() =>
                    setSort({
                      key,
                      descending: sort.key === key ? !sort.descending : false,
                    })
                  }
                >
                  {label}
                  {sort.key === key &&
                    (sort.descending ? <ArrowDown /> : <ArrowUp />)}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ordered.map((job) => {
            const info = summary(job.id);
            return (
              <tr
                key={job.id}
                className={job.id === selectedId ? 'selected' : ''}
              >
                <th scope="row">
                  <button
                    aria-label={`${job.name} · ${job.family.toUpperCase()} · r${job.revision}`}
                    aria-pressed={job.id === selectedId}
                    disabled={disabled}
                    onClick={() => onSelect(job)}
                  >
                    {job.name}
                  </button>
                </th>
                <td>{job.family === 'wan' ? 'Wan' : 'H3'}</td>
                <td>{dataset(job) || 'None selected'}</td>
                <td>r{job.revision}</td>
                <td>{info?.last_run?.status.replace('_', ' ') ?? 'No Runs'}</td>
                <td>{info ? new Date(info.edited).toLocaleString() : ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
