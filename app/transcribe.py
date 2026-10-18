"""Speech-to-text with faster-whisper (open-source Whisper on CTranslate2).

Default model is ``large-v3-turbo``: near large-v3 accuracy at several times the
speed, multilingual, with word-level timestamps. GPU (CUDA) is used when
available, otherwise CPU int8.
"""

from __future__ import annotations

import gc
import os
import re
import threading
import time
from pathlib import Path

from . import cuda_libs
from .jobs import Job
from .subs import join_words

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
# The UI shows its own download progress; keep the terminal clean.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

MODELS = [
    {"id": "large-v3-turbo", "label": "Large v3 Turbo (recommended)", "size": "1.6 GB"},
    {"id": "large-v3", "label": "Large v3 (most accurate, slower)", "size": "3.1 GB"},
    {"id": "medium", "label": "Medium", "size": "1.5 GB"},
    {"id": "small", "label": "Small (fast, OK for weak PCs)", "size": "480 MB"},
    {"id": "base", "label": "Base (very fast, rough)", "size": "145 MB"},
    {"id": "distil-large-v3.5", "label": "Distil Large v3.5 (English only, fast)", "size": "1.5 GB"},
]

ALLOW_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]

# --- Non-speech guards --------------------------------------------------------
# Whisper was trained on captioned web video, so on music, wind, crowd noise or
# silence it tends to invent text ("Thank you for watching!", "♪", repeats).
# Defence in depth: Silero VAD drops non-speech audio before decoding, Whisper's
# own no-speech / log-prob / compression checks reject bad windows, and
# clean_segments() removes what still slips through.

NOISE_LEVELS = {
    #          VAD threshold, min speech ms, min word prob, drop if no_speech_prob >
    "off": None,
    "normal": {"vad": 0.5, "min_speech_ms": 250, "min_word_p": 0.35, "nsp": 0.6},
    "strict": {"vad": 0.65, "min_speech_ms": 400, "min_word_p": 0.5, "nsp": 0.45},
}

# Phrases Whisper commonly hallucinates on non-speech audio. Only removed when the
# segment also looks unreliable, so a real "thank you" or "subscribe" survives.
PHANTOM_PHRASES = {
    "thank you", "thank you so much", "thanks for watching", "thank you for watching",
    "thank you very much", "thanks for watching and see you next time", "please subscribe",
    "please like and subscribe", "subscribe to my channel", "see you next time", "see you in the next video",
    "bye", "bye bye", "you", "oh", "uh", "um", "hmm", "so", "okay", "the end",
    "subtitles by the amaraorg community", "subtitles by", "transcribed by", "translated by",
    "i'm going to go ahead and", "music", "applause", "laughter",
}

_TAG_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|[♪♫♬🎵🎶]+")


def _norm(text: str) -> str:
    return re.sub(r"[^\w' ]+", "", text.lower()).strip()


def _strip_tags(text: str) -> str:
    """Remove non-speech annotations such as [Music], (applause), *laughs*, ♪."""
    return re.sub(r"\s{2,}", " ", _TAG_RE.sub(" ", text)).strip()


_I_RE = re.compile(r"\bi(?=\b|'(?:m|d|ll|ve|s)\b)")


def fix_english_casing(segments: list[dict]) -> None:
    """Safety net for windows Whisper decodes in all-lowercase: capitalise "i",
    "i'm"... and the first word after sentence-ending punctuation."""
    prev_end = "."
    for seg in segments:
        for w in seg["words"]:
            t = _I_RE.sub("I", w["t"])
            if prev_end in ".?!" and t[:1].islower():
                t = t[:1].upper() + t[1:]
            w["t"] = t
            prev_end = t[-1:] if t else prev_end
        seg["text"] = join_words(w["t"] for w in seg["words"])


