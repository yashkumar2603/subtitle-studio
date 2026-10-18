import {
  DEFAULT_LAYOUT, buildCues, joinWords, needsSpace, interpolateWords, retimeWords, rescaleWords,
} from "./layout.js";
import { BUILTIN_PRESETS, SWATCHES } from "./presets.js";

// ------------------------------------------------------------------ helpers

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const debounce = (fn, ms) => {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
};
const clone = (o) => JSON.parse(JSON.stringify(o));

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    init.body = JSON.stringify(opts.json);
    delete init.json;
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 4000);
}

function fmtTime(t, cs = true) {
  t = Math.max(0, t || 0);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  const ss = cs ? s.toFixed(2).padStart(5, "0") : String(Math.floor(s)).padStart(2, "0");
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${ss}`;
}

function parseTime(str) {
  const parts = String(str).trim().replace(",", ".").split(":").map(Number);
  if (parts.some((x) => Number.isNaN(x))) return null;
  return parts.reduce((acc, x) => acc * 60 + x, 0);
}

function rgba(hex, opacity = 100) {
  const c = (hex || "#ffffff").replace("#", "");
  const n = parseInt(c.length === 3 ? c.replace(/./g, "$&$&") : c, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${clamp(opacity, 0, 100) / 100})`;
}

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ------------------------------------------------------------------ state

const S = {
  system: null,
  fonts: [],
  fontMap: new Map(),
  loadedFonts: new Set(),
  project: null,
  segments: [],
  style: null,
  layout: null,
  userPresets: [],
  activePreset: null,
  cues: [],
  undo: [],
  redo: [],
  exportMode: "burn",
  scale: 1,
  playRes: [1920, 1080],
};

const video = $("#video");
const layer = $("#subsLayer");

// ------------------------------------------------------------------ init

async function init() {
  bindStatic();
  try {
    S.system = await api("/api/system");
  } catch (e) {
    toast("Server not reachable: " + e.message, "error");
    return;
  }
  updateDevicePill();
  if (S.system.cuda === undefined || S.system.cuda === null) setTimeout(refreshSystem, 2500);
  fillTranscribeOptions();
  fillEncoders();
  const [fonts, presets] = await Promise.all([api("/api/fonts").catch(() => []), api("/api/presets").catch(() => [])]);
  setFonts(fonts);
  S.userPresets = Array.isArray(presets) ? presets : [];
  const m = location.hash.match(/p=([a-f0-9]+)/);
  if (m) {
    try {
      const r = await api(`/api/project/${m[1]}`);
      return loadProject(r.project, r.proxyJob);
    } catch {}
  }
  showWelcome();
}

async function refreshSystem() {
  try {
    S.system = await api("/api/system");
    updateDevicePill();
    fillEncoders();
    if (S.system.cuda === undefined || S.system.cuda === null) setTimeout(refreshSystem, 2500);
  } catch {}
}

function updateDevicePill() {
  const pill = $("#devicePill");
  if (S.system.cuda) {
    pill.textContent = `GPU · ${(S.system.gpuName || "CUDA").replace(/NVIDIA GeForce /, "")}`;
    pill.classList.add("gpu");
  } else if (S.system.cuda === false) {
    pill.textContent = "CPU mode";
    pill.classList.remove("gpu");
  } else pill.textContent = "Detecting…";
}

function setFonts(list) {
  S.fonts = list;
  S.fontMap = new Map(list.map((f) => [f.family, f]));
  S.fontMapLower = new Map(list.map((f) => [f.family.toLowerCase(), f.family]));
}

function resolveFont(candidates) {
  for (const c of candidates || []) {
    const hit = S.fontMapLower.get(c.toLowerCase());
    if (hit) return hit;
  }
  return S.fonts[0]?.family || "Arial";
}

// ------------------------------------------------------------------ welcome

async function showWelcome() {
  $("#welcome").classList.remove("hidden");
  $("#editor").classList.add("hidden");
  $("#projectName").textContent = "";
  history.replaceState(null, "", location.pathname);
  video.pause();
  try {
    const recent = await api("/api/projects");
    const ul = $("#recentList");
    ul.innerHTML = "";
    $("#recentWrap").classList.toggle("hidden", !recent.length);
    for (const p of recent) {
      const li = document.createElement("li");
      li.className = p.exists ? "" : "missing";
      li.innerHTML = `<span class="name"></span><span class="path"></span><span class="muted small"></span>`;
      li.children[0].textContent = p.name;
      li.children[1].textContent = p.videoPath;
      li.children[2].textContent = p.exists ? `${p.segments} lines` : "file missing";
      li.onclick = async () => {
        if (!p.exists) return toast("That video file no longer exists.", "error");
        const r = await api(`/api/project/${p.id}`);
        loadProject(r.project, r.proxyJob);
      };
      ul.appendChild(li);
    }
  } catch {}
}

function uploadFile(file) {
  const prog = $("#uploadProgress");
  prog.classList.remove("hidden");
  const bar = $("i", prog);
  const label = $("span", prog);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      bar.style.width = `${(e.loaded / e.total) * 100}%`;
      label.textContent = `${Math.round((e.loaded / e.total) * 100)}%`;
    }
  };
  xhr.onload = () => {
    prog.classList.add("hidden");
    if (xhr.status >= 200 && xhr.status < 300) {
      const r = JSON.parse(xhr.responseText);
      loadProject(r.project, r.proxyJob);
    } else {
      let msg = xhr.statusText;
      try { msg = JSON.parse(xhr.responseText).detail; } catch {}
      toast("Upload failed: " + msg, "error");
    }
  };
  xhr.onerror = () => {
    prog.classList.add("hidden");
    toast("Upload failed", "error");
  };
  const fd = new FormData();
  fd.append("file", file);
  xhr.send(fd);
}

// ------------------------------------------------------------------ project

function defaultStyle() {
  const p = BUILTIN_PRESETS[0];
  return { ...S.system.defaultStyle, ...p.style, fontFamily: resolveFont(p.fonts) };
}

async function loadProject(project, proxyJob) {
  S.project = project;
  S.segments = project.segments || [];
  S.style = project.style ? { ...S.system.defaultStyle, ...project.style } : defaultStyle();
  if (!S.fontMap.has(S.style.fontFamily)) S.style.fontFamily = resolveFont([S.style.fontFamily, ...BUILTIN_PRESETS[0].fonts]);
  S.layout = { ...DEFAULT_LAYOUT, ...(project.layout || {}) };
  S.activePreset = project.style ? null : BUILTIN_PRESETS[0].name;
  S.undo = [];
  S.redo = [];
  S.playRes = project.playRes;
  $("#welcome").classList.add("hidden");
  $("#editor").classList.remove("hidden");
  $("#projectName").textContent = project.name;
  history.replaceState(null, "", `#p=${project.id}`);
  $("#eOutDir").placeholder = project.videoPath.replace(/[\\/][^\\/]*$/, "");
  $("#rResult").classList.add("hidden");
  $("#tNotes").innerHTML = "";
  if (project.transcribeMeta) showTranscribeNotes(project.transcribeMeta, false);

  if (proxyJob) {
    showStageMsg("Preparing a browser-friendly preview (this video's format can't play in the browser directly)…", true);
    await pollJob(proxyJob, (j) => setStageProgress(j));
    hideStageMsg();
  }
  video.src = `/api/project/${project.id}/media?v=${project.updated || 0}`;
  fitStage();
  syncControls();
  renderPresets();
  renderFontButton();
  await ensureFont(S.style.fontFamily);
  recompute();
  renderSegments();
  if (!S.segments.length) switchTab("transcribe");
}

