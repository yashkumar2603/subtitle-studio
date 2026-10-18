# Subtitle Studio

Automatic, styled subtitles for your videos — fully local, free and open source.

Drop in a video, and Subtitle Studio transcribes the speech with **Whisper large-v3-turbo**
(via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)), lets you fix the text and
design the look in a live preview, then burns the subtitles into the video with ffmpeg/libass.
Nothing is uploaded anywhere: the model and ffmpeg run on your own computer.

Works on **Windows**, **Linux** and **macOS**. Uses an NVIDIA GPU when one is present
(a 5½-minute video transcribes in about 6 s on an RTX 5070 laptop GPU), otherwise the CPU.

## Features

- **Transcription**: Whisper large-v3-turbo by default (also large-v3, medium, small, base,
  distil-large-v3.5); 99 languages with auto-detection; optional *translate to English*;
  a "names / words to expect" hint for correct spelling of names and slang; word-level timestamps.
- **No phantom subtitles on music or noise**: voice-activity detection (Silero VAD) skips
  non-speech audio, and a confidence filter removes the text Whisper tends to invent on
  silence, music and noise ("Thank you for watching!", "♪", repeated lines). Unclear lines are
  flagged ⚠ for a quick check instead of being silently dropped. Normal / Strict / Off levels.
- **Live preview that matches the final render**: subtitles are drawn over the playing video
  in the same coordinate system and font metrics libass uses. **Exact frame** renders the current
  frame through ffmpeg so you can compare.
- **Styling**: any font installed on the system (plus your own .ttf/.otf uploads), size, bold,
  italic, ALL CAPS, letter spacing, text/outline/shadow colors and opacity, edge softness (glow),
  background box, fade in/out, 9-point alignment, margins, or **drag the subtitle anywhere** on the video.
- **12 presets** (Classic, YouTube, Netflix, TikTok Pop, Karaoke, Word Reveal, Yellow DVD,
  Boxed Yellow, Minimal, Neon, Cinema, Top Caption), plus your own presets that you can save,
  export and import (e.g. share them with friends).
- **Word effects**: highlight the spoken word (with "pop" scaling), karaoke fill, or reveal word by word.
- **Text wrapping**: wrap by screen width (measured with the real font) or by characters,
  max lines, max words per subtitle (TikTok style), start a new subtitle or add lines when text
  doesn't fit, balanced line lengths, new subtitle per sentence, split at pauses, min/max duration,
  close small gaps, remove commas and periods.
- **Editor**: edit text and timings, split at the cursor, merge, delete, add lines, find & replace,
  shift all timings (fix sync), undo/redo, autosave, click-to-seek timeline. Import existing .srt/.vtt.
- **Export**: burned-in MP4/MKV (CPU x264 or GPU NVENC / QuickSync / AMF when available; quality,
  speed and resolution options), a 10-second **test render** from the playhead, soft subtitle tracks
  (MP4 text track, or MKV with full ASS styling and embedded fonts — no re-encode), and SRT / VTT /
  styled ASS / plain-text transcript files.
- Videos the browser can't play (e.g. HEVC from iPhones) get an automatic 720p preview proxy;
  the final render always uses the original file.

## Quick start

### Windows

1. Download this repository (**Code → Download ZIP**) and unzip it anywhere.
2. Double-click **`run.bat`**.