def split_on_gaps(raw: list[dict], gap: float = 2.0) -> list[dict]:
    """VAD can glue speech chunks far apart into one segment; split at long pauses."""
    out = []
    for seg in raw:
        words = seg["words"]
        start = 0
        for i in range(1, len(words) + 1):
            if i == len(words) or words[i]["s"] - words[i - 1]["e"] > gap:
                part = words[start:i]
                if part:
                    out.append(dict(seg, words=part, text=join_words(w["t"] for w in part) if len(part) != len(words) else seg["text"],
                                    start=part[0]["s"], end=part[-1]["e"]))
                start = i
        if not words:
            out.append(seg)
    return out


def merge_subwords(words: list) -> list[dict]:
    """Whisper marks word starts with a leading space; tokens without one (e.g. "-manage",
    ".5") continue the previous word. Scripts without spaces (CJK) are left as-is."""
    spaced = sum(1 for w in words if w.word.startswith(" "))
    space_lang = spaced >= max(1, len(words) // 2)
    out: list[dict] = []
    for w in words:
        t = w.word.strip()
        if not t:
            continue
        if space_lang and out and not w.word.startswith(" "):
            prev = out[-1]
            prev["t"] += t
            prev["e"] = round(w.end, 3)
            prev["p"] = min(prev["p"], w.probability)
            continue
        out.append({"t": t, "s": round(w.start, 3), "e": round(w.end, 3), "p": w.probability})
    return out


def clean_segments(raw: list[dict], level: str = "normal") -> tuple[list[dict], int]:
    """Drop hallucinated / non-speech segments; flag shaky ones as ``lowConf``.

    ``raw`` items carry Whisper diagnostics: ``nsp`` (no-speech prob), ``lp``
    (avg log-prob), ``cr`` (compression ratio) and per-word ``p``.
    """
    cfg = NOISE_LEVELS.get(level)
    raw = split_on_gaps(raw)
    out: list[dict] = []
    removed = 0
    for i, seg in enumerate(raw):
        words = [w for w in seg["words"] if _strip_tags(w["t"])]
        for w in words:
            w["t"] = _strip_tags(w["t"])
        text = _strip_tags(seg["text"])
        norm = _norm(text)
        if not norm or not words:
            removed += 1
            continue
        if cfg is None:
            out.append(dict(seg, text=text, words=words))
            continue
        wp = sum(w["p"] for w in words) / len(words)
        bad = False
        # Whisper itself thinks this window is silence and the decode was weak.
        if seg["nsp"] > cfg["nsp"] and seg["lp"] < -0.6:
            bad = True
        # Word-level confidence collapsed: typical of noise / music.
        elif wp < cfg["min_word_p"]:
            bad = True
        # Highly repetitive text = decoding loop.
        elif seg["cr"] > 2.6:
            bad = True
        # Known phantom phrase that also looks unreliable or isolated.
        elif norm in PHANTOM_PHRASES:
            prev_gap = seg["start"] - raw[i - 1]["end"] if i > 0 else 99
            next_gap = raw[i + 1]["start"] - seg["end"] if i + 1 < len(raw) else 99
            if wp < 0.75 or seg["nsp"] > 0.3 or (prev_gap > 3 and next_gap > 3 and wp < 0.9):
                bad = True
        # Same text repeated 3+ times in a row = loop; keep the first.
        if not bad and len(out) >= 2 and _norm(out[-1]["text"]) == norm == _norm(out[-2]["text"]):
            bad = True
        if bad:
            removed += 1
            continue
        seg = dict(seg, text=text, words=words, start=words[0]["s"], end=max(words[-1]["e"], words[0]["s"] + 0.05))
        if wp < 0.6:
            seg["lowConf"] = True
        out.append(seg)
    # Public shape: drop diagnostics, keep word probability (UI shows shaky words).
    for seg in out:
        for k in ("nsp", "lp", "cr"):
            seg.pop(k, None)
        for w in seg["words"]:
            w["p"] = round(w["p"], 2)
    return out, removed


_model_lock = threading.Lock()
_loaded: dict = {"key": None, "model": None}


def device_info() -> dict:
    cuda = cuda_libs.cuda_available()
    name = None
    if cuda:
        try:
            import subprocess

            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000 if os.name == "nt" else 0,
            ).stdout.strip()
            name = out.splitlines()[0] if out else None
        except Exception:
            pass
    return {"cuda": cuda, "gpuName": name}


