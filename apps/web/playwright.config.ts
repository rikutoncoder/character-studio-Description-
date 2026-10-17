import { defineConfig, devices } from '@playwright/test';
const port = Number(process.env.CHARACTER_STUDIO_E2E_PORT ?? '3000');
export default defineConfig({
  testDir: './tests/e2e',
  use: { baseURL: `http://127.0.0.1:${port}` },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-chromium', use: { ...devices['Pixel 7'] } },
    { name: 'mobile-webkit', use: { ...devices['iPhone 13'] } },
  ],
  webServer: { command: `./node_modules/.bin/next dev --hostname 127.0.0.1 --port ${port}`, url: `http://127.0.0.1:${port}`, reuseExistingServer: !process.env.CI, timeout: 120000 },
});
