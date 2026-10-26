import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';

// Hosted on GitHub Pages under /govt-budget-analyzer until the custom domain exists. When it does, build with
// SITE_URL=https://example.org BASE_PATH=/ (set in .github/workflows/pages.yml); old links will break, which is accepted.
const site = process.env.SITE_URL || 'https://michaeldhart.github.io';
const base = process.env.BASE_PATH || '/govt-budget-analyzer';

export default defineConfig({
  site,
  base,
  trailingSlash: 'always',
  integrations: [svelte()],
});