const saveNow = async () => {
  if (!S.project) return;
  $("#saveState").textContent = "Saving…";
  try {
    await api(`/api/project/${S.project.id}`, {
      method: "PUT",
      json: { segments: S.segments, style: S.style, layout: S.layout },
    });
    $("#saveState").textContent = "Saved";
  } catch (e) {
    $("#saveState").textContent = "Not saved!";
    toast("Autosave failed: " + e.message, "error");
  }
};
const scheduleSave = debounce(saveNow, 700);

function snapshot() {
  return JSON.stringify({ segments: S.segments, style: S.style, layout: S.layout });
}
let lastPush = { key: null, time: 0 };
function pushUndo(key = null) {
  const now = Date.now();
  if (key && lastPush.key === key && now - lastPush.time < 1500) {
    lastPush.time = now;
    return;
  }
  lastPush = { key, time: now };
  S.undo.push(snapshot());
  if (S.undo.length > 200) S.undo.shift();
  S.redo = [];
}
function restore(snap) {
  const o = JSON.parse(snap);
  S.segments = o.segments;
  S.style = o.style;
  S.layout = o.layout;
  syncControls();
  renderFontButton();
  ensureFont(S.style.fontFamily).then(recompute);
  renderSegments();
  scheduleSave();
}
function undo() {
  if (!S.undo.length) return;
  S.redo.push(snapshot());
  restore(S.undo.pop());
  lastPush = { key: null, time: 0 };
}
function redo() {
  if (!S.redo.length) return;
  S.undo.push(snapshot());
  restore(S.redo.pop());
}

// ------------------------------------------------------------------ fonts

function fontInfo(family) {
  return S.fontMap.get(family);
}

function fontRatio() {
  const f = fontInfo(S.style.fontFamily);
  if (!f) return 0.85;
  const key = S.style.bold && S.style.italic ? "bolditalic" : S.style.bold ? "bold" : S.style.italic ? "italic" : "regular";
  return (f.styles[key] || f.styles.regular)?.ratio || 0.85;
}

async function ensureFont(family) {
  const f = fontInfo(family);
  if (!f || S.loadedFonts.has(family)) return;
  S.loadedFonts.add(family);
  const faces = [];
  for (const [key, st] of Object.entries(f.styles)) {
    if (!st.web) continue;
    const url = `/api/fonts/file?family=${encodeURIComponent(family)}&style=${key}`;
    const desc = {
      weight: key.startsWith("bold") ? "700" : "400",
      style: key.includes("italic") ? "italic" : "normal",
    };
    const face = new FontFace(family, `url("${url}")`, desc);
    document.fonts.add(face);
    faces.push(face.load().catch(() => null));
  }
  await Promise.all(faces);
}

function renderFontButton() {
  const b = $("#fontCurrent");
  b.textContent = S.style.fontFamily;
  b.style.fontFamily = `"${S.style.fontFamily}", sans-serif`;
}

function renderFontList() {
  const q = $("#fontSearch").value.trim().toLowerCase();
  const list = $("#fontList");
  list.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const f of S.fonts) {
    if (q && !f.family.toLowerCase().includes(q)) continue;
    const el = document.createElement("div");
    el.className = "font-item" + (f.family === S.style.fontFamily ? " sel" : "");
    el.style.fontFamily = `"${f.family}", sans-serif`;
    el.textContent = f.family;
    const n = Object.keys(f.styles).length;
    const small = document.createElement("small");
    small.textContent = (f.user ? "added · " : "") + (n > 1 ? `${n} styles` : "");
    el.appendChild(small);
    el.onclick = () => selectFont(f.family);
    frag.appendChild(el);
  }
  list.appendChild(frag);
}

async function selectFont(family) {
  pushUndo();
  S.style.fontFamily = family;
  S.activePreset = null;
  $("#fontPop").classList.add("hidden");
  renderFontButton();
  renderPresets();
  await ensureFont(family);
  recompute();
  scheduleSave();
}

// ------------------------------------------------------------------ measuring + cues

const measureCtx = document.createElement("canvas").getContext("2d");

function makeMeasure() {
  const st = S.style;
  if (S.layout.wrapBy === "chars") {
    return { measure: (t) => [...t].length, limit: S.layout.maxChars };
  }
  const size = st.fontSize * fontRatio();
  measureCtx.font = `${st.italic ? "italic " : ""}${st.bold ? 700 : 400} ${size}px "${st.fontFamily}", sans-serif`;
  if ("letterSpacing" in measureCtx) measureCtx.letterSpacing = `${st.letterSpacing || 0}px`;
  const [prx] = S.playRes;
  const avail = prx - st.marginL - st.marginR;
  const limit = Math.max(50, Math.min(avail, (prx * S.layout.maxWidthPct) / 100) - 2 * (st.outlineWidth || 0));
  const cache = new Map();
  return {
    measure: (t) => {
      let w = cache.get(t);
      if (w === undefined) {
        w = measureCtx.measureText(t).width;
        cache.set(t, w);
      }
      return w;
    },
    limit,
  };
}

let recomputeQueued = false;
function recompute() {
  if (recomputeQueued) return;
  recomputeQueued = true;
  requestAnimationFrame(() => {
    recomputeQueued = false;
    if (!S.project) return;
    const { measure, limit } = makeMeasure();
    S.cues = buildCues(S.segments, S.layout, measure, limit);
    renderTimeline();
    lastOverlayKey = null;
    renderOverlay();
    const lines = S.cues.reduce((a, c) => a + c.lines.length, 0);
    $("#layoutStats").textContent = S.cues.length
      ? `${S.cues.length} subtitles · ${lines} lines · avg ${(S.cues.reduce((a, c) => a + c.end - c.start, 0) / S.cues.length).toFixed(1)} s on screen`
      : "";
  });
}

function cuesForServer() {
  return S.cues.map((c) => ({ start: c.start, end: c.end, lines: c.lines }));
}

// ------------------------------------------------------------------ stage + overlay

function fitStage() {
  const wrap = $("#stageWrap");
  const [prx, pry] = S.playRes;
  const W = wrap.clientWidth;
  const H = wrap.clientHeight;
  if (!W || !H) return;
  const aspect = prx / pry;
  let w = W;
  let h = W / aspect;
  if (h > H) {
    h = H;
    w = H * aspect;
  }
  const stage = $("#stage");
  stage.style.width = `${w}px`;
  stage.style.height = `${h}px`;
  S.scale = w / prx;
  layer.style.width = `${prx}px`;
  layer.style.height = `${pry}px`;
  layer.style.transform = `scale(${S.scale})`;
}

function showStageMsg(text, progress = false) {
  const el = $("#stageMsg");
  el.classList.remove("hidden");
  el.innerHTML = `<div></div>${progress ? '<div class="bar indeterminate"><i></i></div>' : ""}`;
  el.firstChild.textContent = text;
}
function setStageProgress(job) {
  const bar = $("#stageMsg .bar");
  if (!bar) return;
  bar.classList.toggle("indeterminate", job.progress < 0);
  $("i", bar).style.width = `${Math.max(0, job.progress) * 100}%`;
}
function hideStageMsg() {
  $("#stageMsg").classList.add("hidden");
}

