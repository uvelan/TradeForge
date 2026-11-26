import {defineConfig} from '@playwright/test';
import {join} from 'node:path';
import {browserConfig} from './playwright.config';

const storage = process.env.TRADEFORGE_PAIR_STORAGE;
const output = process.env.TRADEFORGE_T10_CONFLUENCE_OUTPUT;
const phase = process.env.TRADEFORGE_T10_CONFLUENCE_PHASE;
if (!storage || !output || !phase) throw new Error('Run scripts.verify_t10_confluence_browser to prepare isolated data');

export default defineConfig({
  ...browserConfig('research_backend.tests.recorded_browser_app:app', storage),
  testDir: './comparison-e2e',
  outputDir: join(output, 'test-results-' + phase),
  testMatch: '**/t10-confluence.spec.ts',
});
