import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  describeEvent, episodeDuration, eventTime, loadEpisodeFromZip, manifestRows, MAX_ZIP_BYTES, objectsAt, poseAt,
  safeEpisodeUrl, type Episode,
} from "./episode";
import { Player } from "./player";
import { World } from "./world";

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

/** Creates an element whose text is set with textContent: never parsed as HTML. */
function el(tag: string, text?: string, className?: string): HTMLElement {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
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

const world = new World(scene);
const player = new Player();
let episode: Episode | null = null;
let eventTimes: number[] = [];
let frameTimes: { id: string; t: number }[] = [];
let frameUrls: string[] = [];

const ui = {
  title: $("title"), drop: $("drop"), hud: $("hud"), manifest: $("manifest"), events: $<HTMLOListElement>("events"),
  framesCard: $("framesCard"), frames: $("frames"), frameLarge: $<HTMLImageElement>("frameLarge"), frameCaption: $("frameCaption"), play: $<HTMLButtonElement>("play"), scrub: $<HTMLInputElement>("scrub"),
  clock: $("clock"), speed: $<HTMLSelectElement>("speed"), follow: $<HTMLInputElement>("follow"),
  file: $<HTMLInputElement>("file"), sample: $<HTMLSelectElement>("sample"),
};

function resize(): void {
  const { clientWidth: w, clientHeight: h } = canvas.parentElement!;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

const robotPos = new THREE.Vector3();
const lastTarget = new THREE.Vector3();
let lastWall = performance.now();
function loop(now: number): void {
  const dt = Math.min(0.1, (now - lastWall) / 1000);
  lastWall = now;
  player.advance(dt);
  if (episode && ui.follow.checked) {
    world.robotWorldPosition(robotPos);
    robotPos.y += 0.8;
    const delta = robotPos.clone().sub(lastTarget);
    controls.target.copy(robotPos);
    camera.position.add(delta);
    lastTarget.copy(robotPos);
  }
  controls.update();
  world.faceCamera(camera);
  renderer.render(scene, camera);
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

player.onTick((t) => {
  if (!episode) return;
  const pose = poseAt(episode.trajectory, t);
  world.setRobotPose(pose);
  world.setObjectPositions(objectsAt(episode.trajectory, t));
  ui.scrub.value = String(t / player.duration);
  ui.clock.textContent = `${t.toFixed(1)} s`;
  ui.play.textContent = player.playing ? "❚❚" : "▶";
  ui.hud.textContent = `t ${t.toFixed(2)} s\nx ${pose.x.toFixed(2)}  y ${pose.y.toFixed(2)}  yaw ${pose.yaw.toFixed(2)}\n${pose.mode}`;
  let current = -1;
  const items = ui.events.children;
  for (let i = 0; i < items.length; i++) {
    const past = eventTimes[i]! <= t;
    items[i]!.classList.toggle("past", past);
    if (past) current = i;
  }
  for (let i = 0; i < items.length; i++) items[i]!.classList.toggle("current", i === current);
  if (current >= 0) (items[current] as HTMLElement).scrollIntoView({ block: "nearest" });
  const imgs = ui.frames.children;
  let latest = -1;
  for (let i = 0; i < imgs.length; i++) {
    const visible = (frameTimes[i]?.t ?? Infinity) <= t;
    imgs[i]!.classList.toggle("visible", visible);
    if (visible) latest = i;
  }
  if (latest >= 0) {
    const src = (imgs[latest] as HTMLImageElement).src;
    if (ui.frameLarge.src !== src) ui.frameLarge.src = src;
    ui.frameLarge.hidden = false;
    ui.frameCaption.textContent = `${frameTimes[latest]!.id} · ${frameTimes[latest]!.t.toFixed(1)} s`;
  } else {
    ui.frameLarge.hidden = true;
    ui.frameCaption.textContent = "no frame yet";
  }
});

async function show(ep: Episode): Promise<void> {
  episode = ep;
  world.build(ep.scene);
  const m = ep.manifest;
  ui.title.textContent = `${m.plan_id} · ${m.run_id}`;
  ui.drop.classList.add("hidden");
  // Episode content is untrusted (a shared zip, a ?url= link): it only ever reaches the page as text.
  const dl = el("dl");
  for (const [label, value] of manifestRows(ep)) dl.append(el("dt", label), el("dd", value));
  ui.manifest.replaceChildren(el("h3", "Episode"), dl);

  eventTimes = ep.events.map((e) => eventTime(e, ep.events));
  ui.events.replaceChildren();
  ep.events.forEach((e, i) => {
    const li = document.createElement("li");
    if (e.kind.startsWith("safety")) li.classList.add("safety");
    if (e.kind.startsWith("human") || e.kind === "operator.stop") li.classList.add("human");
    const body = el("span");
    body.append(el("b", e.kind), ` ${describeEvent(e)}`);
    li.append(el("span", `${eventTimes[i]!.toFixed(1)}s`, "t"), body);
    li.addEventListener("click", () => player.seek(eventTimes[i]!));
    ui.events.appendChild(li);
  });

  for (const u of frameUrls) URL.revokeObjectURL(u);  // release the previous episode's images
  frameUrls = [];
  ui.frameLarge.removeAttribute("src");
  ui.frames.replaceChildren();
  frameTimes = [];
  for (const e of ep.events) {
    const id = (e as Record<string, unknown>).frame_id as string | undefined;
    if (id && ep.frames.has(id)) frameTimes.push({ id, t: eventTime(e, ep.events) });
  }
  if (frameTimes.length === 0) for (const id of ep.frames.keys()) frameTimes.push({ id, t: 0 });
  for (const f of frameTimes) {
    const img = document.createElement("img");
    img.src = URL.createObjectURL(ep.frames.get(f.id)!);
    frameUrls.push(img.src);
    img.title = `${f.id} · ${f.t.toFixed(1)} s`;
    img.addEventListener("click", () => player.seek(f.t));
    ui.frames.appendChild(img);
  }
  ui.framesCard.hidden = frameTimes.length === 0;

  ui.play.disabled = ui.scrub.disabled = false;
  player.reset(episodeDuration(ep));
  const start = poseAt(ep.trajectory, 0);
  world.setRobotPose(start);
  world.robotWorldPosition(lastTarget);
  controls.target.copy(lastTarget);
  camera.position.set(lastTarget.x - 3.5, lastTarget.y + 2.5, lastTarget.z + 4);
}

async function loadZip(buf: ArrayBuffer): Promise<void> {
  try {
    await show(loadEpisodeFromZip(buf));
  } catch (err) {
    ui.title.textContent = `error: ${(err as Error).message}`;
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
for (const ev of ["dragenter", "dragover"]) canvas.parentElement!.addEventListener(ev, (e) => { e.preventDefault(); ui.drop.classList.remove("hidden"); ui.drop.classList.add("active"); });
canvas.parentElement!.addEventListener("dragleave", () => ui.drop.classList.remove("active"));
canvas.parentElement!.addEventListener("drop", async (e) => {
  e.preventDefault();
  ui.drop.classList.remove("active");
  const f = (e as DragEvent).dataTransfer?.files?.[0];
  if (f) await loadZip(await f.arrayBuffer());
  else if (episode) ui.drop.classList.add("hidden");
});

(async () => {
  try {
    await world.loadRobot("/models/g1.glb");
  } catch (err) {
    ui.title.textContent = `G1 model not loaded: ${(err as Error).message}`;
  }
  // `?url=` in production; `#url=` also works in development (Vite rejects queries that look like paths).
  const url = new URLSearchParams(location.search).get("url") ?? new URLSearchParams(location.hash.slice(1)).get("url");
  if (url) {
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
      await loadZip(await res.arrayBuffer());
    } catch (err) {
      ui.title.textContent = `error: cannot load ${target}: ${(err as Error).message}`;
    }
  }
})();