let lastOverlayKey = null;

function activeCueIndex(t) {
  // Binary search for the last cue starting at or before t.
  let lo = 0;
  let hi = S.cues.length - 1;
  let idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (S.cues[mid].start <= t) {
      idx = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (idx >= 0 && t < S.cues[idx].end) return idx;
  return -1;
}

function renderOverlay() {
  if (!S.project || S.dragging) return;
  const t = video.currentTime;
  const idx = activeCueIndex(t);
  const st = S.style;
  let wordIdx = -1;
  let opacity = 1;
  if (idx >= 0) {
    const cue = S.cues[idx];
    const words = cue.lines.flat();
    for (let i = 0; i < words.length; i++) if (words[i].s <= t) wordIdx = i;
    if (st.highlightMode === "word" || st.highlightMode === "reveal") wordIdx = Math.max(0, wordIdx);
    const fin = (st.fadeIn || 0) / 1000;
    const fout = (st.fadeOut || 0) / 1000;
    if (fin > 0) opacity = Math.min(opacity, (t - cue.start) / fin);
    if (fout > 0) opacity = Math.min(opacity, (cue.end - t) / fout);
    opacity = clamp(opacity, 0, 1);
  }
  const key = `${idx}:${st.highlightMode === "none" ? 0 : wordIdx}`;
  if (key !== lastOverlayKey) {
    lastOverlayKey = key;
    layer.innerHTML = "";
    if (idx >= 0) layer.appendChild(buildCueEl(S.cues[idx], wordIdx));
  }
  const el = layer.firstElementChild;
  if (el) (el.querySelector(".sub-cue-wrap, .sub-cue") || el).style.opacity = opacity;
}

function textCss(st) {
  const size = st.fontSize * fontRatio();
  const shadows = [];
  if (st.shadowDepth > 0) {
    shadows.push(`${st.shadowDepth}px ${st.shadowDepth}px ${st.blur * 1.5}px ${rgba(st.shadowColor, st.shadowOpacity)}`);
  }
  if (st.blur > 0 && st.outlineWidth > 0) shadows.push(`0 0 ${st.blur * 2}px ${rgba(st.outlineColor, 100)}`);
  return {
    fontFamily: `"${st.fontFamily}", sans-serif`,
    fontSize: `${size}px`,
    lineHeight: `${st.fontSize}px`,
    fontWeight: st.bold ? "700" : "400",
    fontStyle: st.italic ? "italic" : "normal",
    letterSpacing: `${st.letterSpacing || 0}px`,
    color: rgba(st.textColor, st.textOpacity),
    WebkitTextStroke: st.outlineWidth > 0 ? `${st.outlineWidth * 2}px ${rgba(st.outlineColor, 100)}` : "0",
    paintOrder: "stroke fill",
    strokeLinejoin: "round",
    textShadow: shadows.join(", ") || "none",
  };
}

function buildCueEl(cue, wordIdx) {
  const st = S.style;
  if (!st.boxEnabled) return positionCue(buildCueText(cue, wordIdx, "text"));
  // Like libass: boxes on a layer below all text, so a lower line's box never
  // covers descenders of the line above.
  const stack = document.createElement("div");
  stack.className = "sub-stack";
  const box = buildCueText(cue, wordIdx, "box");
  box.setAttribute("aria-hidden", "true");
  const text = buildCueText(cue, wordIdx, "text");
  stack.append(box, text);
  stack.style.alignItems = text.style.alignItems;
  return positionCue(stack, text);
}

function buildCueText(cue, wordIdx, layerKind) {
  const st = S.style;
  const col = ((st.alignment || 2) - 1) % 3; // 0 left, 1 center, 2 right
  const cueEl = document.createElement("div");
  cueEl.className = "sub-cue";
  Object.assign(cueEl.style, textCss(st));
  if (layerKind === "box") {
    Object.assign(cueEl.style, { color: "transparent", WebkitTextStroke: "0", textShadow: "none" });
  }
  cueEl.style.textAlign = ["left", "center", "right"][col];
  cueEl.style.alignItems = ["flex-start", "center", "flex-end"][col];

  let k = 0;
  for (const line of cue.lines) {
    const lineEl = document.createElement("div");
    lineEl.className = "sub-line";
    const inner = document.createElement("span");
    if (layerKind === "box") {
      inner.className = "boxed";
      inner.style.background = rgba(st.boxColor, st.boxOpacity);
      inner.style.padding = `${st.boxPadding}px ${st.boxPadding}px`;
    }
    let prev = null;
    for (const w of line) {
      if (prev !== null && needsSpace(prev, w.t)) inner.appendChild(document.createTextNode(" "));
      const span = document.createElement("span");
      span.className = "sub-word";
      span.textContent = w.t;
      const mode = layerKind === "box" ? (st.highlightMode === "reveal" ? "reveal" : "none") : st.highlightMode;
      if (layerKind === "box" && (st.highlightMode === "word" || st.highlightMode === "reveal") && k === wordIdx && st.highlightScale !== 100) {
        span.style.fontSize = `${(st.fontSize * fontRatio() * st.highlightScale) / 100}px`;
      }
      if (mode === "word" && k === wordIdx) {
        span.style.color = rgba(st.highlightColor, st.textOpacity);
        if (st.highlightScale !== 100) span.style.fontSize = `${(st.fontSize * fontRatio() * st.highlightScale) / 100}px`;
      } else if (mode === "fill" && k <= wordIdx) {
        span.style.color = rgba(st.highlightColor, st.textOpacity);
      } else if (mode === "reveal" && k > wordIdx) {
        span.style.visibility = "hidden";
      } else if (mode === "reveal" && k === wordIdx) {
        span.style.color = rgba(st.highlightColor, st.textOpacity);
        if (st.highlightScale !== 100) span.style.fontSize = `${(st.fontSize * fontRatio() * st.highlightScale) / 100}px`;
      }
      inner.appendChild(span);
      prev = w.t;
      k++;
    }
    lineEl.appendChild(inner);
    cueEl.appendChild(lineEl);
  }
  return cueEl;
}

function positionCue(cueEl, textEl = cueEl) {
  const st = S.style;
  const a = st.alignment || 2;
  const row = a >= 7 ? "top" : a >= 4 ? "mid" : "bot";
  const col = (a - 1) % 3;
  if (cueEl !== textEl) cueEl.classList.add("sub-cue-wrap");

  if (st.posX !== null && st.posX !== undefined && st.posY !== null && st.posY !== undefined) {
    cueEl.classList.add("abs");
    cueEl.style.left = `${st.posX}px`;
    cueEl.style.top = `${st.posY}px`;
    const tx = ["0", "-50%", "-100%"][col];
    const ty = row === "top" ? "0" : row === "mid" ? "-50%" : "-100%";
    cueEl.style.transform = `translate(${tx}, ${ty})`;
    const wrap = document.createElement("div");
    wrap.className = "sub-area";
    wrap.appendChild(cueEl);
    attachDrag(cueEl, row, col);
    return wrap;
  }
  const area = document.createElement("div");
  area.className = "sub-area";
  const mv = row === "mid" ? 0 : st.marginV;
  area.style.padding = `${mv}px ${st.marginR}px ${mv}px ${st.marginL}px`;
  area.style.justifyContent = row === "top" ? "flex-start" : row === "mid" ? "center" : "flex-end";
  area.style.alignItems = ["flex-start", "center", "flex-end"][col];
  area.appendChild(cueEl);
  attachDrag(cueEl, row, col);
  return area;
}

function attachDrag(el, row, col) {
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const layerRect = layer.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const fx = col / 2;
    const fy = row === "top" ? 0 : row === "mid" ? 0.5 : 1;
    const startX = (r.left + r.width * fx - layerRect.left) / S.scale;
    const startY = (r.top + r.height * fy - layerRect.top) / S.scale;
    const ox = e.clientX;
    const oy = e.clientY;
    let moved = false;
    el.setPointerCapture(e.pointerId);
    el.classList.add("dragging");
    const [prx, pry] = S.playRes;
    let x = startX;
    let y = startY;
    const onMove = (ev) => {
      const dx = (ev.clientX - ox) / S.scale;
      const dy = (ev.clientY - oy) / S.scale;
      if (!moved && Math.hypot(dx, dy) < 3) return;
      if (!moved) {
        pushUndo();
        video.pause();
        S.dragging = true; // freeze overlay re-renders so pointer capture survives
      }
      moved = true;
      x = clamp(startX + dx, 0, prx);
      y = clamp(startY + dy, 0, pry);
      if (col === 1 && Math.abs(x - prx / 2) < 12) x = prx / 2; // snap to center
      el.style.translate = `${x - startX}px ${y - startY}px`;
    };
    const onUp = () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
      el.classList.remove("dragging");
      S.dragging = false;
      if (moved) {
        S.style.posX = Math.round(x);
        S.style.posY = Math.round(y);
        S.activePreset = null;
        syncControls();
        renderPresetsActive();
        lastOverlayKey = null;
        renderOverlay();
        scheduleSave();
      } else togglePlay();
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
  });
}

