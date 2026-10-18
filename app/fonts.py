"""System font discovery (Windows, Linux, macOS) plus user-uploaded fonts.

Fonts are grouped by their legacy family name (name ID 1), which is the name
libass matches against, so a family picked in the UI renders identically when
burned in by ffmpeg. Each style records ``ratio`` = unitsPerEm / (winAscent +
winDescent): libass scales glyphs so that winAscent+winDescent equals the ASS
font size, and the browser preview uses this ratio to match it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont

logging.getLogger("fontTools").setLevel(logging.ERROR)

FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}
STYLE_KEYS = ("regular", "bold", "italic", "bolditalic")
CACHE_VERSION = 2

_lock = threading.Lock()
_fonts: dict[str, dict] | None = None


def system_font_dirs() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        dirs = [windir / "Fonts"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    else:
        dirs = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            home / ".local" / "share" / "fonts",
            home / ".fonts",
        ]
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            dirs.append(Path(xdg) / "fonts")
    return [d for d in dirs if d.is_dir()]


def _name(font: TTFont, *ids: int) -> str | None:
    table = font["name"] if "name" in font else None
    if table is None:
        return None
    for name_id in ids:
        # Prefer English (Windows platform, en-US) then anything decodable.
        rec = table.getName(name_id, 3, 1, 0x409) or table.getName(name_id, 1, 0, 0)
        if rec is None:
            for r in table.names:
                if r.nameID == name_id:
                    rec = r
                    break
        if rec is not None:
            try:
                return rec.toUnicode().strip()
            except Exception:
                continue
    return None


def _style_key(font: TTFont, subfamily: str) -> str:
    bold = italic = False
    if "OS/2" in font:
        sel = font["OS/2"].fsSelection
        bold = bool(sel & 0x20)
        italic = bool(sel & 0x01)
    if "head" in font:
        mac = font["head"].macStyle
        bold = bold or bool(mac & 0x01)
        italic = italic or bool(mac & 0x02)
    sub = (subfamily or "").lower()
    bold = bold or "bold" in sub
    italic = italic or "italic" in sub or "oblique" in sub
    if bold and italic:
        return "bolditalic"
    if bold:
        return "bold"
    if italic:
        return "italic"
    return "regular"


def _ratio(font: TTFont) -> float:
    try:
        upem = font["head"].unitsPerEm
        if "OS/2" in font:
            os2 = font["OS/2"]
            total = os2.usWinAscent + os2.usWinDescent
            if total > 0:
                return upem / total
        if "hhea" in font:
            hhea = font["hhea"]
            total = hhea.ascent - hhea.descent
            if total > 0:
                return upem / total
    except Exception:
        pass
    return 0.85


def _describe(font: TTFont, path: Path, index: int) -> dict | None:
    family = _name(font, 1)
    if not family:
        return None
    if family.startswith("."):  # hidden macOS system fonts
        return None
    # Skip symbol / icon fonts that cannot render normal text.
    try:
        cmap = font.getBestCmap() or {}
        if ord("a") not in cmap or ord("A") not in cmap:
            return None
    except Exception:
        return None
    sub = _name(font, 2) or "Regular"
    return {
        "family": family,
        "style": _style_key(font, sub),
        "subfamily": sub,
        "file": str(path),
        "index": index,
        "ratio": round(_ratio(font), 5),
    }


def _scan_file(path: Path) -> list[dict]:
    out = []
    try:
        if path.suffix.lower() in (".ttc", ".otc"):
            coll = TTCollection(str(path), lazy=True)
            for i, font in enumerate(coll.fonts):
                d = _describe(font, path, i)
                if d:
                    out.append(d)
        else:
            font = TTFont(str(path), lazy=True, fontNumber=0)
            d = _describe(font, path, 0)
            if d:
                out.append(d)
    except Exception:
        pass
    return out


def _iter_font_files(dirs: list[Path]):
    for d in dirs:
        for root, _dirs, files in os.walk(d):
            for f in files:
                if Path(f).suffix.lower() in FONT_EXTS:
                    yield Path(root) / f


def _build(dirs: list[Path], cache_file: Path, user_dir: Path) -> dict[str, dict]:
    cache: dict = {}
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text("utf-8"))
            if data.get("version") == CACHE_VERSION:
                cache = data.get("files", {})
        except Exception:
            cache = {}
    new_cache = {}
    families: dict[str, dict] = {}
    for path in _iter_font_files(dirs):
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        entry = cache.get(key)
        if not entry or entry.get("sig") != sig:
            entry = {"sig": sig, "faces": _scan_file(path)}
        new_cache[key] = entry
        user = user_dir in path.parents
        for face in entry["faces"]:
            fam = families.setdefault(
                face["family"], {"family": face["family"], "styles": {}, "user": False}
            )
            fam["user"] = fam["user"] or user
            existing = fam["styles"].get(face["style"])
            # Prefer standalone .ttf/.otf over collections (browsers can't load .ttc).
            if existing is None or (
                existing["file"].lower().endswith((".ttc", ".otc"))
                and not face["file"].lower().endswith((".ttc", ".otc"))
            ):
                fam["styles"][face["style"]] = {
                    "file": face["file"],
                    "index": face["index"],
                    "ratio": face["ratio"],
                    "subfamily": face["subfamily"],
                }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"version": CACHE_VERSION, "files": new_cache}), "utf-8")
    except OSError:
        pass
    # Families with only e.g. a "bold" face still need a usable default style.
    for fam in families.values():
        if "regular" not in fam["styles"]:
            first = next(iter(fam["styles"].values()))
            fam["styles"]["regular"] = dict(first, fallback=True)
    return dict(sorted(families.items(), key=lambda kv: kv[0].lower()))


class FontLibrary:
    def __init__(self, data_dir: Path):
        self.user_dir = data_dir / "fonts"
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = data_dir / "font_cache.json"

    def all(self, rescan: bool = False) -> dict[str, dict]:
        global _fonts
        with _lock:
            if _fonts is None or rescan:
                _fonts = _build(system_font_dirs() + [self.user_dir], self.cache_file, self.user_dir)
            return _fonts

    def public_list(self) -> list[dict]:
        out = []
        for fam in self.all().values():
            styles = {
                k: {"ratio": v["ratio"], "web": not v["file"].lower().endswith((".ttc", ".otc"))}
                for k, v in fam["styles"].items()
            }
            out.append({"family": fam["family"], "styles": styles, "user": fam["user"]})
        return out

    def face(self, family: str, style: str = "regular") -> dict | None:
        fam = self.all().get(family)
        if not fam:
            return None
        return fam["styles"].get(style) or fam["styles"].get("regular")

    def family_files(self, family: str) -> list[Path]:
        fam = self.all().get(family)
        if not fam:
            return []
        return sorted({Path(v["file"]) for v in fam["styles"].values()})

    def copy_family(self, family: str, dest: Path) -> list[Path]:
        """Copy every file of ``family`` into ``dest`` (used as libass fontsdir)."""
        dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for i, src in enumerate(self.family_files(family)):
            target = dest / f"{i}_{src.name}"
            try:
                shutil.copyfile(src, target)
                copied.append(target)
            except OSError:
                pass
        return copied

    def add_user_font(self, filename: str, data: bytes) -> str | None:
        name = Path(filename).name
        if Path(name).suffix.lower() not in FONT_EXTS:
            raise ValueError("Unsupported font type (use .ttf, .otf or .ttc)")
        target = self.user_dir / name
        target.write_bytes(data)
        faces = _scan_file(target)
        if not faces:
            target.unlink(missing_ok=True)
            raise ValueError("Could not read that font file")
        self.all(rescan=True)
        return faces[0]["family"]
