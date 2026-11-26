import {defineConfig} from '@playwright/test';
import config from './playwright.comparison.config';

export default defineConfig({...config, testMatch: '**/t10-replay-common-timing.spec.ts'});
