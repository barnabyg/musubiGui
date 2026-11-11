import type { DatasetOption, Job } from './jobTypes';

// Unknown, missing or empty datasets still need review; never infer image-only from absence.
export function jobMedia(job: Job | null, datasets: DatasetOption[]) {
  const selected =
    job?.datasets.map((entry) =>
      datasets.find((dataset) => dataset.id === entry.dataset_id),
    ) ?? [];
  const images = selected.reduce(
    (sum, dataset) => sum + (dataset?.counts.images ?? 0),
    0,
  );
  const videos = selected.reduce(
    (sum, dataset) => sum + (dataset?.counts.videos ?? 0),
    0,
  );
  return {
    images,
    videos,
    imageOnly: selected.every(Boolean) && images > 0 && videos === 0,
  };
}

export type JobMedia = ReturnType<typeof jobMedia>;
