import { useEffect, useRef } from 'react';
import { focusField, type JobFields } from './jobFields';
import type { Job, Review } from './jobTypes';
import { plural } from './labels';

function rateLabel(fps: number | null) {
  return fps === null ? 'unknown fps' : `${Number(fps.toPrecision(6))} fps`;
}
function sectionType(section: Review['sections'][number]) {
  if (section.kind === 'image') return 'Images';
  return section.fps === null ? 'Videos' : `Videos, ${rateLabel(section.fps)}`;
}
function sizeLabel(bytes: number) {
  return bytes >= 100_000_000
    ? `${(bytes / 1_000_000_000).toFixed(1)} GB`
    : `${(bytes / 1_000_000).toFixed(0)} MB`;
}

function diskVerdict(disk: Review['disk']) {
  const short = disk.volumes.filter((volume) => volume.shortfall > 0);
  if (short.length)
    return `Disk: ${short
      .map(
        (volume) => `${sizeLabel(volume.shortfall)} short on ${volume.volume}`,
      )
      .join(', ')}`;
  return disk.volumes.some((volume) => volume.free === null)
    ? 'Disk: free space unknown'
    : 'Disk: enough free space';
}

export function JobReview({
  job,
  review,
  reviewField,
}: {
  job: Job;
  review: Review;
  reviewField: JobFields['reviewField'];
}) {
  const reviewPanel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (review) reviewPanel.current?.scrollIntoView({ block: 'start' });
  }, [review]);
  return (
    <section
      ref={reviewPanel}
      id="job-section-review"
      tabIndex={-1}
      className="panel job-review"
      aria-label="Configuration review"
    >
      <h2>Configuration review</h2>
      <div className={`review-verdict ${review.valid ? 'passed' : 'failed'}`}>
        <strong>
          {review.valid
            ? 'Review passed. Ready for preparation.'
            : `Review failed: resolve ${plural(review.errors.length, 'error')} before preparation.`}
        </strong>
        <span>
          {plural(review.effective_count, 'included item')} ·{' '}
          {diskVerdict(review.disk)}
        </span>
      </div>
      {review.errors.length > 0 && (
        <ul className="error">
          {review.errors.map((message, index) => {
            const key = reviewField(message);
            return (
              <li key={index}>
                {key ? (
                  <button
                    className="error-link"
                    onClick={() => focusField(key)}
                  >
                    {message}
                  </button>
                ) : (
                  message
                )}
              </li>
            );
          })}
        </ul>
      )}
      <ul>
        {review.warnings.map((message, index) => (
          <li key={index}>{message}</li>
        ))}
      </ul>
      <details data-testid="disk-estimate">
        <summary>Estimated disk use</summary>
        <ul>
          <li>
            Media snapshot: {sizeLabel(review.disk.estimate.media_snapshot)}
          </li>
          <li>Caches: {sizeLabel(review.disk.estimate.caches)}</li>
          <li>
            LoRA checkpoints: {sizeLabel(review.disk.estimate.checkpoints)} (
            {review.disk.estimate.saves} saves)
          </li>
          <li>Optimizer states: {sizeLabel(review.disk.estimate.states)}</li>
          <li>Samples: {sizeLabel(review.disk.estimate.samples)}</li>
        </ul>
        <ul>
          {review.disk.volumes.map((volume) => (
            <li key={volume.volume}>
              {volume.volume} ({volume.roots.join(', ')}): needs about{' '}
              {sizeLabel(volume.needed + volume.margin)} including a{' '}
              {sizeLabel(volume.margin)} margin,{' '}
              {volume.free === null
                ? 'free space unknown'
                : `${sizeLabel(volume.free)} free`}
              {volume.shortfall > 0 && `, ${sizeLabel(volume.shortfall)} short`}
            </li>
          ))}
        </ul>
      </details>
      {review.sections.length > 0 && (
        <div className="job-balance">
          <table aria-label="Training sections">
            <caption>Training sections</caption>
            <thead>
              <tr>
                <th scope="col">Dataset</th>
                <th scope="col">Type</th>
                <th scope="col">Items</th>
                <th scope="col">Repeats</th>
                <th scope="col">Samples per epoch</th>
                <th scope="col">Share of training</th>
              </tr>
            </thead>
            <tbody>
              {review.sections.map((section, index) => (
                <tr key={index}>
                  <th scope="row">{section.dataset}</th>
                  <td>{sectionType(section)}</td>
                  <td>{section.items}</td>
                  <td>{section.repeats}</td>
                  <td>{section.samples}</td>
                  <td>{(section.share * 100).toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {review.video_windows.items.length > 0 && (
        <div data-testid="video-windows">
          <h3>Cached video windows</h3>
          <p>
            {review.video_windows.total} cached video windows from{' '}
            {review.video_windows.items.length} videos (
            {job.config.frame_extraction}).
          </p>
          <ul>
            {review.video_windows.items.map((item) => (
              <li key={item.id}>
                {item.name}: {rateLabel(item.source_fps)},{' '}
                {item.source_frames ?? '?'}
                {item.resampled_frames !== item.source_frames &&
                  ` → ${item.resampled_frames ?? '?'}`}{' '}
                frames · {item.windows ?? '?'} windows
              </li>
            ))}
          </ul>
        </div>
      )}
      {job.family === 'h3' && (
        <>
          <h3>Audio coverage</h3>
          <p>
            {review.audio_coverage.supervised} supervised /{' '}
            {review.audio_coverage.included} included;{' '}
            {review.audio_coverage.missing} without audio.
          </p>
          <details>
            <summary>Per-item audio diagnostics</summary>
            <pre>{JSON.stringify(review.audio_coverage.items, null, 2)}</pre>
          </details>
        </>
      )}
      <details>
        <summary>Advanced</summary>
        <h3>Resolved paths</h3>
        <pre>
          {JSON.stringify({ ...review.paths, models: review.models }, null, 2)}
        </pre>
        {Object.keys(review.model_provenance).length > 0 && (
          <details>
            <summary>Model precision and conversion provenance</summary>
            <pre>{JSON.stringify(review.model_provenance, null, 2)}</pre>
          </details>
        )}
        <h3>Native dataset TOML</h3>
        <pre data-testid="dataset-toml">{review.dataset_toml}</pre>
        <p>
          These paths are a preview. Preparing creates the Run's own folder and
          records the exact arguments it runs with.
        </p>
        {review.stages.map((stage) => (
          <details key={stage.name}>
            <summary>{stage.name} arguments</summary>
            <pre>{JSON.stringify(stage.args, null, 2)}</pre>
          </details>
        ))}
      </details>
    </section>
  );
}
