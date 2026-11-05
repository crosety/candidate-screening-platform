import type { Page, Locator } from '@playwright/test';

export function startBrowserVoice(page: Page): Promise<void>;
export function browserVoiceExit(page: Page): Locator;
