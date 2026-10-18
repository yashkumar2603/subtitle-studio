"""Subtitle file writers: ASS (styled, for burn-in / MKV), SRT, WebVTT, plain text.

The browser does all line wrapping and cue splitting (so the live preview and the
final render share one layout). It sends ``cues``:

    [{"start": 1.2, "end": 3.4, "lines": [[{"t": "Hello", "s": 1.2, "e": 1.5}, ...], ...]}]

and a ``style`` dict (see ``DEFAULT_STYLE``). Coordinates are in ASS PlayRes units,
where the shorter video side is 1080, matching the preview overlay.
"""

from __future__ import annotations

import re

DEFAULT_STYLE = {
    "fontFamily": "Arial",
    "fontSize": 64,
    "bold": True,
    "italic": False,
    "letterSpacing": 0,
    "textColor": "#FFFFFF",
    "textOpacity": 100,
    "outlineColor": "#000000",
    "outlineWidth": 3,
    "shadowColor": "#000000",
    "shadowOpacity": 60,
    "shadowDepth": 2,
    "blur": 0,
    "boxEnabled": False,
    "boxColor": "#000000",
    "boxOpacity": 60,
    "boxPadding": 12,
    "alignment": 2,
    "marginV": 70,
    "marginL": 60,
    "marginR": 60,
    "posX": None,
    "posY": None,
    "highlightMode": "none",  # none | word | fill | reveal
    "highlightColor": "#FFD700",
    "highlightScale": 100,
    "fadeIn": 0,
    "fadeOut": 0,
}


def play_res(width: int, height: int) -> tuple[int, int]:
    k = 1080 / max(1, min(width, height))
    return round(width * k), round(height * k)


def merged_style(style: dict | None) -> dict:
    out = dict(DEFAULT_STYLE)
    for k, v in (style or {}).items():
        if k in out:
            out[k] = v
    return out


# ---------------------------------------------------------------- helpers


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = (color or "#FFFFFF").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return 255, 255, 255


