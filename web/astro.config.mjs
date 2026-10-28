import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';

// Served by GitHub Pages at https://michaeldhart.com/govt-budget-analyzer/ (the account's custom domain). For another host or
// path, set SITE_URL and BASE_PATH in .github/workflows/pages.yml; old links will break, which is accepted.
const site = process.env.SITE_URL || 'https://michaeldhart.com';
const base = process.env.BASE_PATH || '/govt-budget-analyzer';

export default defineConfig({
  site,
  base,
  trailingSlash: 'always',
  integrations: [svelte()],
});
