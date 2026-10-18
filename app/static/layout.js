// Turns transcript segments (with word timings) into on-screen cues.
// The same cues drive the live preview and are sent to the server for the
// final ASS/SRT/VTT, so what you see is what gets rendered.

export const DEFAULT_LAYOUT = {
  wrapBy: "width", // "width" | "chars"
  maxWidthPct: 80,
  maxChars: 42,
  maxLines: 2,
  maxWords: 0, // 0 = unlimited
  overflow: "split", // "split" | "extend"
  balance: true,
  breakOnSentence: true,
  stripPunct: false,
  uppercase: false,
  gapSplit: 1.0,
  fillGaps: 0.3,
  minDuration: 0.8,
  maxDuration: 7,
};

// Scripts written without spaces between words.
const NO_SPACE = /[฀-໿က-႟ក-៿　-ヿ㐀-䶿一-鿿豈-﫿＀-￯]/;

export function needsSpace(prev, next) {
  if (!prev || !next) return false;
  return !(NO_SPACE.test(prev.slice(-1)) || NO_SPACE.test(next[0]));
}

export function joinWords(tokens) {
  let out = "";
  for (const t of tokens) {
    if (out && needsSpace(out, t)) out += " ";
    out += t;
  }
  return out;
}

export function tokenize(text) {
  // Space-separated tokens; CJK runs are split per character so they can wrap.
  const out = [];
  for (const part of text.trim().split(/\s+/)) {
    if (!part) continue;
    if (NO_SPACE.test(part)) {
      let buf = "";
      for (const ch of part) {
        if (NO_SPACE.test(ch)) {
          if (buf) out.push(buf), (buf = "");
          out.push(ch);
        } else buf += ch;
      }
      if (buf) {
        // Attach trailing punctuation to the previous char.
        if (out.length && /^[\p{P}]+$/u.test(buf)) out[out.length - 1] += buf;
        else out.push(buf);
      }
    } else out.push(part);
  }
  return out;
}

export function interpolateWords(text, start, end) {
  const tokens = tokenize(text);
  if (!tokens.length) return [];
  const total = tokens.reduce((a, t) => a + t.length + 1, 0);
  const dur = Math.max(0.01, end - start);
  let cursor = start;
  return tokens.map((t) => {
    const span = (dur * (t.length + 1)) / total;
    const w = { t, s: round3(cursor), e: round3(cursor + span) };
    cursor += span;
    return w;
  });
}

const round3 = (x) => Math.round(x * 1000) / 1000;

function transformToken(t, layout) {
  let out = t;
  if (layout.stripPunct) out = out.replace(/[.,;:،。、，]+$/u, "").replace(/^[,;:]+/, "");
  if (layout.uppercase) out = out.toLocaleUpperCase();
  return out;
}

/**
 * @param {Array} segments  [{start,end,text,words:[{t,s,e}]}]
 * @param {object} layout
 * @param {(text:string)=>number} measure  width of text in PlayRes px (or chars)
 * @param {number} limit  max line width in the same units
 */
