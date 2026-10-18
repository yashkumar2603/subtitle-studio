// Subtitle-driven ducking. Keep envelope() aligned with app/music.py.
export const DEFAULT_MUSIC = { enabled: false, noVoiceVolume: 35, voiceVolume: 10, videoVolume: 100 };

export function envelope(cues, duration) {
  const ranges = cues.map((c) => [Math.max(0, c.start), Math.min(duration, c.end)])
    .filter(([s, e]) => Number.isFinite(s) && Number.isFinite(e) && e > s)
    .sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const [s, e] of ranges) {
    const prev = merged.at(-1);
    if (prev && s - prev[1] <= 0.55) prev[1] = Math.max(prev[1], e);
    else merged.push([s, e]);
  }
  const points = new Map([[0, 0], [duration, 0]]);
  for (const [s, e] of merged) {
    points.set(Math.max(0, s - 0.2), 0);
    points.set(s, 1);
    if (e < duration) points.set(Math.min(duration, e + 0.35), 0);
    points.set(e, 1);
  }
  return [...points].sort((a, b) => a[0] - b[0]);
}

export function valueAt(points, time) {
  for (let i = 1; i < points.length; i++) {
    const [s, a] = points[i - 1], [e, b] = points[i];
    if (time < e) return a + (b - a) * Math.max(0, (time - s) / (e - s));
  }
  return points.at(-1)?.[1] || 0;
}

// Web Audio schedules gain ramps against the audio clock, including background tabs.
// The media element streams/loops the track, so long music files need no full decode.
export class MusicPlayer {
  constructor(video, onError) {
    this.video = video;
    this.onError = onError;
    this.audio = new Audio();
    this.audio.loop = true;
    this.audio.preload = "auto";
    this.settings = { ...DEFAULT_MUSIC };
    this.points = [[0, 0]];
    this.url = null;
    this.playing = false;
    this.generation = 0;
    video.addEventListener("playing", () => { this.playing = true; this.play(); });
    for (const event of ["pause", "waiting", "seeking", "ended", "emptied"]) {
      video.addEventListener(event, () => {
        this.generation++;
        this.playing = false;
        this.audio.pause();
        this.schedule();
      });
    }
    video.addEventListener("seeked", () => {
      this.playing = !video.paused && video.readyState >= 3;
      this.play();
    });
    video.addEventListener("ratechange", () => this.play());
    this.audio.addEventListener("loadedmetadata", () => this.play());
    this.audio.addEventListener("error", () => this.onError("Music preview couldn't load. Add the track again."));
    this.audio.addEventListener("playing", () => { this.align(); this.schedule(); });
  }

  setTrack(url) {
    if (url === this.url) return;
    this.generation++;
    this.audio.pause();
    this.url = url;
    if (url) this.audio.src = url;
    else { this.audio.removeAttribute("src"); this.audio.load(); }
  }

  update(settings, cues, duration) {
    this.settings = settings;
    this.points = envelope(cues, duration);
    this.video.volume = settings.videoVolume / 100;
    this.schedule();
    this.play();
  }

  align() {
    if (Number.isFinite(this.audio.duration) && this.audio.duration > 0) {
      const target = this.video.currentTime % this.audio.duration;
      if (Math.abs(this.audio.currentTime - target) > 0.12) this.audio.currentTime = target;
    }
    this.audio.playbackRate = this.video.playbackRate;
  }

  schedule() {
    if (!this.gain) return;
    const { noVoiceVolume: high, voiceVolume: low } = this.settings;
    const volume = (amount) => (high + (low - high) * amount) / 100;
    const time = this.video.currentTime, now = this.context.currentTime;
    const gain = this.gain.gain;
    gain.cancelScheduledValues(now);
    gain.setValueAtTime(volume(valueAt(this.points, time)), now);
    if (!this.video.paused && this.playing) {
      for (const [t, amount] of this.points) {
        if (t > time) gain.linearRampToValueAtTime(volume(amount), now + (t - time) / this.video.playbackRate);
      }
    }
  }

  async play() {
    const generation = ++this.generation;
    if (!this.settings.enabled || !this.url || this.video.paused || !this.playing) {
      this.audio.pause();
      return;
    }
    try {
      if (!this.context) {
        this.context = new AudioContext();
        this.gain = this.context.createGain();
        this.context.createMediaElementSource(this.audio).connect(this.gain).connect(this.context.destination);
      }
      await this.context.resume();
      if (generation !== this.generation) return;
      this.align();
      this.schedule();
      await this.audio.play();
    } catch (e) {
      if (e.name !== "AbortError" && generation === this.generation) {
        this.onError("Music preview couldn't start. Pause and play again. " + e.message);
      }
    }
  }
}
