import {defineConfig} from '@playwright/test';
import nativeReplay from './playwright.native-capacity.config';

export default defineConfig({...nativeReplay,testDir:'./drawing-capacity'});
