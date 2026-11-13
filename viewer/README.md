# Spingi Viewer

Web replayer for episodes produced by the Spingi runtime (format in [../docs/episode-format.md](../docs/episode-format.md)). Load an episode `.zip` and watch the robot and the objects move in the 3D scene, with the event timeline, the head-camera frames, safety and operator events. When the robot walked in physics its legs move, its body rises and tilts, and outdoor episodes show their ground (asphalt, gravel, dirt, grass, kerbs, ramps). When its odometry drifted, an orange outline shows where it believed it was, tied to where it was by a dashed line, and the scene's AprilTag landmarks stand on their posts and walls. When the episode was recorded with `spingi run --camera-video`, the robot's view plays in the bottom right corner: what the head camera saw, continuously, in step with the replay, at any speed and wherever you jump (ADR-0017). A static site: no server, no physics in the browser.

Live at **https://spingi-viewer.netlify.app**. Episodes are read in the browser; nothing is uploaded.

## Run it

```bash
npm install
```

```bash
npm run dev
```

Open http://localhost:5173. Three ways to load an episode:

- drop a `.zip` produced by `spingi run --zip` onto the page, or use **Load .zip**;
- pick a bundled sample from the **Sample episodes** menu (inspection round on both robots, warehouse material runner, physics runs: the Go2's yard patrol outdoors with realistic sensors, the G1 on the test track until it falls, the G1's inspection round; the physics runs carry the head camera's video);
- open `?url=https://…/episode.zip` to load a published episode (during development use `#url=…`, because the Vite dev server rejects queries that look like file paths). Any https URL is accepted; http only from the viewer's own origin. An episode loaded from another host shows "loaded from <host>" next to its title, so a shared link cannot pass as one of ours.

Episodes may come from strangers, so the viewer treats them as data: archives over 200 MB, or expanding beyond 500 MB (a zip bomb), are refused, and a download is stopped as soon as it passes the limit even when the server does not declare its size; a trajectory with a missing or non-finite value, or more than 64 joints, is refused at load time; at most 256 landmarks are drawn, and a malformed one is skipped; frames are shown only when they are PNG or JPEG; the head camera's video is played only as the MP4 the manifest names, a plain file name, with its rate, frame count and size checked (a malformed entry refuses the episode); `terrain.json` is checked before it reaches the GPU (at most a million samples, sizes that match, colours in range, surface ids that exist); episode text reaches the page only as text, never through `innerHTML` (a test scans every source file); an error during replay stops the player with a message. The images of the previous episode are released when a new one is loaded.

Controls: Space plays and pauses, the arrow keys move one second, the speed menu goes from 0.5× to 4×, clicking an event or a frame jumps to that instant, "follow robot" keeps the camera on the robot, "robot's view" shows or hides the head camera's video (a click on it enlarges it), drag to orbit. The video is the clean render from where the camera really was; the frames under "Frames analysed" are the ones the robot took, with the camera noise when the run had it.

```bash
npm test
```

runs the unit tests (episode reading, joint angles and the base's height, the ground, the head camera's video and which frame to show when, the replay clock, loading limits and handling of untrusted episodes) with Vitest.

```bash
npm run e2e
```

builds the site, serves it as Netlify does and replays every sample in Chromium with Playwright (`e2e/`, ADR-0022): the panels follow the replay, the robot's view follows the video, clicking an event jumps to it, a link to another site says where the episode came from, and the page logs no error. WebGL runs in software, so no GPU is needed. Run `npx playwright install chromium` once first.

```bash
npm run lint
```

checks formatting, import order and lint rules with Biome (`biome.json`, ADR-0019); `npm run format` applies the fixes. CI runs the check.

```bash
npm run build
```

## How it is built

| File | Role |
|------|------|
| `src/episode.ts` | Reads the zip (fflate), YAML and JSONL and checks them: episodes come from strangers too. What the replay cannot do without (the archive's size, the format's version, the trajectory, the event log, the head camera's video entry) is refused with a message; a malformed place, obstacle, object or landmark of the scene is skipped. No rendering dependency: this is the reading SDK in embryo. |
| `src/timeline.ts` | The episode at one time of the replay (`stateAt`): the robot pose, the base's height and tilt and the joint angles interpolated, object positions merged over time; events and frames aligned with the trajectory. No rendering dependency. |
| `src/describe.ts` | The episode in words: the rows of its card, a line for each event, the text over the 3D view. Plain strings only. |
| `src/terrain.ts` | Reads and checks `terrain.json`; ground height at a point; the ground as vertices, colours and triangles. No rendering dependency. |
| `src/world.ts` | Three.js scene from `scene.yaml` and `terrain.json`: floor or ground, locations, obstacles (with their colours, standing on the ground), objects, the robot and its joints. MuJoCo coordinates (Z up) inside a rotated group. |
| `src/player.ts` | Replay clock: play, pause, speed, seek. |
| `src/video.ts` | Keeps the head camera's video on the replay's time, as a decision the page applies: it plays along at the replay's speed and is sought only when it drifts. |
| `src/panels.ts` | The panels around the 3D view: episode card, event list, frames, the robot's view. Each shows a new episode and follows the replay's time; episode content reaches the page only as text. |
| `src/main.ts` | Wiring: the renderer and the camera, loading (file, sample, `?url=`), the transport controls, and the replay loop that hands each time to the world and the panels. |
| `public/models/g1.glb`, `go2.glb` | The Unitree G1 and Go2, one node per body (1.4 MB and 0.7 MB), exported by `runtime/scripts/export_glb.py --robot g1|go2` and compressed with `npm run model:compress` (meshopt, no simplification, nodes kept). Next to each, `<robot>.joints.json` says which node each joint turns, about which axis, and the standing angles. The viewer picks the model from `manifest.robot.model` through a fixed table (`robotModelUrl`); an unknown name is drawn as the G1. Episodes without joint angles (kinematic runs) show the robot standing. |
| `public/samples/` | Episodes generated by the runtime. |
| `public/NOTICE.txt` | The repository's NOTICE and the Unitree BSD 3-Clause licenses that cover the models, served at `/NOTICE.txt`. |

## Deploy

`netlify.toml` at the repository root sets `viewer` as the base directory, `npm run build` as the command and `dist` as the publish folder, and the security headers of every page: a Content Security Policy (scripts and styles only from the site itself, `'wasm-unsafe-eval'` for the meshopt decoder, images and video also from the `blob:` URLs the episode's files are read into, `connect-src` open to https for `?url=`, no framing, no forms, no plugins), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin` and a `Permissions-Policy` that turns off camera, microphone, geolocation, payment and USB. The repository is connected to Netlify: every push to `main` deploys to https://spingi-viewer.netlify.app. CI ([../.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs `npm ci`, an audit of the production dependencies, the tests and the build on every push and pull request.

## Not yet

- Playing `run.mp4`: the video of an episode recorded with `--record` is ignored by the viewer (it still counts towards the size limits).
- Loading an unzipped folder.
- Comparing two episodes of the same plan.
