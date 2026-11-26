import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const storage = process.env.TRADEFORGE_PAIR_STORAGE;
if (!storage) throw new Error('Run scripts.verify_t10_chart --zones to prepare isolated data');
const config = browserConfig('research_backend.tests.comparison_browser_app:app', storage);
export default defineConfig({
  ...config,
  testDir: './comparison-e2e',
  testMatch: '**/t10-zones*.spec.ts',
  use: {...config.use, serviceWorkers: 'block'},
});