export function buildCues(segments, layout, measure, limit) {
  const L = { ...DEFAULT_LAYOUT, ...layout };
  const cues = [];
  const maxLines = L.overflow === "extend" ? 99 : Math.max(1, L.maxLines | 0);

  const wrap = (words) => {
    const lines = [];
    let line = [];
    for (const w of words) {
      if (line.length && measure(joinWords([...line, w].map((x) => x.t))) > limit) {
        lines.push(line);
        line = [];
      }
      line.push(w);
    }
    if (line.length) lines.push(line);
    return lines;
  };

  const balance = (words, n) => {
    // Choose break points so the widest line is as narrow as possible.
    if (n <= 1 || words.length < 2) return [words];
    const widths = (a, b) => measure(joinWords(words.slice(a, b).map((x) => x.t)));
    let best = null;
    const search = (start, k, acc, worst) => {
      if (best && worst >= best.worst) return;
      if (k === 1) {
        const w = widths(start, words.length);
        if (w > limit && words.length - start > 1) return;
        const m = Math.max(worst, w);
        if (!best || m < best.worst) best = { worst: m, lines: [...acc, words.slice(start)] };
        return;
      }
      for (let i = start + 1; i <= words.length - (k - 1); i++) {
        const w = widths(start, i);
        if (w > limit && i - start > 1) break;
        search(i, k - 1, [...acc, words.slice(start, i)], Math.max(worst, w));
      }
    };
    if (words.length <= 40 && n <= 3) search(0, n, [], 0);
    return best ? best.lines : wrap(words);
  };

  const flush = (cur, seg) => {
    if (!cur.length) return;
    let lines = wrap(cur);
    if (L.balance && lines.length > 1) lines = balance(cur, lines.length);
    cues.push({
      start: cur[0].s,
      end: cur[cur.length - 1].e,
      lines: lines.map((l) => l.map((w) => ({ t: w.t, s: w.s, e: w.e }))),
      seg: seg.__i,
      low: !!seg.lowConf,
      ...(seg.place ? { place: { ...seg.place } } : {}),
    });
  };

  segments.forEach((seg, i) => {
    seg.__i = i;
    let words = seg.words && seg.words.length ? seg.words : interpolateWords(seg.text || "", seg.start, seg.end);
    words = words.map((w) => ({ t: transformToken(w.t, L), s: w.s, e: w.e })).filter((w) => w.t);
    let cur = [];
    for (const w of words) {
      if (cur.length) {
        const last = cur[cur.length - 1];
        const gap = w.s - last.e;
        const tooMany = L.maxWords > 0 && cur.length >= L.maxWords;
        const sentence = L.breakOnSentence && /[.?!…。？！]["')\]]?$/.test(last.t) && cur.length > 1;
        const tooLong = w.e - cur[0].s > L.maxDuration;
        const overflow = !tooMany && wrap([...cur, w]).length > maxLines;
        if (gap > L.gapSplit || tooMany || sentence || tooLong || overflow) {
          flush(cur, seg);
          cur = [];
        }
      }
      cur.push(w);
    }
    flush(cur, seg);
    delete seg.__i;
  });

  cues.sort((a, b) => a.start - b.start);
  // Timing polish: minimum duration, close small gaps, never overlap.
  for (let i = 0; i < cues.length; i++) {
    const c = cues[i];
    const next = cues[i + 1];
    const limitEnd = next ? next.start - 0.001 : Infinity;
    if (c.end - c.start < L.minDuration) c.end = Math.min(c.start + L.minDuration, Math.max(c.end, limitEnd));
    if (next && next.start - c.end > 0 && next.start - c.end < L.fillGaps) c.end = next.start - 0.001;
    if (next && c.end > next.start) c.end = Math.max(c.start + 0.05, next.start - 0.001);
    c.start = round3(c.start);
    c.end = round3(c.end);
  }
  return cues;
}

/** Re-time words after the user edits a segment's text. */
export function retimeWords(seg, newText) {
  const tokens = tokenize(newText);
  const old = seg.words || [];
  if (old.length === tokens.length && old.length) {
    return old.map((w, i) => ({ ...w, t: tokens[i] }));
  }
  return interpolateWords(newText, seg.start, seg.end);
}

/** Scale word timings into a new [start, end] window. */
export function rescaleWords(words, oldStart, oldEnd, start, end) {
  const k = (end - start) / Math.max(0.001, oldEnd - oldStart);
  return words.map((w) => ({
    ...w,
    s: round3(start + (w.s - oldStart) * k),
    e: round3(start + (w.e - oldStart) * k),
  }));
}

// ---------------------------------------------------------------- letter animation
// Typewriter (letters appear while the word is spoken) and "erase letter by
// letter" share one timeline. app/subs.py mirrors this so the render matches.

const MAX_LETTER_SEC = 0.1; // a held word still types at a readable pace
const VIRAMA = /[्্੍્୍்్್്්ฺ္្]/;
const JOINER = /[\p{M}‌‍]/u;

// Split into user-visible letters: marks, joiners and virama conjuncts (क्ष)
// stay with their base letter so Indic scripts never break apart.
export function graphemes(text) {
  const out = [];
  for (const ch of Array.from(text || "")) {
    const prev = out.length ? out[out.length - 1] : "";
    if (prev && (JOINER.test(ch) || VIRAMA.test(prev.slice(-1)) || prev.endsWith("‍"))) out[out.length - 1] += ch;
    else out.push(ch);
  }
  return out;
}

export function letterAnimated(st) {
  return st.highlightMode === "typewriter" || !!st.eraseOut;
}

// For each word, the show/hide time (seconds) of each of its letters.
export function letterTimeline(cue, st) {
  const words = cue.lines.flat();
  const dur = cue.end - cue.start;
  const erase = st.eraseOut ? Math.min((st.eraseMs ?? 300) / 1000, dur * 0.5) : 0;
  const eraseStart = cue.end - erase;
  const total = words.reduce((n, w) => n + graphemes(w.t).length, 0);
  let j = 0;
  return words.map((w) => {
    const letters = graphemes(w.t);
    const n = letters.length;
    let s = cue.start;
    let e = cue.start;
    if (st.highlightMode === "typewriter" && Number.isFinite(w.s)) {
      s = Math.min(Math.max(cue.start, w.s), eraseStart);
      const we = Number.isFinite(w.e) ? w.e : s;
      e = Math.min(Math.max(we, s), s + n * MAX_LETTER_SEC, eraseStart);
    }
    return letters.map((t, i) => {
      const show = s + ((e - s) * i) / Math.max(1, n);
      const hide = erase > 0 ? eraseStart + (erase * (j + 1)) / total : cue.end;
      j++;
      return { t, show, hide };
    });
  });
}