The first run installs [uv](https://docs.astral.sh/uv/) (a small Python manager) into your user
folder and downloads the app's components (about 1–2 GB with an NVIDIA GPU, less without).
The first transcription also downloads the Whisper model (1.6 GB). After that it starts in seconds.
Your browser opens at `http://localhost:7860`. Close the black window to stop the app.

### Linux / macOS

```bash
./run.sh
```

### Manual

```bash
uv sync                 # add  --extra gpu  on machines with an NVIDIA GPU
uv run python -m app    # options: --port 7860  --no-browser  --host 127.0.0.1
```

## Using it

1. **Open a video**: drag it onto the page, or paste its path (faster: no copy is made).
2. **Transcribe** (right panel). Turbo on a GPU takes seconds; on a CPU expect roughly real time,
   or pick *Small* for speed.
3. Fix any lines marked ⚠ in the list below the video. Click a line to jump there.
4. **Style**: pick a preset, then adjust font, colors, size and position. Drag the subtitle on
   the video to place it. Press **Exact frame** to see precisely what the render will look like.
5. **Wrapping**: control line length, number of lines and timing.
6. **Export**: try **Test 10 s** first, then **Render video**. The file is saved next to the
   original video, e.g. `My video (subtitled).mp4`.

Keyboard: `Space` play/pause · `←`/`→` seek 2 s (`Shift` = 5 s) · `Ctrl+Z` / `Ctrl+Y` undo/redo · `Ctrl+S` save.

## How it works

```
browser (app/static)                         server (app/, FastAPI on 127.0.0.1)
────────────────────                         ────────────────────────────────────
layout.js  words ➜ cues (wrapping, timing)   transcribe.py  faster-whisper + VAD + filters
app.js     live preview, editor, controls ──▶ subs.py        cues + style ➜ ASS / SRT / VTT
presets.js built-in styles                   media.py       ffmpeg: probe, frame, burn, mux
                                             fonts.py       system font scan (fontTools)
                                             projects.py    per-video JSON in data/projects
```

- **One layout engine.** Line wrapping and cue splitting run in the browser (`layout.js`), measured
  with the selected font on a canvas. The resulting cues drive the live preview *and* are sent to
  the server to build the ASS file, so the preview and the render can't disagree about line breaks.
- **Same coordinates.** ASS `PlayRes` is set so the video's short side is 1080 units; the preview
  overlay uses the same units and is scaled with CSS. Font sizes are converted with each font's
  `unitsPerEm / (winAscent + winDescent)` ratio, which is how libass sizes text.
- **Fonts.** Families are grouped by their name-table family (what libass matches). For rendering,
  the chosen family's files are copied into a temporary `fontsdir`, so burn-in works the same on
  every OS without relying on fontconfig.
- **ffmpeg** comes from `imageio-ffmpeg` (static builds with libass on Windows, Linux and macOS).
  Set `FFMPEG_BINARY` to use your own.
- **GPU.** The optional `gpu` extra installs cuBLAS and cuDNN wheels; `cuda_libs.py` makes them
  loadable (DLL directories on Windows, preloading on Linux). If CUDA fails, transcription falls back
  to the CPU automatically.

### Non-speech guard details

Whisper is trained on captioned web video, so it "hears" outros and captions in music and noise.
Defences, in order:

1. Silero VAD (threshold 0.5, 400 ms padding — tighter padding made Whisper drop punctuation).
2. Whisper's own no-speech / log-probability / compression-ratio checks, plus
   `hallucination_silence_threshold=2` and language detection over 4 windows.
3. `clean_segments()`: removes `[Music]`/`♪` tags, segments with collapsed word confidence,
   no-speech + low log-prob windows, decoding loops, known phantom phrases *only when they also
   look unreliable*, and 3× repeated lines; splits segments at pauses over 2 s; flags lines with
   mean word probability under 0.6 as low confidence.

Tested on pink/brown noise, sine tones and silence (no output; raw Whisper produced "Thank you."),
a music montage (phantom line removed) and speech surrounded by noise (speech kept intact).

## Data

Everything stays in the `data/` folder next to the app: project files (transcript, style, layout),
uploaded videos, presets, added fonts and the font cache. Delete `data/` to reset.

Whisper models are downloaded once into the Hugging Face cache and reused on every start, even
offline and after updating or re-downloading the app:

- Windows: `%USERPROFILE%\.cache\huggingface\hub`
- Linux / macOS: `~/.cache/huggingface/hub`
- set `HF_HOME` to put them elsewhere (e.g. another drive)

Downloaded models are marked "✓ downloaded" in the model list. If a download is interrupted
(app closed mid-download), the next transcription resumes it.

## Stopping

Press `Ctrl+C` or close the terminal window. The app stops immediately: running renders and
transcriptions are cancelled, ffmpeg is stopped, unfinished output files are removed, and all
memory (including GPU memory) is released. Your projects are saved continuously, so nothing is lost.

## Tests

```bash
uv run --with pytest pytest -q tests      # Python: ASS/SRT writers, non-speech filter
node --test tests/*.test.mjs             # JS: layout engine (wrapping, timing)
```

## License

MIT. Whisper models are MIT-licensed by OpenAI; faster-whisper is MIT; ffmpeg builds are GPL.
