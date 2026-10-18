import { test } from "node:test";
import assert from "node:assert/strict";
import { buildCues, retimeWords, joinWords, tokenize, graphemes, letterTimeline } from "../app/static/layout.js";

const words = (text, start = 0, step = 0.3) =>
  text.split(" ").map((t, i) => ({ t, s: start + i * step, e: start + i * step + step * 0.9 }));
const seg = (text, start = 0) => {
  const w = words(text, start);
  return { start, end: w.at(-1).e, text, words: w };
};
const chars = (t) => [...t].length;

test("wraps by characters and respects max lines", () => {
  const cues = buildCues([seg("one two three four five six seven eight nine ten")], { wrapBy: "chars", maxChars: 10, maxLines: 2, breakOnSentence: false }, chars, 10);
  for (const c of cues) {
    assert.ok(c.lines.length <= 2);
    for (const l of c.lines) assert.ok(joinWords(l.map((w) => w.t)).length <= 10);
  }
  assert.equal(cues.flatMap((c) => c.lines.flat()).length, 10);
});

test("max words per cue", () => {
  const cues = buildCues([seg("a b c d e f g")], { maxWords: 3, breakOnSentence: false }, chars, 100);
  assert.deepEqual(cues.map((c) => c.lines.flat().length), [3, 3, 1]);
});

test("splits at long pauses and at sentences", () => {
  const s = seg("Hello there. How are you");
  s.words[3].s += 5; s.words[3].e += 5; s.words[4].s += 5; s.words[4].e += 5;
  const cues = buildCues([s], {}, chars, 100);
  assert.deepEqual(cues.map((c) => joinWords(c.lines.flat().map((w) => w.t))), ["Hello there.", "How", "are you"]);
});

test("balanced two-line split", () => {
  const cues = buildCues([seg("aaaa bbbb cccc dddd eeee")], { wrapBy: "chars", maxChars: 20, maxLines: 2, breakOnSentence: false }, chars, 20);
  const [l1, l2] = cues[0].lines.map((l) => joinWords(l.map((w) => w.t)).length);
  assert.ok(Math.abs(l1 - l2) <= 5, `${l1} vs ${l2}`);
});

test("no overlaps and min duration", () => {
  const cues = buildCues([seg("a b c d e f", 0)], { maxWords: 1, minDuration: 1, breakOnSentence: false }, chars, 100);
  for (let i = 0; i + 1 < cues.length; i++) assert.ok(cues[i].end <= cues[i + 1].start);
});

test("uppercase + strip punctuation", () => {
  const cues = buildCues([seg("hi, there.")], { uppercase: true, stripPunct: true }, chars, 100);
  assert.equal(joinWords(cues[0].lines.flat().map((w) => w.t)), "HI THERE");
});

test("retime keeps timings when word count matches", () => {
  const s = seg("helo world");
  const w = retimeWords(s, "hello world");
  assert.equal(w[0].t, "hello");
  assert.equal(w[0].s, s.words[0].s);
});

test("tokenize splits CJK per character", () => {
  assert.deepEqual(tokenize("你好 world"), ["你", "好", "world"]);
});

const twCue = {
  start: 1, end: 3,
  lines: [[{ t: "Hi", s: 1, e: 1.2 }, { t: "you", s: 1.5, e: 1.8 }]],
};

test("graphemes keep Indic conjuncts and marks together", () => {
  assert.deepEqual(graphemes("hey"), ["h", "e", "y"]);
  assert.deepEqual(graphemes("क्षमा"), ["क्ष", "मा"]);
});

test("typewriter types letters across each spoken word", () => {
  const tl = letterTimeline(twCue, { highlightMode: "typewriter" });
  assert.deepEqual(tl[0].map((l) => +l.show.toFixed(3)), [1, 1.1]);
  assert.deepEqual(tl[1].map((l) => +l.show.toFixed(3)), [1.5, 1.6, 1.7]);
  assert.ok(tl.flat().every((l) => l.hide === 3));
});

test("erase hides letters left to right inside the last eraseMs", () => {
  const tl = letterTimeline(twCue, { highlightMode: "none", eraseOut: true, eraseMs: 500 }).flat();
  assert.ok(tl.every((l) => l.show === 1));
  assert.equal(+tl[0].hide.toFixed(3), 2.6);
  assert.equal(tl.at(-1).hide, 3);
  for (let i = 1; i < tl.length; i++) assert.ok(tl[i].hide > tl[i - 1].hide);
});

test("per-line placement is carried from segment to cue", () => {
  const seg = { start: 0, end: 1, text: "a b", words: [{ t: "a", s: 0, e: 0.4 }, { t: "b", s: 0.5, e: 0.9 }], place: { alignment: 8 } };
  const cues = buildCues([seg], {}, (s) => s.length, 100);
  assert.deepEqual(cues[0].place, { alignment: 8 });
});