def _repo(model: str) -> str:
    from faster_whisper.utils import _MODELS

    return _MODELS.get(model, model)


def cached_model_path(model: str) -> str | None:
    """Local folder of a fully downloaded model, or None.

    Models live in the Hugging Face cache (``~/.cache/huggingface/hub`` on Linux and
    macOS, ``%USERPROFILE%\\.cache\\huggingface\\hub`` on Windows; ``HF_HOME`` overrides),
    so they survive app restarts and app updates and are found offline. A download
    interrupted by closing the app leaves a snapshot without ``model.bin``; treat that
    as missing so the next run resumes it instead of failing to load.
    """
    from huggingface_hub import snapshot_download

    try:
        path = snapshot_download(_repo(model), allow_patterns=ALLOW_PATTERNS, local_files_only=True)
    except Exception:
        return None
    p = Path(path)
    if (p / "model.bin").is_file() and (p / "config.json").is_file():
        return path
    return None


def models_status() -> list[dict]:
    return [dict(m, downloaded=cached_model_path(m["id"]) is not None) for m in MODELS]


def _ensure_downloaded(model: str, job: Job) -> str:
    """Download model files (with byte progress) and return the local folder."""
    from huggingface_hub import HfApi, constants, snapshot_download

    repo = _repo(model)
    cached = cached_model_path(model)
    if cached:
        return cached

    job.update(-1, f"Downloading model {model} (first time only)…")
    total = 0
    try:
        info = HfApi().model_info(repo, files_metadata=True)
        import fnmatch

        for s in info.siblings or []:
            if any(fnmatch.fnmatch(s.rfilename, p) for p in ALLOW_PATTERNS):
                total += s.size or 0
    except Exception:
        total = 0

    blobs = Path(constants.HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "blobs"

    def on_disk() -> int:
        try:
            return sum(f.stat().st_size for f in blobs.iterdir() if f.is_file())
        except OSError:
            return 0

    start_bytes = on_disk()
    result: dict = {}

    def dl():
        try:
            result["path"] = snapshot_download(repo, allow_patterns=ALLOW_PATTERNS)
        except Exception as e:  # pragma: no cover - network errors
            result["error"] = e

    t = threading.Thread(target=dl, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.5)
        if total:
            got = max(0, on_disk() - start_bytes)
            frac = min(0.99, got / total)
            job.update(0.0, f"Downloading model {model}… {got / 1e6:.0f} / {total / 1e6:.0f} MB")
            job.progress = -1 if frac <= 0 else frac * 0.999
        else:
            job.check()
    if "error" in result:
        raise RuntimeError(f"Model download failed: {result['error']}")
    return result["path"]


def _load(model: str, device: str, job: Job):
    from faster_whisper import WhisperModel

    compute = "float16" if device == "cuda" else "int8"
    key = (model, device, compute)
    with _model_lock:
        if _loaded["key"] == key:
            return _loaded["model"]
        # Free the previous model first (VRAM is limited).
        _loaded["model"] = None
        _loaded["key"] = None
        gc.collect()
        path = _ensure_downloaded(model, job)
        job.update(-1, f"Loading {model} on {device.upper()}…")
        cpu_threads = max(1, (os.cpu_count() or 4) - 1)
        m = WhisperModel(path, device=device, compute_type=compute, cpu_threads=cpu_threads)
        _loaded["model"], _loaded["key"] = m, key
        return m


def transcribe(job: Job, media_path: str, duration: float, settings: dict) -> dict:
    model_name = settings.get("model") or "large-v3-turbo"
    language = settings.get("language") or None
    if language == "auto":
        language = None
    task = "translate" if settings.get("task") == "translate" else "transcribe"
    want = settings.get("device") or "auto"
    vad = bool(settings.get("vad", True))
    level = settings.get("noiseFilter") or "normal"
    if level not in NOISE_LEVELS:
        level = "normal"
    guard = NOISE_LEVELS[level] or NOISE_LEVELS["normal"]
    vad_params = {
        "threshold": guard["vad"],
        "min_speech_duration_ms": guard["min_speech_ms"],
        "min_silence_duration_ms": 500,
        # 400 ms padding: tighter cuts make Whisper drop casing/punctuation.
        "speech_pad_ms": 400,
    }
    prompt = (settings.get("initialPrompt") or "").strip() or None

    cuda_libs.setup()
    device = "cuda" if (want in ("auto", "cuda") and cuda_libs.cuda_available()) else "cpu"
    notes = []
    if task == "translate" and ("turbo" in model_name or model_name.startswith("distil")):
        # Turbo/distil were not trained to translate; use a model that was.
        model_name = "large-v3" if device == "cuda" else "medium"
        notes.append(f"Translation uses {model_name} (turbo models cannot translate).")

    def run(dev: str):
        model = _load(model_name, dev, job)
        job.update(0.0, "Transcribing…")
        kwargs = dict(
            language=language,
            task=task,
            word_timestamps=True,
            vad_filter=vad,
            initial_prompt=prompt,
            beam_size=5,
            vad_parameters=vad_params if vad else None,
            # Look at several windows before deciding the language, so an intro
            # of music or noise does not lock in a wrong one.
            language_detection_segments=4,
        )
        if dev == "cuda" and settings.get("batched", False):
            from faster_whisper import BatchedInferencePipeline

            pipe = BatchedInferencePipeline(model=model)
            segs, info = pipe.transcribe(media_path, batch_size=8, **kwargs)
        else:
            segs, info = model.transcribe(
                media_path,
                # Keeps punctuation/casing consistent across 30 s windows; VAD guards against loops.
                condition_on_previous_text=True,
                # Skip silent gaps > 2 s around suspected hallucinations.
                hallucination_silence_threshold=2.0,
                **kwargs,
            )
        out = []
        t0 = time.time()
        total = duration or info.duration or 1
        for s in segs:
            job.check()
            words = merge_subwords(s.words or [])
            text = s.text.strip()
            if not text:
                continue
            out.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": text, "words": words,
                        "nsp": s.no_speech_prob, "lp": s.avg_logprob, "cr": s.compression_ratio})
            frac = min(0.999, s.end / total)
            elapsed = time.time() - t0
            eta = elapsed / frac - elapsed if frac > 0.02 else None
            job.update(frac, f"Transcribing… {_fmt(s.end)} / {_fmt(total)}" + (f" · ~{_fmt(eta)} left" if eta else ""))
        return out, info

    try:
        segments, info = run(device)
    except Exception as e:
        if device != "cuda" or job.cancelled:
            raise
        notes.append(f"GPU failed ({e.__class__.__name__}: {str(e)[:120]}); used CPU instead.")
        segments, info = run("cpu")
        device = "cpu"

    segments, removed = clean_segments(segments, level)
    if (info.language or "") == "en":
        fix_english_casing(segments)
    if removed:
        notes.append(f"Filtered {removed} non-speech / hallucinated segment{'s' if removed != 1 else ''}.")
    if not segments:
        notes.append("No speech detected.")

    return {
        "segments": segments,
        "language": info.language,
        "languageProbability": round(info.language_probability or 0, 3),
        "model": model_name,
        "device": device,
        "task": task,
        "notes": notes,
    }


def _fmt(sec: float | None) -> str:
    if sec is None:
        return "?"
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"
