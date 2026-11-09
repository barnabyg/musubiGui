import { defineConfig } from 'vite';
export default defineConfig({
  build: {
    outDir: 'musubi_gui/web',
    emptyOutDir: true,
    rolldownOptions: {
      onLog(level, log, forward) {
        // This bundle runs entirely in the browser. Revisit if adding SSR or upgrading Lucide.
        if (
          log.code === 'MODULE_LEVEL_DIRECTIVE' &&
          log.message.includes('directive "use client"') &&
          /\/node_modules\/lucide-react\/dist\/esm\/(?:context|Icon)\.mjs$/.test(
            log.id ?? '',
          )
        )
          return;
        forward(level, log);
      },
    },
  },
});
