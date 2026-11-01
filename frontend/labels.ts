// One source for model component names, so Settings and Jobs can't describe them differently.
export const componentLabels: Record<string, string> = {
  wan_low: 'Wan low-noise DiT',
  wan_high: 'Wan high-noise DiT',
  wan_vae: 'Wan 2.1 VAE',
  wan_t5: 'Wan T5 encoder',
  h3_dit: 'H3 DiT',
  h3_video_vae: 'H3 video VAE',
  h3_audio_vae: 'H3 audio VAE',
  h3_text: 'H3 Qwen text encoder',
};

export function plural(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

// What a dataset holds, worked out from its files: there is no stored image or video type.
export function contentsLabel(counts: { images: number; videos: number }) {
  const parts = [
    counts.images ? plural(counts.images, 'image') : '',
    counts.videos ? plural(counts.videos, 'video') : '',
  ].filter(Boolean);
  return parts.join(' · ') || 'No included media';
}
