import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const storage=process.env.TRADEFORGE_NATIVE_STORAGE;
if(!storage)throw Error('Supply a disposable prepared native replay store');
const config=browserConfig('research_backend.tests.recorded_browser_app:app',storage);
export default defineConfig({...config,testDir:'./native-drawing-e2e',use:{...config.use,
  viewport:{width:1536,height:900}}});
