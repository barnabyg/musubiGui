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
