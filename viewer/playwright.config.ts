import { defineConfig, devices } from "@playwright/test";

/** The viewer in a real browser: the built site, served as Netlify serves it (`vite preview`), replaying the samples
 *  in `public/samples/`. WebGL comes from SwiftShader, so no GPU is needed (CI). */
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:4180",
    viewport: { width: 1400, height: 900 },
    launchOptions: {
      args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"],
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 900 } } }],
  webServer: {
    command: "npm run build && npx vite preview --port 4180 --strictPort",
    url: "http://localhost:4180",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
