"""Project persistence: one JSON file per video under ``data/projects/<id>/``."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from . import media, subs


class ProjectStore:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def dir(self, pid: str) -> Path:
        if not pid.isalnum():
            raise KeyError(pid)
        return self.root / pid

    def _file(self, pid: str) -> Path:
        return self.dir(pid) / "project.json"

    @staticmethod
    def make_id(path: Path) -> str:
        st = path.stat()
        raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]

    def open_video(self, path: str | Path) -> dict:
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")
        pid = self.make_id(p)
        existing = self.load(pid)
        if existing:
            existing["updated"] = time.time()
            self.save(existing)
            return existing
        info = media.probe(p)
        prx, pry = subs.play_res(info["width"], info["height"])
        project = {
            "id": pid,
            "name": p.stem,
            "videoPath": str(p.resolve()),
            "info": info,
            "playRes": [prx, pry],
            "needsProxy": not media.browser_playable(info, str(p)),
            "proxyReady": False,
            "segments": [],
            "language": None,
            "transcribeMeta": None,
            "style": None,
            "layout": None,
            "created": time.time(),
            "updated": time.time(),
        }
        self.dir(pid).mkdir(parents=True, exist_ok=True)
        self.save(project)
        return project

    def load(self, pid: str) -> dict | None:
        try:
            f = self._file(pid)
        except KeyError:
            return None
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, project: dict) -> None:
        with self._lock:
            d = self.dir(project["id"])
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "project.json.tmp"
            tmp.write_text(json.dumps(project, ensure_ascii=False), "utf-8")
            os.replace(tmp, d / "project.json")

    def update(self, pid: str, **fields) -> dict:
        with self._lock:
            project = self.load(pid)
            if project is None:
                raise KeyError(pid)
            project.update(fields)
            project["updated"] = time.time()
            self.save(project)
            return project

    def recent(self, limit: int = 20) -> list[dict]:
        out = []
        for d in self.root.iterdir():
            f = d / "project.json"
            if f.exists():
                try:
                    p = json.loads(f.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                out.append(
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "videoPath": p["videoPath"],
                        "updated": p.get("updated", 0),
                        "segments": len(p.get("segments") or []),
                        "exists": Path(p["videoPath"]).exists(),
                    }
                )
        out.sort(key=lambda p: p["updated"], reverse=True)
        return out[:limit]

    def attach_music(self, pid: str, track: dict) -> dict:
        """Publish a completed import without overwriting concurrent editor saves."""
        with self._lock:
            project = self.load(pid)
            if project is None:
                raise KeyError(pid)
            return self.update(pid, musicTrack=track, music={**(project.get("music") or {}), "enabled": True})

    def playback_path(self, project: dict) -> Path:
        if project.get("needsProxy"):
            proxy = self.dir(project["id"]) / "proxy.mp4"
            if proxy.exists() and project.get("proxyReady"):
                return proxy
        return Path(project["videoPath"])
