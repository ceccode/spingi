/** Replay clock: current time, play/pause, speed. Independent of the DOM and of 3D. */
export class Player {
  t = 0;
  playing = false;
  speed = 1;
  duration = 1;
  private listeners: ((t: number) => void)[] = [];

  onTick(fn: (t: number) => void): void { this.listeners.push(fn); }

  advance(dtWall: number): void {
    if (!this.playing) return;
    this.t = Math.min(this.duration, this.t + dtWall * this.speed);
    if (this.t >= this.duration) this.playing = false;
    this.emit();
  }

  seek(t: number): void { this.t = Math.max(0, Math.min(this.duration, t)); this.emit(); }
  toggle(): void { if (!this.playing && this.t >= this.duration) this.t = 0; this.playing = !this.playing; this.emit(); }
  reset(duration: number): void { this.duration = Math.max(0.1, duration); this.t = 0; this.playing = false; this.emit(); }

  private emit(): void { for (const fn of this.listeners) fn(this.t); }
}
