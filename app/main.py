"""Subtitle Studio web server (FastAPI). Binds to localhost only."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import media, subs, transcribe
from .fonts import FontLibrary
from .jobs import Job, JobManager
from .projects import ProjectStore

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
DATA = Path(os.environ.get("SUBSTUDIO_DATA", ROOT / "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "uploads").mkdir(exist_ok=True)
(DATA / "outputs").mkdir(exist_ok=True)

VERSION = "1.0.0"

app = FastAPI(title="Subtitle Studio", version=VERSION)
jobs = JobManager()
store = ProjectStore(DATA)
fonts = FontLibrary(DATA)
PRESETS_FILE = DATA / "presets.json"

# Warm slow lookups in the background so the first page load is snappy.
_system: dict = {}


def _warm():
    try:
        fonts.all()
    except Exception:
        pass
    try:
        _system["encoders"] = media.encoders()
    except Exception:
        _system["encoders"] = [{"id": "libx264", "label": "CPU (x264)"}]
    try:
        _system.update(transcribe.device_info())
    except Exception:
        _system.update({"cuda": False, "gpuName": None})


threading.Thread(target=_warm, daemon=True).start()

LANG3 = {
    "en": "eng", "hi": "hin", "es": "spa", "fr": "fra", "de": "deu", "it": "ita", "pt": "por",
    "ru": "rus", "ja": "jpn", "ko": "kor", "zh": "zho", "ar": "ara", "bn": "ben", "ur": "urd",
    "ta": "tam", "te": "tel", "mr": "mar", "gu": "guj", "pa": "pan", "kn": "kan", "ml": "mal",
    "tr": "tur", "nl": "nld", "pl": "pol", "id": "ind", "vi": "vie", "th": "tha", "uk": "ukr",
}


def _project(pid: str) -> dict:
    p = store.load(pid)
    if p is None:
        raise HTTPException(404, "Project not found")
    return p


def _job_or_404(jid: str) -> Job:
    j = jobs.get(jid)
    if j is None:
        raise HTTPException(404, "Job not found")
    return j


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem} {i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def _output_dir(project: dict, requested: str | None) -> Path:
    for cand in [requested, str(Path(project["videoPath"]).parent)]:
        if not cand:
            continue
        d = Path(cand).expanduser()
        if d.is_dir() and os.access(d, os.W_OK) and DATA / "uploads" not in [d, *d.parents]:
            return d
    return DATA / "outputs"


def _prepare_workdir(workdir: Path, project: dict, cues: list, style: dict) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    w, h = project["info"]["width"], project["info"]["height"]
    st = subs.merged_style(style)
    (workdir / "subs.ass").write_text(subs.build_ass(cues, st, w, h, project["name"]), "utf-8")
    fdir = workdir / "fonts"
    if fdir.exists():
        shutil.rmtree(fdir, ignore_errors=True)
    fonts.copy_family(st["fontFamily"], fdir)
    fdir.mkdir(exist_ok=True)
    return workdir


# ------------------------------------------------------------------ system


@app.get("/api/system")
def system():
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    return {
        "version": VERSION,
        "platform": platform.system(),
        "python": sys.version.split()[0],
        "cuda": _system.get("cuda"),
        "gpuName": _system.get("gpuName"),
        "encoders": _system.get("encoders") or [{"id": "libx264", "label": "CPU (x264)"}],
        "models": transcribe.MODELS,
        "languages": sorted(_LANGUAGE_CODES),
        "dataDir": str(DATA),
        "defaultStyle": subs.DEFAULT_STYLE,
    }


# ------------------------------------------------------------------ fonts


@app.get("/api/fonts")
def list_fonts(rescan: bool = False):
    if rescan:
        fonts.all(rescan=True)
    return fonts.public_list()


@app.get("/api/fonts/file")
def font_file(family: str, style: str = "regular"):
    face = fonts.face(family, style)
    if not face:
        raise HTTPException(404, "Font not found")
    path = Path(face["file"])
    media_type = "font/otf" if path.suffix.lower() == ".otf" else "font/ttf"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "max-age=86400"})


@app.post("/api/fonts/upload")
async def upload_font(file: UploadFile = File(...)):
    data = await file.read()
    try:
        family = fonts.add_user_font(file.filename or "font.ttf", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"family": family}


# ------------------------------------------------------------------ presets


@app.get("/api/presets")
def get_presets():
    if PRESETS_FILE.exists():
        try:
            return json.loads(PRESETS_FILE.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return []


@app.put("/api/presets")
def put_presets(presets: list = Body(...)):
    PRESETS_FILE.write_text(json.dumps(presets, ensure_ascii=False, indent=1), "utf-8")
    return {"ok": True}


# ------------------------------------------------------------------ projects


def _maybe_proxy(project: dict) -> str | None:
    if not project.get("needsProxy") or project.get("proxyReady"):
        return None
    for j in jobs.active():
        if j.kind == "proxy" and j.project_id == project["id"]:
            return j.id
    dest = store.dir(project["id"]) / "proxy.mp4"

    def work(job: Job):
        tmp = dest.with_suffix(".tmp.mp4")
        media.make_proxy(project["videoPath"], tmp, project["info"], job)
        os.replace(tmp, dest)
        store.update(project["id"], proxyReady=True)
        return {"ok": True}

    return jobs.start("proxy", work, project["id"]).id


@app.post("/api/open")
def open_path(body: dict = Body(...)):
    path = (body.get("path") or "").strip().strip('"').strip("'")
    if not path:
        raise HTTPException(400, "No path given")
    try:
        project = store.open_video(path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"project": project, "proxyJob": _maybe_proxy(project)}


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    name = Path(file.filename or "video.mp4").name
    dest = DATA / "uploads" / name
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "wb") as f:
        while chunk := await file.read(8 * 1024 * 1024):
            f.write(chunk)
    os.replace(tmp, dest)
    try:
        project = store.open_video(dest)
    except ValueError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    return {"project": project, "proxyJob": _maybe_proxy(project)}


@app.get("/api/projects")
def recent_projects():
    return store.recent()


@app.get("/api/project/{pid}")
def get_project(pid: str):
    project = _project(pid)
    return {"project": project, "proxyJob": _maybe_proxy(project)}


@app.put("/api/project/{pid}")
def save_project(pid: str, body: dict = Body(...)):
    _project(pid)
    allowed = {k: body[k] for k in ("segments", "style", "layout", "language") if k in body}
    store.update(pid, **allowed)
    return {"ok": True}


@app.get("/api/project/{pid}/media")
def project_media(pid: str):
    project = _project(pid)
    path = store.playback_path(project)
    if not path.exists():
        raise HTTPException(404, "Video file is missing")
    return FileResponse(path)


@app.post("/api/project/{pid}/import")
async def import_subs(pid: str, file: UploadFile = File(...)):
    _project(pid)
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    segs = subs.parse_srt_vtt(text)
    if not segs:
        raise HTTPException(400, "No subtitles found in that file (expected .srt or .vtt)")
    store.update(pid, segments=segs)
    return {"segments": segs}


# ------------------------------------------------------------------ transcription


@app.post("/api/project/{pid}/transcribe")
def start_transcribe(pid: str, settings: dict = Body(...)):
    project = _project(pid)
    for j in jobs.active():
        if j.kind == "transcribe":
            raise HTTPException(409, "A transcription is already running")

    def work(job: Job):
        res = transcribe.transcribe(job, project["videoPath"], project["info"]["duration"], settings)
        store.update(pid, segments=res["segments"], language=res["language"],
                     transcribeMeta={k: v for k, v in res.items() if k != "segments"})
        return res

    return {"jobId": jobs.start("transcribe", work, pid).id}


# ------------------------------------------------------------------ jobs


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    return _job_or_404(jid).to_dict()


@app.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: str):
    _job_or_404(jid).cancel()
    return {"ok": True}


@app.get("/api/jobs/{jid}/file")
def job_file(jid: str):
    j = _job_or_404(jid)
    if j.status != "done" or not j.result or "path" not in j.result:
        raise HTTPException(404, "No output yet")
    p = Path(j.result["path"])
    return FileResponse(p, filename=p.name)


# ------------------------------------------------------------------ subtitles + rendering


@app.post("/api/project/{pid}/subtitles")
def subtitle_file(pid: str, body: dict = Body(...)):
    project = _project(pid)
    fmt = body.get("format", "srt")
    cues = body.get("cues") or []
    name = project["name"]
    if fmt == "ass":
        text = subs.build_ass(cues, body.get("style") or {}, project["info"]["width"], project["info"]["height"], name)
    elif fmt == "vtt":
        text = subs.build_vtt(cues)
    elif fmt == "txt":
        text = subs.build_txt(project.get("segments") or [])
    else:
        fmt, text = "srt", subs.build_srt(cues)
    mime = {"vtt": "text/vtt", "ass": "text/x-ssa"}.get(fmt, "text/plain")
    return Response(
        text.encode("utf-8"),
        media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_ascii(name)}.{fmt}"'},
    )


def _ascii(name: str) -> str:
    return "".join(c if c.isascii() and c not in '"\\' else "_" for c in name) or "subtitles"


@app.post("/api/project/{pid}/frame")
def preview_frame(pid: str, body: dict = Body(...)):
    project = _project(pid)
    t = float(body.get("time") or 0)
    cues = [c for c in body.get("cues") or [] if c.get("start", 0) <= t + 1 and c.get("end", 0) >= t - 1]
    workdir = _prepare_workdir(store.dir(pid) / "frame", project, cues, body.get("style") or {})
    try:
        out = media.render_frame(project["videoPath"], t, workdir)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return Response(out.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/project/{pid}/render")
def render(pid: str, body: dict = Body(...)):
    project = _project(pid)
    cues = body.get("cues") or []
    style = body.get("style") or {}
    mode = body.get("mode", "burn")
    container = "mkv" if body.get("container") == "mkv" else "mp4"
    encoder = body.get("encoder") or "libx264"
    if encoder not in {e["id"] for e in (_system.get("encoders") or [])} | {"libx264"}:
        encoder = "libx264"
    quality = int(body.get("quality") or 20)
    speed = body.get("speed") or "medium"
    height = int(body.get("height") or 0)
    clip = body.get("clip")  # {"start": s, "duration": d} for a quick test render
    info = project["info"]
    src = project["videoPath"]
    out_dir = _output_dir(project, body.get("outputDir"))
    suffix = " (subtitled)" if mode == "burn" else " (soft subs)"
    if clip:
        suffix += " test"
    out = _unique(out_dir / f"{project['name']}{suffix}.{container}")

    def work(job: Job):
        workdir = _prepare_workdir(store.dir(pid) / f"render-{job.id}", project, cues, style)
        lang = LANG3.get(project.get("language") or "", "und")
        try:
            if mode == "soft":
                if container == "mkv":
                    sub_in, sub_codec = "subs.ass", "ass"
                else:
                    (workdir / "subs.srt").write_text(subs.build_srt(cues), "utf-8")
                    sub_in, sub_codec = "subs.srt", "mov_text"
                args = ["-y", "-i", src, "-i", sub_in, "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
                        "-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec,
                        "-metadata:s:s:0", f"language={lang}", "-disposition:s:0", "default"]
                if container == "mkv":
                    for i, f in enumerate(sorted((workdir / "fonts").iterdir())):
                        mt = "application/vnd.ms-opentype" if f.suffix.lower() == ".otf" else "application/x-truetype-font"
                        args += ["-attach", str(f), f"-metadata:s:t:{i}", f"mimetype={mt}"]
                elif info.get("acodec") not in media.MP4_AUDIO:
                    args[args.index("-c:a") + 1] = "aac"
                args += ["-movflags", "+faststart"] if container == "mp4" else []
                args.append(str(out))
                media.run_ffmpeg(args, job, info["duration"], cwd=workdir, label="Muxing subtitles")
            else:
                vf = []
                # "720p" means the short side, so portrait videos scale correctly too.
                if height and height < min(info["width"], info["height"]):
                    vf.append(f"scale={height}:-2" if info["width"] < info["height"] else f"scale=-2:{height}")
                pre, total = [], info["duration"]
                if clip:
                    start = max(0.0, float(clip.get("start") or 0))
                    total = max(0.5, min(float(clip.get("duration") or 10), info["duration"] - start))
                    pre = ["-ss", f"{start:.3f}", "-t", f"{total:.3f}"]
                    vf = [f"setpts=PTS+{start:.3f}/TB"] + vf + [media.subtitles_filter(), "setpts=PTS-STARTPTS"]
                else:
                    vf.append(media.subtitles_filter())
                args = ["-y", *pre, "-i", src, "-vf", ",".join(vf), "-map", "0:v:0", "-map", "0:a?",
                        *media.video_codec_args(encoder, quality, speed), "-pix_fmt", "yuv420p"]
                if clip:
                    # setpts shifts drop the stream frame rate; keep source timestamps (else 25 fps).
                    args += ["-fps_mode", "passthrough"]
                if container == "mp4" and info.get("acodec") not in media.MP4_AUDIO:
                    args += ["-c:a", "aac", "-b:a", "192k"]
                else:
                    args += ["-c:a", "copy"]
                if container == "mp4":
                    args += ["-movflags", "+faststart"]
                args.append(str(out))
                media.run_ffmpeg(args, job, total, cwd=workdir, label="Burning subtitles")
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return {"path": str(out), "name": out.name, "size": out.stat().st_size, "dir": str(out.parent)}

    return {"jobId": jobs.start("render", work, pid).id, "output": str(out)}


@app.post("/api/reveal")
def reveal(body: dict = Body(...)):
    """Open the folder containing a rendered file in the OS file manager."""
    p = Path(body.get("path") or "")
    target = p.parent if p.is_file() else p
    if not target.is_dir():
        raise HTTPException(404, "Folder not found")
    try:
        if os.name == "nt":
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        raise HTTPException(500, f"Could not open folder: {e}")
    return {"ok": True}


@app.exception_handler(Exception)
async def unhandled(_req, exc: Exception):
    return JSONResponse({"detail": f"{exc.__class__.__name__}: {exc}"}, status_code=500)


app.mount("/", StaticFiles(directory=APP_DIR / "static", html=True), name="static")
