# 0022: The viewer is tested in a browser, in CI

Status: Accepted · Date: 2026-11-13 · Milestone: M4.0

## Context
After ADR-0018 and ADR-0021 every pure module of the viewer has unit tests, but the panels and `main.ts` have none: they need a DOM, WebGL, a `<video>` element and the real samples. They were checked only by hand, by replaying the samples in a headless browser outside CI, so a change that broke the event list, the frames or the robot's view would pass CI. These are the parts people see. Writing them against a fake DOM would test the fake, not the page.

## Decision
- **Playwright with Chromium.** `@playwright/test` 1.64.1 is an exact dev dependency. `npm run e2e` builds the site, serves it with `vite preview` as Netlify serves it, and runs `e2e/replay.spec.ts`. WebGL comes from SwiftShader, so no GPU is needed.
- **What is checked.** Every sample in `public/samples/` is loaded from the menu, scrubbed, played and paused: the episode card, the event list (events already happened, the current one), the thumbnails and the large frame follow the replay's time, and the page logs no error and no failed request. Separate tests check that the robot's view follows the replay and is offered only when the episode has a video, that clicking an event jumps to its time, that an episode from another site is labelled with its host, and that a link that is not http or https is refused.
- **In CI.** The viewer job installs Chromium and runs `npm run e2e` after the build; a failing test is retried once in CI only.

## Consequences
- **The panels are covered.** Breaking the large frame or the current event on purpose fails the tests. Adding a sample means adding its row (events, frames, video) to the test's table.
- **Slower CI.** The viewer job grows by about two minutes: installing Chromium and a minute of tests. Its time limit goes from 10 to 15 minutes.
- **Not covered.** What the 3D view draws is not compared with a reference image: pixels from software rendering differ across machines. The tests check what the page says, not how the robot looks.
