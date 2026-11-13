/** Keeping the head camera's video on the replay's time. Decisions only: the caller applies them to the <video>. */
import type { CameraVideo } from "./episode";

const DRIFT_S = 0.3; // video seconds the camera may run ahead of or behind the replay before it is put back

/** Where the replay is: the Player, seen by what follows it. */
export interface ReplayClock { readonly t: number; readonly playing: boolean; readonly speed: number }
/** What the <video> reports; `starting` is true from a play() call until it has started. */
export interface VideoReading { currentTime: number; seeking: boolean; starting: boolean }
/** What to do with the <video>: run or hold it, the rate to run at, and the time to jump to (null: leave it). */
export interface VideoCommand { run: boolean; rate: number | null; seekTo: number | null }

/** While the replay plays inside the video's span, the video plays along at the replay's speed and is sought only
 *  when it drifts. Paused, scrubbing or outside the span, it is held on the frame taken last at or before t: never
 *  left to run to its end, because play() after the end would restart it from the beginning. */
export function videoSync(cam: Pick<CameraVideo, "fps" | "start" | "frames">, replay: ReplayClock, video: VideoReading): VideoCommand {
  const along = replay.t - cam.start; // where the video is if it runs with the replay
  if (replay.playing && along >= 0 && along < (cam.frames - 1) / cam.fps) {
    const drifted = Math.abs(video.currentTime - along) > DRIFT_S * Math.max(1, replay.speed);
    return { run: true, rate: replay.speed, seekTo: drifted && !video.starting ? along : null };
  }
  const still = videoTimeAt(cam, replay.t);
  const off = Math.abs(video.currentTime - still) > 0.5 / cam.fps;
  return { run: false, rate: null, seekTo: off && !video.seeking ? still : null };
}

/** The video time that shows what the camera saw at replay time t: the middle of the last frame taken at or before
 *  t, safe from rounding at the frame's edges. The first frame is held before it was taken, the last one after. */
export function videoTimeAt(video: Pick<CameraVideo, "fps" | "start" | "frames">, t: number): number {
  const frame = Math.min(Math.max(Math.floor((t - video.start) * video.fps + 1e-6), 0), video.frames - 1);
  return (frame + 0.5) / video.fps;
}
