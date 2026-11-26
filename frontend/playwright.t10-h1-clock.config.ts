import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const storage = process.env.TRADEFORGE_H1_CLOCK_STORAGE;
if (!storage) throw new Error('Run scripts.verify_t10_h1_clock to prepare isolated data');
const config = browserConfig('research_backend.tests.recorded_browser_app:app', storage);
export default defineConfig({
  ...config,
  testDir: './comparison-e2e',
  testMatch: '**/t10-h1-clock.spec.ts',
  use: {...config.use, serviceWorkers: 'block'},
});
