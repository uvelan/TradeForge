import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const config=browserConfig('research_backend.tests.recorded_browser_app:app');
export default defineConfig({...config,testDir:'./recorded-e2e',outputDir:'./recorded-test-results',
  use:{...config.use,serviceWorkers:'block',trace:'off',screenshot:'off',video:'off'}});