// ------------------------------------------------------------------ playback loop + timeline

function togglePlay() {
  if (video.paused) video.play();
  else video.pause();
}

function tick() {
  if (S.project && !$("#editor").classList.contains("hidden")) {
    renderOverlay();
    const d = video.duration || S.project.info.duration || 1;
    $("#tlHead").style.left = `${(video.currentTime / d) * 100}%`;
    $("#timeLabel").textContent = `${fmtTime(video.currentTime)} / ${fmtTime(d, false)}`;
    highlightActiveSegment();
  }
  requestAnimationFrame(tick);
}

function renderTimeline() {
  const box = $("#tlCues");
  box.innerHTML = "";
  const d = S.project.info.duration || video.duration || 1;
  const frag = document.createDocumentFragment();
  for (const c of S.cues) {
    const el = document.createElement("div");
    el.className = "tl-cue" + (c.low ? " low" : "");
    el.style.left = `${(c.start / d) * 100}%`;
    el.style.width = `${((c.end - c.start) / d) * 100}%`;
    frag.appendChild(el);
  }
  box.appendChild(frag);
}

// ------------------------------------------------------------------ segment editor

function autosize(ta) {
  ta.style.height = "auto";
  ta.style.height = `${ta.scrollHeight + 2}px`;
}

function renderSegments() {
  const list = $("#segList");
  $$(".seg", list).forEach((el) => el.remove());
  $("#segEmpty").classList.toggle("hidden", S.segments.length > 0);
  const low = S.segments.filter((s) => s.lowConf).length;
  $("#segCount").textContent = S.segments.length
    ? `${S.segments.length} lines${low ? ` · ${low} to double-check` : ""}`
    : "";
  const frag = document.createDocumentFragment();
  S.segments.forEach((seg, i) => {
    const row = document.createElement("div");
    row.className = "seg";
    row.dataset.i = i;
    row.innerHTML = `
      <div class="seg-times">
        <input class="t-start" title="Start (m:ss.cc)" />
        <input class="t-end" title="End (m:ss.cc)" />
      </div>
      <div>
        <textarea rows="1" spellcheck="true"></textarea>
        ${seg.lowConf ? '<div class="warn">⚠ Low confidence — please check this line</div>' : ""}
      </div>
      <div class="seg-actions">
        <button data-act="play" title="Play this line">▶</button>
        <button data-act="split" title="Split at the text cursor">✂</button>
        <button data-act="merge" title="Merge with the next line">⤓</button>
        <button data-act="del" title="Delete line">✕</button>
      </div>`;
    $(".t-start", row).value = fmtTime(seg.start);
    $(".t-end", row).value = fmtTime(seg.end);
    $("textarea", row).value = seg.text;
    frag.appendChild(row);
  });
  list.appendChild(frag);
  $$(".seg textarea", list).forEach(autosize);
  applyFind();
}

let activeSegIdx = -1;
function highlightActiveSegment() {
  const t = video.currentTime;
  let idx = -1;
  for (let i = 0; i < S.segments.length; i++) {
    if (S.segments[i].start <= t && t < S.segments[i].end) {
      idx = i;
      break;
    }
  }
  if (idx === activeSegIdx) return;
  const list = $("#segList");
  $(`.seg[data-i="${activeSegIdx}"]`, list)?.classList.remove("active");
  activeSegIdx = idx;
  const row = $(`.seg[data-i="${idx}"]`, list);
  if (row) {
    row.classList.add("active");
    const typing = list.contains(document.activeElement);
    if ($("#followPlay").checked && !typing && !video.paused) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function segmentsChanged({ rerender = true } = {}) {
  S.segments.sort((a, b) => a.start - b.start);
  if (rerender) renderSegments();
  recompute();
  scheduleSave();
}

function bindSegmentList() {
  const list = $("#segList");
  list.addEventListener("input", (e) => {
    const row = e.target.closest(".seg");
    if (!row || e.target.tagName !== "TEXTAREA") return;
    const i = +row.dataset.i;
    pushUndo(`text-${i}`);
    const seg = S.segments[i];
    seg.text = e.target.value;
    seg.words = retimeWords(seg, seg.text);
    delete seg.lowConf;
    autosize(e.target);
    segmentsChanged({ rerender: false });
  });
  list.addEventListener("change", (e) => {
    const row = e.target.closest(".seg");
    if (!row || e.target.tagName !== "INPUT") return;
    const i = +row.dataset.i;
    const seg = S.segments[i];
    const v = parseTime(e.target.value);
    if (v === null) {
      e.target.value = fmtTime(e.target.classList.contains("t-start") ? seg.start : seg.end);
      return toast("Use a time like 1:23.45", "error");
    }
    pushUndo();
    let start = seg.start;
    let end = seg.end;
    if (e.target.classList.contains("t-start")) start = v;
    else end = v;
    if (end <= start) end = start + 0.5;
    seg.words = rescaleWords(seg.words || [], seg.start, seg.end, start, end);
    seg.start = start;
    seg.end = end;
    segmentsChanged();
  });
  list.addEventListener("focusin", (e) => {
    const row = e.target.closest(".seg");
    if (!row) return;
    const seg = S.segments[+row.dataset.i];
    if (video.paused && seg) video.currentTime = seg.start + 0.01;
  });
  list.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const row = btn.closest(".seg");
    const i = +row.dataset.i;
    const seg = S.segments[i];
    const act = btn.dataset.act;
    if (act === "play") {
      video.currentTime = seg.start + 0.01;
      video.play();
    } else if (act === "del") {
      pushUndo();
      S.segments.splice(i, 1);
      segmentsChanged();
    } else if (act === "merge") {
      const next = S.segments[i + 1];
      if (!next) return toast("No next line to merge with.");
      pushUndo();
      seg.text = joinWords([seg.text, next.text]);
      seg.words = [...(seg.words || []), ...(next.words || [])];
      seg.end = Math.max(seg.end, next.end);
      if (next.lowConf) seg.lowConf = true;
      S.segments.splice(i + 1, 1);
      segmentsChanged();
    } else if (act === "split") {
      const ta = $("textarea", row);
      const pos = ta.selectionStart;
      const before = ta.value.slice(0, pos).trim();
      const after = ta.value.slice(pos).trim();
      if (!before || !after) return toast("Click inside the text where you want to split, then press ✂.");
      pushUndo();
      const nBefore = before.split(/\s+/).length;
      const words = seg.words && seg.words.length ? seg.words : interpolateWords(seg.text, seg.start, seg.end);
      let cut;
      if (words.length === ta.value.trim().split(/\s+/).length && nBefore < words.length) {
        cut = words[nBefore].s;
      } else {
        cut = seg.start + ((seg.end - seg.start) * before.length) / (before.length + after.length);
      }
      const a = { ...seg, end: cut, text: before };
      const b = { ...seg, start: cut, text: after };
      a.words = words.filter((w) => w.s < cut);
      b.words = words.filter((w) => w.s >= cut);
      if (a.words.length !== nBefore) a.words = interpolateWords(before, a.start, a.end);
      if (!b.words.length || b.words.length !== after.split(/\s+/).length) b.words = interpolateWords(after, b.start, b.end);
      S.segments.splice(i, 1, a, b);
      segmentsChanged();
    }
  });
}

