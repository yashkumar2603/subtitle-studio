"""ffmpeg helpers: probing, encoder detection, progress-tracked runs, renders.

Uses the static ffmpeg bundled by ``imageio-ffmpeg`` (built with libass), so no
system install is needed on Windows or Linux. Set ``FFMPEG_BINARY`` to override.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections import deque
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from . import procs
from .jobs import Job

BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac", None}
MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac", "opus"}


class PlaybackSettings(BaseModel):
    playbackSpeed: float = Field(default=1, ge=1, le=3, allow_inf_nan=False)
    speedMusic: bool = False


def pitched_speed_filter(rate: float) -> str:
    """Play samples faster: pitch/formants rise together, like a sped-up record."""
    return f"aresample=48000,asetrate={48000 * rate:.6f},aresample=48000"


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    env = os.environ.get("FFMPEG_BINARY")
    if env and Path(env).exists():
        return env
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return procs.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        **kw,
    )


def _ts(s: str) -> float:
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def probe(path: str | Path) -> dict:
    err = _run(["-i", str(path)]).stderr
    info = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 30.0,
        "vcodec": None,
        "acodec": None,
        "rotation": 0,
        "hasAudio": False,
    }
    m = re.search(r"Duration:\s*(\d+:\d+:\d+(?:\.\d+)?)", err)
    if m:
        info["duration"] = _ts(m.group(1))
    v = re.search(r"Stream #\d+:\d+[^\n]*?: Video: (\w+)[^\n]*?, (\d{2,5})x(\d{2,5})", err)
    if v:
        info["vcodec"], info["width"], info["height"] = v.group(1), int(v.group(2)), int(v.group(3))
        line = err[v.start() : err.find("\n", v.start())]
        f = re.search(r"([\d.]+) fps", line) or re.search(r"([\d.]+) tbr", line)
        if f:
            info["fps"] = float(f.group(1))
    a = re.search(r"Stream #\d+:\d+[^\n]*?: Audio: (\w+)", err)
    if a:
        info["acodec"], info["hasAudio"] = a.group(1), True
    r = re.search(r"rotation of (-?[\d.]+) degrees", err) or re.search(r"rotate\s*:\s*(-?\d+)", err)
    if r:
        info["rotation"] = int(round(float(r.group(1)))) % 360
        if info["rotation"] in (90, 270):
            info["width"], info["height"] = info["height"], info["width"]
    if not v:
        raise ValueError("No video stream found in that file")
    return info


def browser_playable(info: dict, path: str) -> bool:
    ext = Path(path).suffix.lower()
    return (
        info.get("vcodec") in BROWSER_VIDEO
        and info.get("acodec") in BROWSER_AUDIO
        and ext in (".mp4", ".m4v", ".webm", ".mov", ".mkv")
    )


@lru_cache(maxsize=1)
def encoders() -> list[dict]:
    """H.264 encoders that actually work on this machine (hardware ones are test-encoded)."""
    listed = _run(["-encoders"]).stdout
    out = [{"id": "libx264", "label": "CPU (x264, best quality per size)"}]
    hw = [
        ("h264_nvenc", "NVIDIA GPU (NVENC, fast)"),
        ("h264_qsv", "Intel GPU (QuickSync, fast)"),
        ("h264_amf", "AMD GPU (AMF, fast)"),
    ]
    for enc, label in hw:
        if re.search(rf"\b{enc}\b", listed):
            test = _run(
                ["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-c:v", enc, "-f", "null", "-"],
                timeout=30,
            )
            if test.returncode == 0:
                out.append({"id": enc, "label": label})
    return out


def video_codec_args(encoder: str, quality: int, speed: str = "medium") -> list[str]:
    q = str(int(quality))
    if encoder == "h264_nvenc":
        preset = {"fast": "p3", "medium": "p5", "slow": "p7"}.get(speed, "p5")
        return ["-c:v", "h264_nvenc", "-preset", preset, "-rc", "vbr", "-cq", q, "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", q]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", q, "-qp_p", q]
    preset = {"fast": "veryfast", "medium": "medium", "slow": "slow"}.get(speed, "medium")
    return ["-c:v", "libx264", "-preset", preset, "-crf", q]


def run_ffmpeg(args: list[str], job: Job | None, duration: float, cwd: str | Path | None = None,
               progress_span: tuple[float, float] = (0.0, 1.0), label: str = "Rendering") -> None:
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats", *args]
    proc = procs.popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if job:
        job.on_cancel = proc.kill
    tail: deque[str] = deque(maxlen=40)

    def drain():
        for line in proc.stderr:  # type: ignore[union-attr]
            tail.append(line.rstrip())

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    lo, hi = progress_span
    for line in proc.stdout:  # type: ignore[union-attr]
        if not job:
            continue
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            if duration > 0:
                frac = max(0.0, min(1.0, us / 1e6 / duration))
                job.progress = lo + (hi - lo) * frac
                job.message = f"{label}… {int(frac * 100)}%"
        if job.cancelled:
            proc.kill()
    proc.wait()
    procs.forget(proc)
    t.join(timeout=2)
    if job:
        job.on_cancel = None
        job.check()
    if proc.returncode != 0:
        msg = "\n".join(l for l in tail if l.strip())[-1500:]
        raise RuntimeError(f"ffmpeg failed:\n{msg}")


def subtitles_filter(ass_name: str = "subs.ass", fonts_dir: str = "fonts") -> str:
    return f"subtitles=filename={ass_name}:fontsdir={fonts_dir}"


def render_frame(video: str, t: float, workdir: Path, out_name: str = "frame.jpg",
                 max_height: int = 1080) -> Path:
    """Exact libass render of one frame at time ``t`` (uses ``subs.ass`` + ``fonts/`` in workdir)."""
    vf = (
        f"setpts=PTS+{t:.3f}/TB,{subtitles_filter()},"
        f"scale=-2:'min({max_height},ih)'"
    )
    res = _run(
        ["-y", "-ss", f"{t:.3f}", "-i", video, "-vf", vf, "-frames:v", "1", "-q:v", "3", out_name],
        cwd=str(workdir),
        timeout=120,
    )
    out = workdir / out_name
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("Frame render failed:\n" + res.stderr[-1200:])
    return out


def make_proxy(src: str, dest: Path, info: dict, job: Job) -> None:
    run_ffmpeg(
        [
            "-y", "-i", src,
            "-vf", "scale=-2:'min(720,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            str(dest),
        ],
        job,
        info["duration"],
        label="Creating browser preview",
    )
