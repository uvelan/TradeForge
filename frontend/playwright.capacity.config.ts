import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const config=browserConfig('research_backend.tests.recorded_browser_app:app');
export default defineConfig({...config,testDir:'./capacity-e2e',outputDir:'./test-results/capacity',
  use:{...config.use,viewport:{width:1536,height:900},trace:'off',video:'off'}});