function applyFind() {
  const q = $("#findInput").value.trim().toLowerCase();
  let first = null;
  $$(".seg").forEach((row) => {
    const hit = q && S.segments[+row.dataset.i]?.text.toLowerCase().includes(q);
    row.classList.toggle("match", !!hit);
    if (hit && !first) first = row;
  });
  return first;
}

function replaceAll() {
  const q = $("#findInput").value;
  const r = $("#replaceInput").value;
  if (!q) return toast("Type something to find first.");
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  let n = 0;
  pushUndo();
  for (const seg of S.segments) {
    const next = seg.text.replace(re, () => (n++, r));
    if (next !== seg.text) {
      seg.text = next;
      seg.words = retimeWords(seg, next);
    }
  }
  toast(n ? `Replaced ${n} match${n === 1 ? "" : "es"}.` : "No matches.");
  if (n) segmentsChanged();
}

function addSegment() {
  pushUndo();
  const t = video.currentTime;
  const next = S.segments.find((s) => s.start > t);
  const end = Math.min(t + 2, next ? next.start : t + 2, S.project.info.duration || t + 2);
  const seg = { start: +t.toFixed(3), end: +Math.max(end, t + 0.5).toFixed(3), text: "New subtitle" };
  seg.words = interpolateWords(seg.text, seg.start, seg.end);
  S.segments.push(seg);
  segmentsChanged();
  const i = S.segments.indexOf(seg);
  const ta = $(`.seg[data-i="${i}"] textarea`);
  if (ta) {
    ta.focus();
    ta.select();
  }
}

function shiftTiming() {
  const v = prompt("Shift all subtitles by how many seconds?\nPositive = later, negative = earlier (e.g. -0.3)", "0.2");
  if (v === null) return;
  const d = parseFloat(v);
  if (Number.isNaN(d) || !d) return;
  pushUndo();
  for (const seg of S.segments) {
    seg.start = Math.max(0, +(seg.start + d).toFixed(3));
    seg.end = Math.max(seg.start + 0.05, +(seg.end + d).toFixed(3));
    seg.words = (seg.words || []).map((w) => ({ ...w, s: Math.max(0, +(w.s + d).toFixed(3)), e: Math.max(0, +(w.e + d).toFixed(3)) }));
  }
  segmentsChanged();
  toast(`Shifted by ${d > 0 ? "+" : ""}${d} s`);
}

// ------------------------------------------------------------------ transcription

const LANG_NAMES = (() => {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" });
  } catch {
    return null;
  }
})();

function fillTranscribeOptions() {
  const saved = JSON.parse(localStorage.getItem("tSettings") || "{}");
  const m = $("#tModel");
  m.innerHTML = "";
  for (const md of S.system.models) {
    const o = new Option(`${md.label} · ${md.downloaded ? "✓ downloaded" : md.size}`, md.id);
    m.appendChild(o);
  }
  const l = $("#tLanguage");
  l.innerHTML = "";
  l.appendChild(new Option("Auto-detect", "auto"));
  const langs = S.system.languages
    .map((c) => ({ c, n: (LANG_NAMES && LANG_NAMES.of(c)) || c }))
    .sort((a, b) => a.n.localeCompare(b.n));
  for (const { c, n } of langs) l.appendChild(new Option(n === c ? c : `${n} (${c})`, c));
  if (!S.system.cuda && saved.model === undefined) m.value = "small";
  for (const [id, key] of [["tModel", "model"], ["tLanguage", "language"], ["tTask", "task"], ["tDevice", "device"], ["tNoise", "noiseFilter"]]) {
    if (saved[key] !== undefined && [...$("#" + id).options].some((o) => o.value === saved[key])) $("#" + id).value = saved[key];
  }
  if (saved.vad !== undefined) $("#tVad").checked = saved.vad;
  if (saved.batched !== undefined) $("#tBatched").checked = saved.batched;
}

async function refreshModelFlags() {
  try {
    S.system = await api("/api/system");
    const sel = $("#tModel");
    for (const md of S.system.models) {
      const o = [...sel.options].find((x) => x.value === md.id);
      if (o) o.textContent = `${md.label} · ${md.downloaded ? "✓ downloaded" : md.size}`;
    }
  } catch {}
}

function transcribeSettings() {
  const s = {
    model: $("#tModel").value,
    language: $("#tLanguage").value,
    task: $("#tTask").value,
    device: $("#tDevice").value,
    noiseFilter: $("#tNoise").value,
    vad: $("#tVad").checked,
    batched: $("#tBatched").checked,
  };
  localStorage.setItem("tSettings", JSON.stringify(s));
  return { ...s, initialPrompt: $("#tPrompt").value };
}

async function pollJob(jobId, onUpdate) {
  for (;;) {
    let j;
    try {
      j = await api(`/api/jobs/${jobId}`);
    } catch (e) {
      throw new Error("Lost contact with the server: " + e.message);
    }
    onUpdate?.(j);
    if (j.status === "done") return j;
    if (j.status === "error") throw new Error(j.error || "Failed");
    if (j.status === "cancelled") {
      const err = new Error("Cancelled");
      err.cancelled = true;
      throw err;
    }
    await new Promise((r) => setTimeout(r, 600));
  }
}

function showJob(box, j) {
  box.classList.remove("hidden");
  const bar = $(".bar", box);
  bar.classList.toggle("indeterminate", j.progress < 0 || j.status === "queued");
  $("i", bar).style.width = `${Math.max(0, j.progress) * 100}%`;
  $(".job-msg", box).textContent = j.message || (j.status === "queued" ? "Starting…" : "");
}

