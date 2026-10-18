"""Music ingestion and subtitle-driven audio mixing for preview and export."""

from __future__ import annotations

import math
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field

from . import media, procs
from .jobs import Job


class MusicSettings(BaseModel):
    enabled: bool = False
    noVoiceVolume: float = Field(default=35, ge=0, le=100, allow_inf_nan=False)
    voiceVolume: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    videoVolume: float = Field(default=100, ge=0, le=100, allow_inf_nan=False)


def youtube_url(value: str) -> str:
    """Accept one YouTube video, never an arbitrary URL or playlist."""
    url = urlparse(value.strip())
    host = (url.hostname or "").lower()
    video_id = ""
    if url.scheme == "https" and not url.username and not url.password and url.port in (None, 443):
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if url.path == "/watch":
                video_id = parse_qs(url.query).get("v", [""])[0]
            elif url.path.startswith(("/shorts/", "/embed/", "/live/")):
                video_id = url.path.split("/")[2]
        elif host == "youtu.be":
            video_id = url.path.strip("/")
    if len(video_id) != 11 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in video_id):
        raise ValueError("Paste an https:// YouTube video link (not a playlist).")
    return f"https://www.youtube.com/watch?v={video_id}"


def download(url: str, workdir: Path, job: Job) -> Path:
    """Run yt-dlp as a tracked child so cancellation/shutdown stops downloads."""
    job.update(-1, "Downloading audio from YouTube…")
    args = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-progress",
            "--no-cache-dir", "--socket-timeout", "20", "--retries", "2", "--max-filesize", "500M",
            "--format", "bestaudio/best", "--output", str(workdir / "source.%(ext)s"),
            "--ffmpeg-location", media.ffmpeg_exe()]
    if shutil.which("node"):
        args += ["--js-runtimes", "node"]
    proc = procs.popen([*args, "--", youtube_url(url)], stdout=-1, stderr=-1,
                       text=True, encoding="utf-8", errors="replace")
    job.on_cancel = proc.kill
    try:
        job.check()
        _out, err = proc.communicate(timeout=600)
        job.check()
        if proc.returncode:
            raise ValueError("YouTube download failed. The video may be restricted or YouTube may require a login. "
                             "Try a local audio file instead.\n" + err[-1200:])
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        procs.forget(proc)
        job.on_cancel = None
    files = [p for p in workdir.glob("source.*") if p.suffix not in {".part", ".ytdl"}]
    if not files:
        raise ValueError("YouTube returned no audio (download limit: 500 MB).")
    return files[0]


def prepare(source: Path, dest: Path, job: Job) -> float:
    """Normalize any decodable audio to browser-friendly AAC, without video."""
    job.update(-1, "Preparing music for preview and export…")
    media.run_ffmpeg(["-y", "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000",
                      "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)],
                     job, 0, label="Preparing music")
    # PyAV is already required by transcription; use it for audio-only metadata.
    import av

    with av.open(str(dest)) as container:
        duration = float(container.duration or 0) / av.time_base
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("That audio file has no playable duration.")
    return duration


def envelope(cues: list, duration: float) -> list[tuple[float, float]]:
    """Speech fraction, with 200 ms attack / 350 ms release. Mirrored in music.js."""
    ranges: list[list[float]] = []
    for cue in cues:
        start, end = float(cue["start"]), float(cue["end"])
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Subtitle timings must be finite numbers.")
        start, end = max(0.0, start), min(duration, end)
        if end > start:
            ranges.append([start, end])
    merged: list[list[float]] = []
    for start, end in sorted(ranges):
        if merged and start - merged[-1][1] <= 0.55:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    points = {0.0: 0.0, duration: 0.0}
    for start, end in merged:
        points[max(0.0, start - 0.2)] = 0.0
        points[start] = 1.0
        if end < duration:
            points[min(duration, end + 0.35)] = 0.0
        points[end] = 1.0
    return sorted(points.items())


def value_at(points: list[tuple[float, float]], time: float) -> float:
    for (start, a), (end, b) in zip(points, points[1:]):
        if time < end:
            return a + (b - a) * max(0, (time - start) / (end - start))
    return points[-1][1]


def render_args(settings: MusicSettings, track: Path | None, cues: list, duration: float,
                start: float, total: float, has_audio: bool, input_index: int,
                workdir: Path, track_duration: float = 0, playback_speed: float = 1,
                speed_music: bool = False) -> tuple[list[str], list[str]]:
    """Extra inputs and output options. Leave untouched audio as stream copy."""
    use_music = settings.enabled and track is not None
    if not use_music and settings.videoVolume == 100 and playback_speed == 1:
        return [], ["-map", "0:a?", "-c:a", "copy"]
    inputs, filters, outputs = [], [], []
    if has_audio:
        speed_filter = media.pitched_speed_filter(playback_speed) + "," if playback_speed != 1 else ""
        filters.append(f"[0:a:0]{speed_filter}"
                       f"volume={settings.videoVolume / 100:.8f}[original]")
        outputs.append("[original]")
    if use_music:
        music_start = start if speed_music else start / playback_speed
        offset = music_start % track_duration if track_duration > 0 else music_start
        inputs = ["-stream_loop", "-1", "-ss", f"{offset:.6f}", "-i", str(track)]
        points = envelope(cues, duration)
        points = [(0.0, value_at(points, start)),
                  *((t - start, v) for t, v in points if start < t < start + total),
                  (total, value_at(points, start + total))]
        points = [(t / playback_speed, amount) for t, amount in points]
        commands = []
        for (t, a), (end, b) in zip(points, points[1:]):
            if end - t < 0.000001:
                continue  # Command timestamps have microsecond precision.
            gain = (settings.noVoiceVolume + (settings.voiceVolume - settings.noVoiceVolume) * a) / 100
            next_gain = (settings.noVoiceVolume + (settings.voiceVolume - settings.noVoiceVolume) * b) / 100
            expr = f"{gain:.8f}+({next_gain - gain:.8f})*clip((t-{t:.6f})/{end - t:.6f},0,1)"
            commands.append(f"{t:.6f} volume@music volume '{expr}';")
        (workdir / "music-volume.txt").write_text("\n".join(commands), "utf-8")
        initial = (settings.noVoiceVolume + (settings.voiceVolume - settings.noVoiceVolume) * points[0][1]) / 100
        speed_filter = media.pitched_speed_filter(playback_speed) + "," if speed_music and playback_speed != 1 else ""
        filters.append(f"[{input_index}:a:0]asetpts=PTS-STARTPTS,{speed_filter}asendcmd=f=music-volume.txt,"
                       f"volume@music={initial:.8f}:eval=frame[music]")
        outputs.append("[music]")
    if not outputs:
        return [], ["-an"]
    if len(outputs) == 2:
        filters.append("".join(outputs) + "amix=inputs=2:duration=longest:normalize=0[mixed]")
        output = "[mixed]"
    else:
        output = outputs[0]
    # A limiter catches clipping when both sources are loud. No automatic gain boost.
    filters.append(f"{output}alimiter=limit=0.95:level=0:latency=1[audio]")
    (workdir / "music-filter.txt").write_text(";\n".join(filters), "utf-8")
    return inputs, ["-filter_complex_script", "music-filter.txt", "-map", "[audio]",
                    "-c:a", "aac", "-b:a", "192k", "-t", f"{total / playback_speed:.6f}"]