def ass_color(color: str, opacity: float = 100) -> str:
    r, g, b = _hex_rgb(color)
    alpha = round(255 * (1 - max(0.0, min(100.0, float(opacity))) / 100))
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def ass_inline_color(color: str) -> str:
    r, g, b = _hex_rgb(color)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t: float, sep: str = ",") -> str:
    ms = max(0, round(t * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


_NO_SPACE = re.compile(
    "[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def join_words(tokens) -> str:
    """Join word tokens with spaces, except between scripts written without them (CJK, Thai...)."""
    out = ""
    for t in tokens:
        if out and not (_NO_SPACE.match(out[-1]) or _NO_SPACE.match(t[:1] or " ")):
            out += " "
        out += t
    return out


def _join_parts(raw: list[str], rendered: list[str]) -> str:
    """Like join_words, but spacing decided on ``raw`` tokens while emitting ``rendered``."""
    out, prev = "", ""
    for r, piece in zip(raw, rendered):
        if out and not (_NO_SPACE.match(prev[-1:] or " ") or _NO_SPACE.match(r[:1] or " ")):
            out += " "
        out += piece
        prev = r
    return out


def _ass_escape(text: str) -> str:
    return text.replace("\\", "∖").replace("{", "(").replace("}", ")").replace("\n", " ")


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _cue_words(cue: dict) -> list[dict]:
    return [w for line in cue.get("lines", []) for w in line]


def _cue_text(cue: dict, sep: str = "\n") -> str:
    return sep.join(join_words(w.get("t", "") for w in line).strip() for line in cue.get("lines", []))


def _valid_cues(cues: list[dict]) -> list[dict]:
    out = []
    for c in cues or []:
        start, end = _num(c.get("start")), _num(c.get("end"))
        if end <= start or not _cue_text(c).strip():
            continue
        out.append(dict(c, start=start, end=end))
    out.sort(key=lambda c: c["start"])
    return out


# ---------------------------------------------------------------- ASS


def build_ass(cues: list[dict], style: dict, width: int, height: int, title: str = "Subtitles") -> str:
    st = merged_style(style)
    prx, pry = play_res(width, height)
    font = str(st["fontFamily"]).replace(",", " ")
    size = _num(st["fontSize"], 64)
    bold = -1 if st["bold"] else 0
    italic = -1 if st["italic"] else 0
    spacing = _num(st["letterSpacing"])
    align = int(_num(st["alignment"], 2)) or 2
    ml, mr, mv = int(_num(st["marginL"])), int(_num(st["marginR"])), int(_num(st["marginV"]))
    mode = st["highlightMode"]

    primary = ass_color(st["textColor"], st["textOpacity"])
    secondary = primary
    if mode == "fill":
        # \kf sweeps from Secondary (not yet sung) to Primary (sung).
        primary = ass_color(st["highlightColor"], st["textOpacity"])
        secondary = ass_color(st["textColor"], st["textOpacity"])
    outline_w = max(0.0, _num(st["outlineWidth"]))
    outline_c = ass_color(st["outlineColor"], 100 if outline_w > 0 else 0)
    shadow_c = ass_color(st["shadowColor"], st["shadowOpacity"])
    shadow_d = max(0.0, _num(st["shadowDepth"]))

    styles = [
        f"Style: Main,{font},{size:g},{primary},{secondary},{outline_c},{shadow_c},"
        f"{bold},{italic},0,0,100,100,{spacing:g},0,1,{outline_w:g},{shadow_d:g},{align},{ml},{mr},{mv},1"
    ]
    if st["boxEnabled"]:
        box_c = ass_color(st["boxColor"], st["boxOpacity"])
        pad = max(0.0, _num(st["boxPadding"]))
        styles.append(
            f"Style: Box,{font},{size:g},&HFF000000,&HFF000000,{box_c},&HFF000000,"
            f"{bold},{italic},0,0,100,100,{spacing:g},0,3,{pad:g},0,{align},{ml},{mr},{mv},1"
        )

    head = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        f"PlayResX: {prx}",
        f"PlayResY: {pry}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    prefix = ""
    if st["posX"] is not None and st["posY"] is not None:
        prefix += f"\\an{align}\\pos({_num(st['posX']):.0f},{_num(st['posY']):.0f})"
    blur = _num(st["blur"])
    if blur > 0:
        prefix += f"\\blur{blur:g}"
    fade_in, fade_out = int(_num(st["fadeIn"])), int(_num(st["fadeOut"]))
    hl = ass_inline_color(st["highlightColor"])
    scale = _num(st["highlightScale"], 100)
    # \r resets overrides to the event's style; re-apply blur afterwards.
    reset = "{\\r" + (f"\\blur{blur:g}" if blur > 0 else "") + "}"

    events: list[str] = []

    def emit(start: float, end: float, body: str, fin: int, fout: int):
        tags = prefix + (f"\\fad({fin},{fout})" if (fin or fout) else "")
        text = (f"{{{tags}}}" if tags else "") + body
        if st["boxEnabled"]:
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Box,,0,0,0,,{text}")
        events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Main,,0,0,0,,{text}")

    for cue in _valid_cues(cues):
        lines = cue.get("lines", [])
        words = _cue_words(cue)
        timed = all("s" in w and "e" in w for w in words) and words
        if mode in ("word", "reveal") and timed:
            n = len(words)
            for i, w in enumerate(words):
                ws = cue["start"] if i == 0 else max(cue["start"], _num(w["s"]))
                we = cue["end"] if i == n - 1 else min(cue["end"], _num(words[i + 1]["s"]))
                if we - ws < 0.01:
                    continue
                parts_lines, k = [], 0
                for line in lines:
                    parts = []
                    for word in line:
                        t = _ass_escape(word.get("t", ""))
                        if k == i:
                            tag = f"\\1c{hl}"  # newest word: highlight color (word + reveal modes)
                            if scale != 100:
                                tag += f"\\fscx{scale:g}\\fscy{scale:g}"
                            parts.append(f"{{{tag}}}{t}{reset}" if tag else t)
                        elif mode == "reveal" and k > i:
                            parts.append(f"{{\\alpha&HFF&}}{t}{reset}")
                        else:
                            parts.append(t)
                        k += 1
                    parts_lines.append(_join_parts([w.get("t", "") for w in line], parts))
                emit(ws, we, "\\N".join(parts_lines), fade_in if i == 0 else 0, fade_out if i == n - 1 else 0)
        elif mode == "fill" and timed:
            parts_lines, cursor = [], cue["start"]
            flat = words
            k = 0
            for line in lines:
                parts = []
                for word in line:
                    nxt = _num(flat[k + 1]["s"]) if k + 1 < len(flat) else cue["end"]
                    ws = max(cursor, _num(word["s"]))
                    lead = ws - cursor
                    dur = max(0.0, min(nxt, cue["end"]) - ws)
                    t = _ass_escape(word.get("t", ""))
                    lead_tag = f"{{\\k{round(lead * 100)}}}" if lead >= 0.01 else ""
                    parts.append(f"{lead_tag}{{\\kf{max(1, round(dur * 100))}}}{t}")
                    cursor = ws + dur
                    k += 1
                parts_lines.append(_join_parts([w.get("t", "") for w in line], parts))
            emit(cue["start"], cue["end"], "\\N".join(parts_lines), fade_in, fade_out)
        else:
            body = "\\N".join(_ass_escape(join_words(w.get("t", "") for w in line).strip()) for line in lines)
            emit(cue["start"], cue["end"], body, fade_in, fade_out)

    return "\n".join(head + events) + "\n"


# ---------------------------------------------------------------- plain formats


def build_srt(cues: list[dict]) -> str:
    out = []
    for i, c in enumerate(_valid_cues(cues), 1):
        out.append(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{_cue_text(c)}\n")
    return "\n".join(out)


def build_vtt(cues: list[dict]) -> str:
    out = ["WEBVTT", ""]
    for c in _valid_cues(cues):
        out.append(f"{srt_time(c['start'], '.')} --> {srt_time(c['end'], '.')}\n{_cue_text(c)}\n")
    return "\n".join(out)


def build_txt(segments: list[dict]) -> str:
    return "\n".join((s.get("text") or "").strip() for s in segments or [] if (s.get("text") or "").strip()) + "\n"


# ---------------------------------------------------------------- import


def _parse_ts(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


def parse_srt_vtt(text: str) -> list[dict]:
    """Parse SRT or WebVTT into segments (words get interpolated timings)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    blocks = re.split(r"\n\s*\n", text)
    segs = []
    for block in blocks:
        lines = [l for l in block.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if "-->" in line:
                a, b = line.split("-->", 1)
                start, end = _parse_ts(a), _parse_ts(b.strip().split(" ")[0])
                body = " ".join(lines[i + 1 :])
                body = re.sub(r"<[^>]+>", "", body)
                body = re.sub(r"\{[^}]*\}", "", body).strip()
                if body and end > start:
                    segs.append({"start": start, "end": end, "text": body, "words": interpolate_words(body, start, end)})
                break
    return segs


def interpolate_words(text: str, start: float, end: float) -> list[dict]:
    tokens = text.split()
    if not tokens:
        return []
    total = sum(len(t) + 1 for t in tokens)
    dur = max(0.01, end - start)
    out, cursor = [], start
    for t in tokens:
        span = dur * (len(t) + 1) / total
        out.append({"t": t, "s": round(cursor, 3), "e": round(cursor + span, 3)})
        cursor += span
    return out