let tJobId = null;
async function startTranscribe() {
  if (S.segments.length && !confirm("Replace the current subtitles with a new transcription?")) return;
  const btn = $("#btnTranscribe");
  btn.disabled = true;
  $("#btnCancelT").classList.remove("hidden");
  $("#tNotes").innerHTML = "";
  const box = $("#tJob");
  try {
    const { jobId } = await api(`/api/project/${S.project.id}/transcribe`, { json: transcribeSettings() });
    tJobId = jobId;
    const j = await pollJob(jobId, (j) => showJob(box, j));
    pushUndo();
    S.segments = j.result.segments;
    segmentsChanged();
    showTranscribeNotes(j.result, true);
    refreshModelFlags();
    if (S.segments.length) {
      toast(`Done: ${S.segments.length} lines.`, "ok");
      switchTab("style");
    }
  } catch (e) {
    if (!e.cancelled) toast("Transcription failed: " + e.message, "error");
  } finally {
    tJobId = null;
    btn.disabled = false;
    $("#btnCancelT").classList.add("hidden");
    box.classList.add("hidden");
  }
}

function showTranscribeNotes(meta, fresh) {
  const box = $("#tNotes");
  box.innerHTML = "";
  const add = (text, kind = "") => {
    const d = document.createElement("div");
    d.className = kind;
    d.textContent = text;
    box.appendChild(d);
  };
  if (meta.language) {
    const name = (LANG_NAMES && LANG_NAMES.of(meta.language)) || meta.language;
    add(`${fresh ? "Detected" : "Language"}: ${name}${meta.languageProbability ? ` (${Math.round(meta.languageProbability * 100)}% sure)` : ""} · ${meta.model} on ${String(meta.device).toUpperCase()}`, "ok");
  }
  for (const n of meta.notes || []) add(n, /No speech/.test(n) ? "warn" : "");
  const low = S.segments.filter((s) => s.lowConf).length;
  if (low) add(`${low} line${low === 1 ? "" : "s"} marked ⚠ — the audio was unclear there; please give them a quick check.`, "warn");
}

async function importSubs(file) {
  if (S.segments.length && !confirm("Replace the current subtitles with this file?")) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api(`/api/project/${S.project.id}/import`, { method: "POST", body: fd });
    pushUndo();
    S.segments = r.segments;
    segmentsChanged();
    toast(`Imported ${r.segments.length} lines.`, "ok");
  } catch (e) {
    toast("Import failed: " + e.message, "error");
  }
}

// ------------------------------------------------------------------ style controls

const STYLE_UNITS = {
  fontSize: "", letterSpacing: "", textOpacity: "%", outlineWidth: "", shadowDepth: "", shadowOpacity: "%",
  blur: "", boxOpacity: "%", boxPadding: "", highlightScale: "%", fadeIn: " ms", fadeOut: " ms", marginV: "", marginL: "", marginR: "",
};
const LAYOUT_FMT = {
  maxWidthPct: (v) => `${v}%`,
  maxChars: (v) => `${v}`,
  maxLines: (v) => `${v}`,
  maxWords: (v) => (+v ? `${v}` : "no limit"),
  gapSplit: (v) => `${(+v).toFixed(1)} s`,
  fillGaps: (v) => (+v ? `${(+v).toFixed(2)} s` : "off"),
  minDuration: (v) => `${(+v).toFixed(1)} s`,
  maxDuration: (v) => `${(+v).toFixed(1)} s`,
};

function syncControls() {
  const st = S.style;
  for (const el of $$("[data-style]")) {
    const k = el.dataset.style;
    if (el.type === "checkbox") el.checked = !!st[k];
    else el.value = st[k] ?? "";
  }
  for (const out of $$("output[data-for]")) {
    const k = out.dataset.for;
    out.textContent = `${st[k]}${STYLE_UNITS[k] ?? ""}`;
  }
  const L = S.layout;
  for (const el of $$("[data-layout]")) {
    const k = el.dataset.layout;
    if (el.type === "checkbox") el.checked = !!L[k];
    else el.value = L[k];
  }
  for (const out of $$("output[data-for-layout]")) {
    const k = out.dataset.forLayout;
    out.textContent = (LAYOUT_FMT[k] || String)(L[k]);
  }
  for (const grp of $$("[data-layout-radio]")) {
    for (const b of $$("button", grp)) b.classList.toggle("active", b.dataset.value === L[grp.dataset.layoutRadio]);
  }
  for (const el of $$("[data-show]")) {
    const [k, v] = el.dataset.show.split("=");
    el.classList.toggle("hidden", String(L[k]) !== v);
  }
  $$("#alignGrid button").forEach((b) => b.classList.toggle("active", +b.dataset.align === +st.alignment));
  $("#boxOpts").classList.toggle("hidden", !st.boxEnabled);
  $$(".hl-opts").forEach((el) => el.classList.toggle("hidden", st.highlightMode === "none"));
  $("#resetPos").classList.toggle("hidden", st.posX === null || st.posX === undefined);
  for (const sw of $$("[data-swatch]")) {
    $$("button", sw).forEach((b) => (b.style.outline = b.dataset.color.toLowerCase() === String(st[sw.dataset.swatch]).toLowerCase() ? "2px solid var(--accent)" : ""));
  }
}

function onStyleInput(el) {
  const k = el.dataset.style;
  pushUndo(`style-${k}`);
  let v;
  if (el.type === "checkbox") v = el.checked;
  else if (el.type === "range" || el.type === "number") v = parseFloat(el.value);
  else v = el.value;
  S.style[k] = v;
  S.activePreset = null;
  syncControls();
  if (k === "bold" || k === "italic") ensureFont(S.style.fontFamily);
  recompute();
  renderPresetsActive();
  scheduleSave();
}

function onLayoutInput(el) {
  const k = el.dataset.layout;
  pushUndo(`layout-${k}`);
  S.layout[k] = el.type === "checkbox" ? el.checked : parseFloat(el.value);
  syncControls();
  recompute();
  scheduleSave();
}

// ------------------------------------------------------------------ presets

function applyPreset(p) {
  pushUndo();
  const fam = p.user ? (S.fontMap.has(p.style.fontFamily) ? p.style.fontFamily : resolveFont([p.style.fontFamily, ...BUILTIN_PRESETS[0].fonts])) : resolveFont(p.fonts);
  S.style = { ...S.system.defaultStyle, ...p.style, fontFamily: fam };
  S.layout = { ...S.layout, ...(p.layout || {}) };
  S.activePreset = p.name;
  syncControls();
  renderFontButton();
  renderPresetsActive();
  ensureFont(fam).then(recompute);
  scheduleSave();
}

function presetSampleStyle(p) {
  const st = { ...S.system.defaultStyle, ...p.style };
  const fam = p.user ? st.fontFamily : resolveFont(p.fonts);
  const css = {
    fontFamily: `"${fam}", sans-serif`,
    fontWeight: st.bold ? 700 : 400,
    fontStyle: st.italic ? "italic" : "normal",
    color: rgba(st.textColor, st.textOpacity),
    WebkitTextStroke: st.outlineWidth > 0 ? `${Math.min(3, st.outlineWidth * 0.5)}px ${st.outlineColor}` : "0",
    paintOrder: "stroke fill",
    textShadow: st.shadowDepth > 0 ? `1px 1px ${st.blur}px ${rgba(st.shadowColor, st.shadowOpacity)}` : "none",
    padding: st.boxEnabled ? "1px 5px" : "0",
    background: st.boxEnabled ? rgba(st.boxColor, st.boxOpacity) : "transparent",
    textTransform: p.layout?.uppercase ? "uppercase" : "none",
  };
  return css;
}

