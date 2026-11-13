import { expect, type Page, test } from "@playwright/test";

/** Every sample of the viewer, replayed as a person would: chosen from the menu, scrubbed, played, paused. The panels
 *  must follow the replay's time and the page must log no error. */
const SAMPLES = [
  { name: "demo_inspection_round", events: 56, frames: 2, video: false },
  { name: "demo_inspection_round_go2", events: 56, frames: 2, video: false },
  { name: "demo_material_runner", events: 56, frames: 1, video: false },
  { name: "yard_patrol_go2_realistic", events: 118, frames: 27, video: true },
  { name: "test_track_g1_physics", events: 22, frames: 3, video: true },
  { name: "demo_inspection_round_g1_physics", events: 69, frames: 9, video: true },
];

/** Errors and failed requests of the page, collected for the whole test. */
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`exception: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  page.on("requestfailed", (r) => errors.push(`request failed: ${r.url()}`));
  return errors;
}

/** Chooses a sample and waits until it is shown. The menu goes back to its prompt only once the episode and its
 *  robot model are loaded: the play button is no signal, it stays enabled from the episode before. */
async function loadSample(page: Page, name: string): Promise<void> {
  await page.locator("#sample").selectOption(name);
  await expect(page.locator("#sample")).toHaveValue("", { timeout: 20_000 });
  await expect(page.locator("#play")).toBeEnabled();
  await expect(page.locator("#title")).not.toContainText("error");
}

/** The replay's time on the clock, in seconds. */
async function clock(page: Page): Promise<number> {
  return Number.parseFloat((await page.locator("#clock").textContent()) ?? "NaN");
}

/** Moves the scrub bar to a fraction of the episode, as dragging it does. */
async function scrubTo(page: Page, fraction: number): Promise<void> {
  await page.locator("#scrub").evaluate((el, f) => {
    const input = el as HTMLInputElement;
    input.value = String(f);
    input.dispatchEvent(new Event("input"));
  }, fraction);
}

for (const sample of SAMPLES) {
  test(`replays ${sample.name}`, async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto("/");
    await loadSample(page, sample.name);

    await expect(page.locator("#manifest dt")).toHaveCount(5);
    await expect(page.locator("#events > li")).toHaveCount(sample.events);
    await expect(page.locator("#frames > img")).toHaveCount(sample.frames);
    await expect(page.locator("#hud")).toContainText("t 0.00 s");
    await expect(page.locator("#robotView")).toBeEnabled({ enabled: sample.video });

    await scrubTo(page, 0.6);
    const scrubbed = await clock(page);
    expect(scrubbed).toBeGreaterThan(0);
    const past = await page.locator("#events > li.past").count();
    expect(past).toBeGreaterThan(0);
    expect(past).toBeLessThan(sample.events);
    await expect(page.locator("#events > li.current")).toHaveCount(1);

    await page.locator("#play").click();
    await expect(page.locator("#play")).toHaveText("❚❚");
    await expect.poll(() => clock(page)).toBeGreaterThan(scrubbed);
    await page.locator("#play").click();
    await expect(page.locator("#play")).toHaveText("▶");

    await scrubTo(page, 1);
    await expect(page.locator("#events > li.past")).toHaveCount(sample.events);
    await expect(page.locator("#frames > img.visible")).toHaveCount(sample.frames);
    // The large frame is the latest one the replay reached, the same image as its thumbnail.
    const latest = await page.locator("#frames > img").last().getAttribute("src");
    await expect(page.locator("#frameLarge")).toBeVisible();
    await expect(page.locator("#frameLarge")).toHaveAttribute("src", latest ?? "missing");
    await expect(page.locator("#frameCaption")).not.toHaveText("no frame yet");

    expect(errors).toEqual([]);
  });
}

test("the robot's view follows the replay, and a second episode replaces the first", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await loadSample(page, "yard_patrol_go2_realistic");
  await page.locator("#robotView").check();
  await expect(page.locator("#fpv")).toBeVisible();
  await expect(page.locator("#fpvLabel")).toHaveText("head camera · 10 fps");

  await scrubTo(page, 0.5);
  const at = await clock(page);
  const video = page.locator("#fpvVideo");
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(at, 0);

  await page.locator("#play").click();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  await page.locator("#play").click();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);

  await loadSample(page, "demo_inspection_round");
  await expect(page.locator("#fpv")).toBeHidden();
  await expect(page.locator("#robotView")).toBeDisabled();
  await expect(page.locator("#events > li")).toHaveCount(56);
  await expect(page.locator("#clock")).toHaveText("0.0 s");
  expect(errors).toEqual([]);
});

test("an episode chosen while the default robot is still loading shows its own robot", async ({ page }) => {
  let g1Loads = 0;
  await page.route("**/models/g1.glb", async (route) => {
    g1Loads += 1;
    if (g1Loads === 1) await new Promise((r) => setTimeout(r, 3000)); // the default model, loaded at startup, comes late
    await route.continue();
  });
  await page.goto("/");
  await loadSample(page, "demo_inspection_round_go2");
  await expect(page.locator("#canvas")).toHaveAttribute("data-robot", "/models/go2.glb");
  await page.waitForTimeout(4000); // past the default model's arrival
  await expect(page.locator("#canvas")).toHaveAttribute("data-robot", "/models/go2.glb");
});

test("clicking an event jumps to its time", async ({ page }) => {
  await page.goto("/");
  await loadSample(page, "demo_inspection_round");
  const last = page.locator("#events > li").last();
  const time = Number.parseFloat((await last.locator(".t").textContent()) ?? "NaN");
  await last.click();
  await expect.poll(() => clock(page)).toBeCloseTo(time, 0);
  await expect(last).toHaveClass(/current/);
});

test("a link to an episode on another site says where it was loaded from", async ({ page }) => {
  await page.route("https://episodes.example.org/**", (route) =>
    route.fulfill({ path: "public/samples/demo_material_runner.zip" }),
  );
  await page.goto("/?url=https://episodes.example.org/run.zip");
  await expect(page.locator("#title")).toContainText("loaded from episodes.example.org");
  await expect(page.locator("#events > li")).toHaveCount(56);
});

test("a link that is not http or https is refused", async ({ page }) => {
  await page.goto("/?url=javascript:alert(1)");
  await expect(page.locator("#title")).toHaveText("error: the episode URL must be http or https");
});
