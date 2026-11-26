import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const config = browserConfig('research_backend.tests.comparison_browser_app:app', process.env.TRADEFORGE_PAIR_STORAGE);
export default defineConfig({
  ...config,
  testDir: './comparison-e2e',
  testMatch: '**/t10-indicators-checkpoint.spec.ts',
  use: {...config.use, serviceWorkers: 'block'},
});
