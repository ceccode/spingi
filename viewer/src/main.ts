import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { hudText } from "./describe";
import {
  DEFAULT_ROBOT_MODEL, loadEpisodeFromZip, MAX_ZIP_BYTES, readCapped, robotJointsUrl, robotModelUrl, safeEpisodeUrl,
  type Episode,
} from "./episode";
import { EventList, FrameStrip, HeadCamera, showManifest } from "./panels";
import { Player } from "./player";
import { episodeDuration, stateAt } from "./timeline";
import { World } from "./world";

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

const canvas = $<HTMLCanvasElement>("canvas");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14161c);
const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 200);
camera.position.set(-4, 3, 5);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xdfe4ff, 0x30333c, 0.9));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(5, 10, 4);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = sun.shadow.camera.bottom = -12;
sun.shadow.camera.right = sun.shadow.camera.top = 12;
scene.add(sun);

const ui = {
  title: $("title"), drop: $("drop"), hud: $("hud"), manifest: $("manifest"),
  play: $<HTMLButtonElement>("play"), scrub: $<HTMLInputElement>("scrub"), clock: $("clock"),
  speed: $<HTMLSelectElement>("speed"), follow: $<HTMLInputElement>("follow"),
  file: $<HTMLInputElement>("file"), sample: $<HTMLSelectElement>("sample"),
};

const world = new World(scene);
const player = new Player();
const seek = (t: number): void => player.seek(t);
const eventList = new EventList($("events"), seek);
const frameStrip = new FrameStrip(
  { card: $("framesCard"), strip: $("frames"), large: $<HTMLImageElement>("frameLarge"), caption: $("frameCaption") }, seek,
);
const headCamera = new HeadCamera(
  { box: $("fpv"), video: $<HTMLVideoElement>("fpvVideo"), label: $("fpvLabel"), toggle: $<HTMLInputElement>("robotView") }, player,
);
let episode: Episode | null = null;

