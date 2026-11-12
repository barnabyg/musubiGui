import { useState } from 'react';
import type { JobFields } from './jobFields';
import { fieldId } from './jobRules';
import type { Job, SetConfig } from './jobTypes';

export function SampleGeometry({
  job,
  busy,
  fields,
  config,
}: {
  job: Job;
  busy: boolean;
  fields: JobFields;
  config: SetConfig;
}) {
  const width = job.config.sample_width ?? job.config.width;
  const height = job.config.sample_height ?? job.config.height;
  const frames = job.config.sample_frames ?? job.config.frames;
  const fps = job.family === 'wan' ? 16 : 24;
  const nativeWanPreview =
    job.family === 'wan' &&
    job.config.recipe !== 'alternating' &&
    job.config.preview_every > 0 &&
    frames > 1;
  const previewFrames =
    job.config.sample_frames == null
      ? frames
      : Math.max(5, 4 * Math.round(((frames * 24) / 16 - 1) / 4) + 1);
  const [durationDraft, setDurationDraft] = useState<{
    jobId: string;
    frames: number;
    text: string;
  }>();
  const text =
    durationDraft?.jobId === job.id && durationDraft.frames === frames
      ? durationDraft.text
      : String(Number((frames / fps).toFixed(6)));
  return (
    <fieldset>
      <legend>Sample and preview resolution &amp; duration</legend>
      <p>
        Previews and the final sample share these settings. Set their resolution
        here independently of dataset training resolutions. Dimensions left at
        the default follow the default training resolution, never a dataset's
        custom resolution.
      </p>
      {nativeWanPreview && (
        <p>
          Native preview: {width} × {height} · {previewFrames} frames · 24 fps ·{' '}
          {Number((previewFrames / 24).toFixed(6))} seconds. The pinned trainer
          uses 24 fps; explicit durations round to its supported frame grid.
        </p>
      )}
      <div className="number-row">
        {(['width', 'height'] as const).map((dimension) => {
          const key = `sample_${dimension}` as const;
          return (
            <div className="field number-field" key={dimension}>
              <label htmlFor={fieldId(key)}>Sample {dimension}</label>
              <input
                id={fieldId(key)}
                type="number"
                step="1"
                disabled={busy}
                value={dimension === 'width' ? width : height}
                {...fields.problemProps(key)}
                onChange={(event) => config(key, Number(event.target.value))}
              />
              <small>
                {job.config[key] == null
                  ? `Using default training ${dimension}`
                  : `Custom sample and preview ${dimension}`}
              </small>
              {fields.problemText(key)}
              <button
                type="button"
                disabled={busy || job.config[key] == null}
                onClick={() => config(key, null)}
              >
                Use default training {dimension}
              </button>
            </div>
          );
        })}
      </div>
      {job.family === 'wan' && (
        <label className="field">
          <input
            type="checkbox"
            disabled={busy}
            checked={frames === 1}
            onChange={(event) =>
              config('sample_frames', event.target.checked ? 1 : 17)
            }
          />
          Image sample (one frame)
        </label>
      )}
      {(job.family !== 'wan' || frames !== 1) && (
        <div className="field number-field">
          <label htmlFor={fieldId('sample_frames')}>
            Sample duration (seconds)
          </label>
          <input
            id={fieldId('sample_frames')}
            type="number"
            step="any"
            disabled={busy}
            value={text}
            {...fields.problemProps('sample_frames')}
            onChange={(event) => {
              const seconds = Number(event.target.value);
              const interval = job.family === 'wan' ? 4 : 17;
              const offset = job.family === 'wan' ? 1 : 5;
              const next =
                seconds > 0
                  ? Math.round((seconds * fps - offset) / interval) * interval +
                    offset
                  : 0;
              setDurationDraft({
                jobId: job.id,
                frames: next,
                text: event.target.value,
              });
              config('sample_frames', next);
            }}
          />
          {fields.problemText('sample_frames')}
          <small>
            Rounded to the nearest supported frame count at {fps} fps.{' '}
            {job.family === 'wan'
              ? '4*n+1 frames.'
              : '17*n+5 frames, from 124 to 345.'}
          </small>
        </div>
      )}
      <button
        type="button"
        disabled={busy || job.config.sample_frames == null}
        onClick={() => {
          setDurationDraft(undefined);
          config('sample_frames', null);
        }}
      >
        Use training frame count
      </button>
      <p aria-live="polite">
        Effective sample and preview resolution: {width} × {height} · {frames}{' '}
        frames ·{' '}
        {frames === 1
          ? 'image sample'
          : `${fps} fps · ${Number((frames / fps).toFixed(6))} seconds`}
        .
      </p>
    </fieldset>
  );
}