function renderPresets() {
  const box = $("#presets");
  box.innerHTML = "";
  const all = [...BUILTIN_PRESETS, ...S.userPresets.map((p) => ({ ...p, user: true }))];
  for (const p of all) {
    const el = document.createElement("div");
    el.className = "preset";
    el.dataset.name = p.name;
    el.title = p.user ? `${p.name} (yours)` : p.name;
    const name = document.createElement("span");
    name.className = "pname";
    name.textContent = p.name;
    const sample = document.createElement("span");
    sample.className = "sample";
    const st = { ...S.system.defaultStyle, ...p.style };
    if (st.highlightMode !== "none") {
      sample.innerHTML = `Hey <span></span>`;
      sample.lastChild.textContent = "there";
      sample.lastChild.style.color = st.highlightColor;
    } else sample.textContent = "Hey there";
    Object.assign(sample.style, presetSampleStyle(p));
    el.append(name, sample);
    if (p.user) {
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "✕";
      del.title = "Delete preset";
      del.onclick = (e) => {
        e.stopPropagation();
        if (!confirm(`Delete preset "${p.name}"?`)) return;
        S.userPresets = S.userPresets.filter((x) => x.name !== p.name);
        persistPresets();
        renderPresets();
      };
      el.appendChild(del);
    }
    el.onclick = () => applyPreset(p);
    box.appendChild(el);
  }
  renderPresetsActive();
}

function renderPresetsActive() {
  $$("#presets .preset").forEach((el) => el.classList.toggle("active", el.dataset.name === S.activePreset));
}

async function persistPresets() {
  try {
    await api("/api/presets", { method: "PUT", json: S.userPresets });
  } catch (e) {
    toast("Could not save presets: " + e.message, "error");
  }
}

function saveCurrentPreset() {
  const name = (prompt("Name for this preset:", "My style") || "").trim();
  if (!name) return;
  if (BUILTIN_PRESETS.some((p) => p.name === name)) return toast("That name is used by a built-in preset.", "error");
  const layoutKeys = ["maxWords", "maxLines", "uppercase", "wrapBy", "maxWidthPct", "maxChars", "stripPunct", "overflow", "balance", "breakOnSentence"];
  const layout = Object.fromEntries(layoutKeys.map((k) => [k, S.layout[k]]));
  const preset = { name, style: clone(S.style), layout };
  S.userPresets = [...S.userPresets.filter((p) => p.name !== name), preset];
  S.activePreset = name;
  persistPresets();
  renderPresets();
  toast(`Saved preset "${name}".`, "ok");
}

// ------------------------------------------------------------------ export + render

function fillEncoders() {
  const sel = $("#eEncoder");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const e of S.system.encoders || []) sel.appendChild(new Option(e.label, e.id));
  const hw = (S.system.encoders || []).find((e) => e.id !== "libx264");
  sel.value = prev && [...sel.options].some((o) => o.value === prev) ? prev : hw ? hw.id : "libx264";
}

function syncExportMode() {
  for (const b of $$("[data-export-radio] button")) b.classList.toggle("active", b.dataset.value === S.exportMode);
  const burn = S.exportMode === "burn";
  $$(".burn-only").forEach((el) => el.classList.toggle("hidden", !burn));
  $("#modeHint").textContent = burn
    ? "Subtitles become part of the picture with your exact style. Works everywhere (Instagram, WhatsApp, TVs)."
    : $("#eContainer").value === "mkv"
      ? "Fast (no re-encode). MKV keeps your styling (VLC, mpv). Viewers can switch subtitles off."
      : "Fast (no re-encode). MP4 subtitle tracks are plain text: players use their own style.";
}

async function exportSubs(fmt) {
  if (!S.cues.length && fmt !== "txt") return toast("No subtitles to export yet.", "error");
  try {
    const res = await api(`/api/project/${S.project.id}/subtitles`, { json: { format: fmt, cues: cuesForServer(), style: S.style } });
    download(await res.blob(), `${S.project.name}.${fmt}`);
  } catch (e) {
    toast("Export failed: " + e.message, "error");
  }
}

let rJobId = null;
async function startRender(test = false) {
  if (!S.cues.length) return toast("No subtitles yet — transcribe first.", "error");
  const body = {
    cues: cuesForServer(),
    style: S.style,
    mode: S.exportMode,
    container: $("#eContainer").value,
    encoder: $("#eEncoder").value,
    quality: +$("#eQuality").value,
    speed: $("#eSpeed").value,
    height: +$("#eHeight").value,
    outputDir: $("#eOutDir").value.trim() || null,
  };
  if (test) body.clip = { start: video.currentTime, duration: 10 };
  const box = $("#rJob");
  $("#rResult").classList.add("hidden");
  $("#btnRender").disabled = $("#btnTestRender").disabled = true;
  $("#btnCancelR").classList.remove("hidden");
  try {
    await saveNow();
    const { jobId } = await api(`/api/project/${S.project.id}/render`, { json: body });
    rJobId = jobId;
    const j = await pollJob(jobId, (j) => showJob(box, j));
    showResult(jobId, j.result);
    toast(`Saved ${j.result.name}`, "ok");
  } catch (e) {
    if (!e.cancelled) toast("Render failed: " + e.message, "error");
  } finally {
    rJobId = null;
    box.classList.add("hidden");
    $("#btnRender").disabled = $("#btnTestRender").disabled = false;
    $("#btnCancelR").classList.add("hidden");
  }
}

function showResult(jobId, result) {
  const box = $("#rResult");
  box.classList.remove("hidden");
  const url = `/api/jobs/${jobId}/file`;
  $("#rVideo").src = url;
  $("#rPath").textContent = `${result.path} · ${(result.size / 1e6).toFixed(1)} MB`;
  $("#btnDownload").href = url;
  $("#btnDownload").download = result.name;
  $("#btnReveal").onclick = () => api("/api/reveal", { json: { path: result.path } }).catch((e) => toast(e.message, "error"));
}

async function exactFrame() {
  if (!S.project) return;
  const t = video.currentTime;
  const btn = $("#btnExactFrame");
  btn.disabled = true;
  btn.textContent = "Rendering…";
  try {
    const res = await api(`/api/project/${S.project.id}/frame`, { json: { time: t, cues: cuesForServer(), style: S.style } });
    const blob = await res.blob();
    const img = $("#frameImg");
    if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(blob);
    $("#frameInfo").textContent = `at ${fmtTime(t)}`;
    $("#frameModal").classList.remove("hidden");
  } catch (e) {
    toast("Frame render failed: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Exact frame";
  }
}

// ------------------------------------------------------------------ tabs + bindings

function switchTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
}

