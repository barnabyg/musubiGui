import { useState } from 'react';
import type { Output } from './Outputs';

// The output scanner uses Pillow and PyAV. A file it can decode may be unsupported in Edge.
const mediaTypes: Record<string, string> = {
  mp4: 'video/mp4',
  m4v: 'video/mp4',
  mov: 'video/quicktime',
  mkv: 'video/x-matroska',
  avi: 'video/x-msvideo',
  webm: 'video/webm',
  aac: 'audio/aac',
  flac: 'audio/flac',
  m4a: 'audio/mp4',
  mp3: 'audio/mpeg',
  ogg: 'audio/ogg',
  opus: 'audio/ogg; codecs=opus',
  wav: 'audio/wav',
};
const probe = document.createElement('video');

function supported(output: Output) {
  const ext = output.path.split('.').pop()?.toLowerCase() ?? '';
  if (output.kind === 'image')
    return ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif'].includes(ext);
  if (output.kind !== 'video' && output.kind !== 'audio') return false;
  return !!mediaTypes[ext] && !!probe.canPlayType(mediaTypes[ext]);
}

export function useOutputPreviews() {
  // An HTTP or decoder error applies to that version of the file; another sample may still play.
  const [failures, setFailures] = useState(
    new Map<string, number | undefined>(),
  );
  function playable(item: Output) {
    return (
      item.status === 'available' &&
      !!item.file_url &&
      supported(item) &&
      !(failures.has(item.id) && failures.get(item.id) === item.modified_ms)
    );
  }
  return {
    playable,
    failed(item: Output) {
      setFailures((old) => new Map(old).set(item.id, item.modified_ms));
    },
    retry() {
      setFailures(new Map());
    },
    latest(items: Output[]) {
      return items.reduce<Output | undefined>(
        (latest, item) =>
          playable(item) &&
          (!latest || (item.modified_ms ?? 0) >= (latest.modified_ms ?? 0))
            ? item
            : latest,
        undefined,
      );
    },
  };
}

export type OutputPreviews = ReturnType<typeof useOutputPreviews>;

export function MediaPreview({
  output,
  previews,
}: {
  output: Output;
  previews: OutputPreviews;
}) {
  if (output.status !== 'available' || !output.file_url) return null;
  if (!previews.playable(output))
    return <p>This browser cannot preview this sample.</p>;
  const failed = () => previews.failed(output);
  if (output.kind === 'image')
    return (
      <img
        className="output-media"
        onError={failed}
        src={output.file_url}
        alt={`Sample ${output.name}`}
      />
    );
  if (output.kind === 'video')
    return (
      <video
        className="output-media"
        controls
        preload="metadata"
        onError={failed}
        src={output.file_url}
        aria-label="Output video"
      />
    );
  if (output.kind === 'audio')
    return (
      <audio
        controls
        preload="metadata"
        onError={failed}
        src={output.file_url}
        aria-label="Output audio"
      />
    );
  return null;
}