function resize(): void {
  const { clientWidth: w, clientHeight: h } = canvas.parentElement!;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

const followed = new THREE.Vector3(); // the point the camera looked at last
const robotNow = new THREE.Vector3();

/** Moves the camera with the robot, keeping the angle and the distance the user chose. */
function followRobot(): void {
  world.robotWorldPosition(robotNow).y += 0.8; // look at the robot's middle, not at its feet
  camera.position.add(robotNow.clone().sub(followed));
  controls.target.copy(robotNow);
  followed.copy(robotNow);
}

/** A new episode's first view: looking down at the robot, over containers and racks. */
function frameRobot(): void {
  world.robotWorldPosition(followed);
  controls.target.copy(followed);
  camera.position.set(followed.x - 3.5, followed.y + 6, followed.z + 4);
}

let lastWall = performance.now();
function loop(now: number): void {
  const dt = Math.min(0.1, (now - lastWall) / 1000);
  lastWall = now;
  player.advance(dt);
  if (episode && ui.follow.checked) followRobot();
  controls.update();
  world.faceCamera(camera);
  renderer.render(scene, camera);
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

player.onTick((t) => {
  if (!episode) return;
  try {
    render(episode, t);
  } catch (err) {  // a bad episode must stop the replay with a message, not freeze the animation loop
    player.playing = false;
    renderTransport(t);
    ui.title.textContent = `error while replaying: ${(err as Error).message}`;
  }
});

function render(episode: Episode, t: number): void {
  const state = stateAt(episode.trajectory, t);
  world.setRobotPose(state.pose);
  world.setEstimate(state.estimate, state.pose);
  world.setJoints(state.joints);
  world.setObjectPositions(state.objects);
  headCamera.sync();
  renderTransport(t);
  ui.hud.textContent = hudText(t, state.pose, state.estimate);
  eventList.render(t);
  frameStrip.render(t);
}

/** The play button, the scrub bar and the clock. */
function renderTransport(t: number): void {
  ui.scrub.value = String(t / player.duration);
  ui.clock.textContent = `${t.toFixed(1)} s`;
  ui.play.textContent = player.playing ? "❚❚" : "▶";
}

let robotModel = "";  // the GLB currently loaded, so that episodes of the same robot do not reload it

/** Draws the robot the episode was recorded on (manifest.robot.model); a failed load keeps the previous model. */
async function showRobot(url: string): Promise<void> {
  if (url === robotModel) return;
  await world.loadRobot(url, robotJointsUrl(url));
  robotModel = url;
}

async function show(ep: Episode): Promise<void> {
  episode = ep;
  world.build(ep.scene, ep.terrain);
  const m = ep.manifest;
  ui.title.textContent = `${m.plan_id} · ${m.run_id}`;
  try {
    await showRobot(robotModelUrl(m.robot?.model));
  } catch (err) {
    ui.title.textContent += ` · robot model not loaded: ${(err as Error).message}`;
  }
  ui.drop.classList.add("hidden");
  headCamera.show(ep);
  showManifest(ui.manifest, ep);
  eventList.show(ep);
  frameStrip.show(ep);
  ui.play.disabled = ui.scrub.disabled = false;
  player.reset(episodeDuration(ep));
  frameRobot();
}

async function loadZip(buf: ArrayBuffer): Promise<void> {
  try {
    await show(loadEpisodeFromZip(buf));
  } catch (err) {
    ui.title.textContent = `error: ${(err as Error).message}`;
  }
}

/** The episode named by `?url=`, if any; `#url=` also works in development (Vite rejects queries that look like paths). */
async function loadFromLink(): Promise<void> {
  const url = new URLSearchParams(location.search).get("url") ?? new URLSearchParams(location.hash.slice(1)).get("url");
  if (!url) return;
  const target = safeEpisodeUrl(url, location.href);
  if (!target) {
    ui.title.textContent = "error: the episode URL must be http or https";
    return;
  }
  try {
    const res = await fetch(target);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const declared = Number(res.headers.get("content-length") ?? 0);
    if (declared > MAX_ZIP_BYTES) throw new Error("episode archive too large");
    await loadZip(await readCapped(res, MAX_ZIP_BYTES));
    const source = new URL(target);
    if (source.origin !== location.origin && episode) {
      ui.title.textContent += ` · loaded from ${source.host}`;  // never let a shared link pass as ours
    }
  } catch (err) {
    ui.title.textContent = `error: cannot load ${target}: ${(err as Error).message}`;
  }
}

ui.file.addEventListener("change", async () => {
  const f = ui.file.files?.[0];
  if (f) await loadZip(await f.arrayBuffer());
});
ui.sample.addEventListener("change", async () => {
  const name = ui.sample.value;
  if (!name) return;
  const res = await fetch(`/samples/${name}.zip`);
  await loadZip(await res.arrayBuffer());
  ui.sample.value = "";
});
ui.play.addEventListener("click", () => player.toggle());
ui.scrub.addEventListener("input", () => player.seek(Number(ui.scrub.value) * player.duration));
ui.speed.addEventListener("change", () => (player.speed = Number(ui.speed.value)));
addEventListener("keydown", (e) => {
  if (e.code === "Space" && episode) { e.preventDefault(); player.toggle(); }
  if (e.code === "ArrowRight") player.seek(player.t + 1);
  if (e.code === "ArrowLeft") player.seek(player.t - 1);
});

const stage = canvas.parentElement!;
for (const ev of ["dragenter", "dragover"]) {
  stage.addEventListener(ev, (e) => {
    e.preventDefault();
    ui.drop.classList.remove("hidden");
    ui.drop.classList.add("active");
  });
}
stage.addEventListener("dragleave", () => ui.drop.classList.remove("active"));
stage.addEventListener("drop", async (e) => {
  e.preventDefault();
  ui.drop.classList.remove("active");
  const f = (e as DragEvent).dataTransfer?.files?.[0];
  if (f) await loadZip(await f.arrayBuffer());
  else if (episode) ui.drop.classList.add("hidden");
});

(async () => {
  try {
    await showRobot(DEFAULT_ROBOT_MODEL);
  } catch (err) {
    ui.title.textContent = `robot model not loaded: ${(err as Error).message}`;
  }
  await loadFromLink();
})();