function bindStatic() {
  // welcome
  const dz = $("#dropzone");
  $("#fileInput").onchange = (e) => e.target.files[0] && uploadFile(e.target.files[0]);
  dz.addEventListener("dragover", (e) => {
    e.preventDefault();
    dz.classList.add("over");
  });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });
  $("#pathForm").onsubmit = async (e) => {
    e.preventDefault();
    const path = $("#pathInput").value.trim();
    if (!path) return;
    try {
      const r = await api("/api/open", { json: { path } });
      loadProject(r.project, r.proxyJob);
    } catch (err) {
      toast(err.message, "error");
    }
  };
  $("#btnHome").onclick = () => {
    if (S.project) saveNow();
    S.project = null;
    showWelcome();
  };

  // player
  $("#btnPlay").onclick = togglePlay;
  video.addEventListener("play", () => ($("#btnPlay").textContent = "❚❚"));
  video.addEventListener("pause", () => ($("#btnPlay").textContent = "▶"));
  video.addEventListener("loadedmetadata", fitStage);
  video.addEventListener("error", () => {
    if (S.project) toast("The browser couldn't play this video. Subtitles can still be generated and rendered.", "error");
  });
  $("#stage").addEventListener("click", (e) => {
    if (e.target === video || e.target === $("#stage")) togglePlay();
  });
  new ResizeObserver(fitStage).observe($("#stageWrap"));
  const tl = $("#timeline");
  const seek = (e) => {
    const r = tl.getBoundingClientRect();
    const d = video.duration || S.project.info.duration;
    video.currentTime = clamp((e.clientX - r.left) / r.width, 0, 1) * d;
  };
  tl.addEventListener("pointerdown", (e) => {
    seek(e);
    tl.setPointerCapture(e.pointerId);
    const mv = (ev) => seek(ev);
    tl.addEventListener("pointermove", mv);
    tl.addEventListener("pointerup", () => tl.removeEventListener("pointermove", mv), { once: true });
  });
  $("#btnExactFrame").onclick = exactFrame;
  $$("#frameModal [data-close], #frameModal").forEach((el) =>
    el.addEventListener("click", (e) => {
      if (e.target === el) $("#frameModal").classList.add("hidden");
    }),
  );

  // segments
  bindSegmentList();
  $("#findInput").addEventListener("input", () => applyFind()?.scrollIntoView({ block: "nearest" }));
  $("#btnReplace").onclick = replaceAll;
  $("#btnAddSeg").onclick = addSegment;
  $("#btnShift").onclick = shiftTiming;
  $("#btnUndo").onclick = undo;
  $("#btnRedo").onclick = redo;

  // tabs
  $$("#tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));

  // transcribe
  $("#btnTranscribe").onclick = startTranscribe;
  $("#btnCancelT").onclick = () => tJobId && api(`/api/jobs/${tJobId}/cancel`, { json: {} });
  $("#importInput").onchange = (e) => {
    if (e.target.files[0]) importSubs(e.target.files[0]);
    e.target.value = "";
  };

  // style
  for (const el of $$("[data-style]")) el.addEventListener("input", () => onStyleInput(el));
  for (const sw of $$("[data-swatch]")) {
    for (const c of SWATCHES) {
      const b = document.createElement("button");
      b.style.background = c;
      b.dataset.color = c;
      b.title = c;
      b.onclick = () => {
        const input = $(`input[data-style="${sw.dataset.swatch}"]`);
        input.value = c.toLowerCase();
        onStyleInput(input);
      };
      sw.appendChild(b);
    }
  }
  $$("#alignGrid button").forEach(
    (b) =>
      (b.onclick = () => {
        pushUndo();
        S.style.alignment = +b.dataset.align;
        S.style.posX = S.style.posY = null;
        S.activePreset = null;
        syncControls();
        recompute();
        scheduleSave();
      }),
  );
  $("#resetPos").onclick = (e) => {
    e.preventDefault();
    pushUndo();
    S.style.posX = S.style.posY = null;
    syncControls();
    recompute();
    scheduleSave();
  };
  $("#fontCurrent").onclick = () => {
    const pop = $("#fontPop");
    pop.classList.toggle("hidden");
    if (!pop.classList.contains("hidden")) {
      $("#fontSearch").value = "";
      renderFontList();
      $("#fontSearch").focus();
      $(".font-item.sel")?.scrollIntoView({ block: "center" });
    }
  };
  $("#fontSearch").addEventListener("input", renderFontList);
  $("#fontSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $(".font-item")?.click();
  });
  document.addEventListener("click", (e) => {
    if (!$("#fontPicker").contains(e.target)) $("#fontPop").classList.add("hidden");
  });
  $("#fontUpload").onchange = async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api("/api/fonts/upload", { method: "POST", body: fd });
      setFonts(await api("/api/fonts"));
      await selectFont(r.family);
      toast(`Added font "${r.family}".`, "ok");
    } catch (err) {
      toast("Font upload failed: " + err.message, "error");
    }
  };
  $("#fontRescan").onclick = async () => {
    setFonts(await api("/api/fonts?rescan=true"));
    renderFontList();
    toast(`${S.fonts.length} fonts found.`);
  };
  $("#btnSavePreset").onclick = saveCurrentPreset;
  $("#btnExportPresets").onclick = () => {
    if (!S.userPresets.length) return toast("You have no saved presets yet.");
    download(new Blob([JSON.stringify(S.userPresets, null, 1)], { type: "application/json" }), "subtitle-presets.json");
  };
  $("#importPresets").onchange = async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    try {
      const list = JSON.parse(await f.text());
      if (!Array.isArray(list)) throw new Error("not a preset list");
      const valid = list.filter((p) => p && p.name && p.style);
      const names = new Set(valid.map((p) => p.name));
      S.userPresets = [...S.userPresets.filter((p) => !names.has(p.name)), ...valid];
      persistPresets();
      renderPresets();
      toast(`Imported ${valid.length} preset(s).`, "ok");
    } catch (err) {
      toast("Invalid preset file: " + err.message, "error");
    }
  };

  // layout
  for (const el of $$("[data-layout]")) el.addEventListener("input", () => onLayoutInput(el));
  for (const grp of $$("[data-layout-radio]")) {
    for (const b of $$("button", grp)) {
      b.onclick = () => {
        pushUndo();
        S.layout[grp.dataset.layoutRadio] = b.dataset.value;
        syncControls();
        recompute();
        scheduleSave();
      };
    }
  }

  // export
  $$("[data-export]").forEach((b) => (b.onclick = () => exportSubs(b.dataset.export)));
  for (const b of $$("[data-export-radio] button")) {
    b.onclick = () => {
      S.exportMode = b.dataset.value;
      syncExportMode();
    };
  }
  $("#eContainer").onchange = syncExportMode;
  syncExportMode();
  $("#btnRender").onclick = () => startRender(false);
  $("#btnTestRender").onclick = () => startRender(true);
  $("#btnCancelR").onclick = () => rJobId && api(`/api/jobs/${rJobId}/cancel`, { json: {} });

  // keyboard
  document.addEventListener("keydown", (e) => {
    const tag = document.activeElement?.tagName;
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (e.key === "Escape") {
      $("#frameModal").classList.add("hidden");
      $("#fontPop").classList.add("hidden");
    }
    if (!S.project || $("#editor").classList.contains("hidden")) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveNow();
      return;
    }
    if (typing) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      e.shiftKey ? redo() : undo();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
      e.preventDefault();
      redo();
    } else if (e.key === " ") {
      e.preventDefault();
      togglePlay();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const d = (e.shiftKey ? 5 : 2) * (e.key === "ArrowLeft" ? -1 : 1);
      video.currentTime = clamp(video.currentTime + d, 0, video.duration || 1e9);
    }
  });
  requestAnimationFrame(tick);
}

init();
