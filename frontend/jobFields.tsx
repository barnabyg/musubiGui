import { errorField, componentError, fieldId, fieldProblems } from './jobRules';
import type { Job, JobConfig, Review, SetConfig } from './jobTypes';

export type JobFields = ReturnType<typeof jobFields>;

// Field problems and the field controls that show them.
export function jobFields(
  job: Job | null,
  busy: boolean,
  review: Review | null,
  config: SetConfig,
) {
  const liveProblems: Record<string, string> = job
    ? fieldProblems(job.family, job.config)
    : {};
  // Review errors about a field show beside it, unless the check while typing already says so.
  const problems: Record<string, string> = {};
  // Review reports size as one error, so the check while typing says which dimension it means.
  const reviewField = (message: string) => {
    const key = errorField(message);
    return key === 'width' && !liveProblems.width && liveProblems.height
      ? 'height'
      : key;
  };
  for (const message of review?.errors ?? []) {
    const key = reviewField(message);
    if (key && !liveProblems[key] && !componentError(message))
      problems[key] = message.slice(message.indexOf(':') + 2);
  }
  Object.assign(problems, liveProblems);
  const problemProps = (key: string) =>
    problems[key]
      ? {
          'aria-invalid': true,
          'aria-describedby': `${fieldId(key)}-problem`,
        }
      : {};
  function problemText(key: string) {
    return problems[key] ? (
      <small className="field-problem" id={`${fieldId(key)}-problem`}>
        {problems[key]}
      </small>
    ) : null;
  }
  function number(label: string, key: keyof JobConfig, help?: string) {
    return (
      <div className="field number-field">
        <label htmlFor={fieldId(key)}>{label}</label>
        <input
          id={fieldId(key)}
          type="number"
          step="any"
          value={String(job?.config[key] ?? '')}
          disabled={busy}
          onChange={(event) => config(key, Number(event.target.value))}
          {...problemProps(key)}
        />
        {problemText(key)}
        {help && <small>{help}</small>}
      </div>
    );
  }
  function toggle(label: string, key: keyof JobConfig, help?: string) {
    return (
      <div className="job-toggle">
        <input
          id={fieldId(key)}
          type="checkbox"
          checked={Boolean(job?.config[key])}
          disabled={busy}
          onChange={(event) => config(key, event.target.checked)}
          {...problemProps(key)}
        />
        <label htmlFor={fieldId(key)}>
          {label}
          {help && <small>{help}</small>}
          {problemText(key)}
        </label>
      </div>
    );
  }
  return { problems, reviewField, problemProps, problemText, number, toggle };
}

export function focusField(key: string) {
  const field = document.getElementById(fieldId(key));
  const section = field?.closest('details');
  if (section) section.open = true;
  field?.focus();
}
