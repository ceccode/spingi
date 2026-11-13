/** The panels around the 3D view. Each one shows a new episode and then follows the replay's time, keeping to
 *  itself what it needs for that. Episode content is untrusted (a shared zip, a ?url= link): it only ever reaches
 *  the page as text. No 3D here. */
import { describeEvent, eventTone, manifestRows } from "./describe";
import type { CameraVideo, Episode } from "./episode";
import { eventTimes, frameTimeline, reached, type TimedFrame } from "./timeline";
import { videoSync, type ReplayClock } from "./video";

/** Creates an element whose text is set with textContent: never parsed as HTML. */
function el(tag: string, text?: string, className?: string): HTMLElement {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

type Seek = (t: number) => void;

/** The episode card: outcome, robot, duration, date and plan. */
export function showManifest(card: HTMLElement, ep: Episode): void {
  const dl = el("dl");
  for (const [label, value] of manifestRows(ep)) dl.append(el("dt", label), el("dd", value));
  card.replaceChildren(el("h3", "Episode"), dl);
}

/** The event log: a click on an event goes to its time; the events already happened and the current one are marked. */
export class EventList {
  private times: number[] = [];
  private marked = -1; // how many events were marked as happened at the last render; -1: not rendered yet

  constructor(private readonly list: HTMLElement, private readonly seek: Seek) {}

  show(ep: Episode): void {
    this.times = eventTimes(ep.events);
    this.marked = -1;
    this.list.replaceChildren(...ep.events.map((e, i) => {
      const time = this.times[i]!;
      const li = el("li");
      const tone = eventTone(e.kind);
      if (tone) li.classList.add(tone);
      const body = el("span");
      body.append(el("b", e.kind), ` ${describeEvent(e)}`);
      li.append(el("span", `${time.toFixed(1)}s`, "t"), body);
      li.addEventListener("click", () => this.seek(time));
      return li;
    }));
  }

  render(t: number): void {
    const { count, last } = reached(this.times, t);
    const items = this.list.children;
    if (count !== this.marked) { // otherwise the same events as at the last render: their marks are already right
      this.marked = count;
      for (let i = 0; i < items.length; i++) {
        items[i]!.classList.toggle("past", this.times[i]! <= t);
        items[i]!.classList.toggle("current", i === last);
      }
    }
    // At every render, not only when the current event changes: the list is resized as the frames below it load.
    if (last >= 0) items[last]!.scrollIntoView({ block: "nearest" });
  }
}

/** The frames the robot took: thumbnails that appear as the replay reaches them, and the latest one large. */
export class FrameStrip {
  private frames: TimedFrame[] = [];
  private times: number[] = [];
  private urls: string[] = [];
  private visible = -1; // how many thumbnails were visible at the last render; -1: not rendered yet

  constructor(
    private readonly ui: { card: HTMLElement; strip: HTMLElement; large: HTMLImageElement; caption: HTMLElement },
    private readonly seek: Seek,
  ) {}

  show(ep: Episode): void {
    for (const url of this.urls) URL.revokeObjectURL(url); // release the previous episode's images
    this.frames = frameTimeline(ep);
    this.times = this.frames.map((f) => f.t);
    this.urls = this.frames.map((f) => URL.createObjectURL(ep.frames.get(f.id)!));
    this.visible = -1;
    this.ui.large.removeAttribute("src");
    this.ui.strip.replaceChildren(...this.frames.map((f, i) => {
      const img = document.createElement("img");
      img.src = this.urls[i]!;
      img.title = caption(f);
      img.addEventListener("click", () => this.seek(f.t));
      return img;
    }));
    this.ui.card.hidden = this.frames.length === 0;
  }

  render(t: number): void {
    const { count, last } = reached(this.times, t);
    if (count === this.visible) return;
    this.visible = count;
    const imgs = this.ui.strip.children;
    for (let i = 0; i < imgs.length; i++) imgs[i]!.classList.toggle("visible", this.times[i]! <= t);
    const latest = this.frames[last];
    this.ui.large.hidden = !latest;
    if (latest && this.ui.large.src !== this.urls[last]) this.ui.large.src = this.urls[last]!;
    this.ui.caption.textContent = latest ? caption(latest) : "no frame yet";
  }
}

function caption(f: TimedFrame): string {
  return `${f.id} · ${f.t.toFixed(1)} s`;
}

/** The robot's view: the head camera's video, kept on the replay's time. Offered only when the episode has one. */
export class HeadCamera {
  private cam: CameraVideo | null = null;
  private url = "";
  private starting = false; // play() was called and has not started yet

  constructor(
    private readonly ui: { box: HTMLElement; video: HTMLVideoElement; label: HTMLElement; toggle: HTMLInputElement },
    private readonly replay: ReplayClock,
  ) {
    ui.toggle.addEventListener("change", () => {
      this.showOrHide();
      this.sync();
    });
    ui.box.addEventListener("click", () => ui.box.classList.toggle("large"));
    ui.video.addEventListener("seeked", () => { if (!this.replay.playing) this.sync(); }); // the last scrub may have come mid-seek
  }

  show(ep: Episode): void {
    const { box, video, label, toggle } = this.ui;
    const cam = this.cam = ep.cameraVideo;
    video.pause();
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = cam ? URL.createObjectURL(cam.blob) : "";
    if (cam) {
      video.src = this.url;
      box.style.aspectRatio = `${cam.width} / ${cam.height}`;
      label.textContent = `${cam.camera} camera · ${cam.fps} fps`;
    } else {
      video.removeAttribute("src");
      video.load();
    }
    toggle.disabled = !cam;
    this.showOrHide();
  }

  /** Puts the video where the replay is (see `videoSync`). */
  sync(): void {
    const video = this.ui.video;
    if (!this.cam || this.ui.box.hidden) {
      if (!video.paused) video.pause();
      return;
    }
    const { run, rate, seekTo } = videoSync(this.cam, this.replay, { currentTime: video.currentTime, seeking: video.seeking, starting: this.starting });
    if (rate !== null && video.playbackRate !== rate) video.playbackRate = rate;
    if (!run && !video.paused) video.pause();
    if (seekTo !== null) video.currentTime = seekTo;
    if (run && video.paused && !this.starting) {
      this.starting = true;
      video.play().catch(() => undefined).finally(() => (this.starting = false));
    }
  }

  private showOrHide(): void {
    this.ui.box.hidden = !this.cam || !this.ui.toggle.checked;
  }
}
