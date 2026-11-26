import {defineConfig} from '@playwright/test';
import {join} from 'node:path';
import {browserConfig} from './playwright.config';

const storage = process.env.TRADEFORGE_PAIR_STORAGE;
if (!storage) throw new Error('Run scripts.verify_t10_crt_browser to prepare isolated data');
const config = browserConfig('research_backend.tests.recorded_browser_app:app', storage);

export default defineConfig({
  ...config,
  testDir: './comparison-e2e',
  outputDir: join(storage, 'browser-results'),
  testMatch: '**/t10-crt.spec.ts',
});
