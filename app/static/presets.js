// Built-in style presets. `fonts` lists candidates; the first one installed wins
// (Windows names first, then common Linux/macOS equivalents).

const SANS = ["Arial", "Helvetica", "Liberation Sans", "Arimo", "DejaVu Sans", "Noto Sans", "Ubuntu"];
const BOLD_SANS = ["Montserrat ExtraBold", "Montserrat Black", "Montserrat", "Arial Black", "Impact", "Anton",
  "Liberation Sans", "DejaVu Sans", "Noto Sans", "Ubuntu"];
const ROUNDED = ["Roboto", "Segoe UI", "Open Sans", "Noto Sans", "Ubuntu", "Liberation Sans", "DejaVu Sans"];
const SERIF = ["Georgia", "Cambria", "Times New Roman", "Liberation Serif", "DejaVu Serif", "Noto Serif", "C059"];

const BASE_LAYOUT = { maxWords: 0, maxLines: 2, uppercase: false, wrapBy: "width", maxWidthPct: 80, stripPunct: false };

export const BUILTIN_PRESETS = [
  {
    name: "Classic",
    fonts: SANS,
    style: { fontSize: 64, bold: true, textColor: "#FFFFFF", outlineColor: "#000000", outlineWidth: 3,
      shadowColor: "#000000", shadowOpacity: 60, shadowDepth: 2, alignment: 2, marginV: 70 },
    layout: BASE_LAYOUT,
  },
  {
    name: "YouTube",
    fonts: ROUNDED,
    style: { fontSize: 56, bold: false, textColor: "#FFFFFF", outlineWidth: 0, shadowDepth: 0, boxEnabled: true,
      boxColor: "#000000", boxOpacity: 75, boxPadding: 10, alignment: 2, marginV: 60 },
    layout: BASE_LAYOUT,
  },
  {
    name: "Netflix",
    fonts: SANS,
    style: { fontSize: 60, bold: false, textColor: "#FFFFFF", outlineColor: "#000000", outlineWidth: 2,
      shadowColor: "#000000", shadowOpacity: 45, shadowDepth: 3, blur: 1, alignment: 2, marginV: 80 },
    layout: { ...BASE_LAYOUT, maxWidthPct: 70 },
  },
  {
    name: "TikTok Pop",
    fonts: BOLD_SANS,
    style: { fontSize: 92, bold: true, textColor: "#FFFFFF", outlineColor: "#000000", outlineWidth: 7,
      shadowColor: "#000000", shadowOpacity: 50, shadowDepth: 3, alignment: 2, marginV: 260,
      highlightMode: "word", highlightColor: "#FFE600", highlightScale: 112 },
    layout: { ...BASE_LAYOUT, maxWords: 3, maxLines: 2, uppercase: true, maxWidthPct: 70, stripPunct: true },
  },
  {
    name: "Karaoke",
    fonts: BOLD_SANS,
    style: { fontSize: 72, bold: true, textColor: "#FFFFFF", outlineColor: "#101030", outlineWidth: 4,
      shadowDepth: 2, shadowOpacity: 60, alignment: 2, marginV: 90, highlightMode: "fill", highlightColor: "#00E5FF" },
    layout: { ...BASE_LAYOUT, maxLines: 2 },
  },
  {
    name: "Word Reveal",
    fonts: BOLD_SANS,
    style: { fontSize: 80, bold: true, textColor: "#FFFFFF", outlineColor: "#000000", outlineWidth: 5,
      shadowDepth: 0, alignment: 2, marginV: 220, highlightMode: "reveal", highlightColor: "#FFFFFF" },
    layout: { ...BASE_LAYOUT, maxWords: 6, maxWidthPct: 75 },
  },
  {
    name: "Typewriter",
    fonts: SANS,
    style: { fontSize: 66, bold: true, textColor: "#FFFFFF", outlineColor: "#000000", outlineWidth: 3,
      shadowDepth: 2, shadowOpacity: 60, alignment: 1, marginV: 120, marginL: 120, highlightMode: "typewriter",
      eraseOut: true, eraseMs: 300 },
    layout: { ...BASE_LAYOUT, maxWidthPct: 70 },
  },
  {
    name: "Yellow DVD",
    fonts: SANS,
    style: { fontSize: 62, bold: true, textColor: "#FFE135", outlineColor: "#000000", outlineWidth: 3,
      shadowDepth: 2, shadowOpacity: 70, alignment: 2, marginV: 60 },
    layout: BASE_LAYOUT,
  },
  {
    name: "Boxed Yellow",
    fonts: BOLD_SANS,
    style: { fontSize: 66, bold: true, textColor: "#111111", outlineWidth: 0, shadowDepth: 0, boxEnabled: true,
      boxColor: "#FFD400", boxOpacity: 100, boxPadding: 14, alignment: 2, marginV: 120 },
    layout: { ...BASE_LAYOUT, maxWidthPct: 70 },
  },
  {
    name: "Minimal",
    fonts: ROUNDED,
    style: { fontSize: 52, bold: false, textColor: "#FFFFFF", outlineWidth: 0, shadowColor: "#000000",
      shadowOpacity: 85, shadowDepth: 1.5, blur: 2, alignment: 2, marginV: 60 },
    layout: BASE_LAYOUT,
  },
  {
    name: "Neon",
    fonts: BOLD_SANS,
    style: { fontSize: 70, bold: true, textColor: "#FFFFFF", outlineColor: "#FF2DAA", outlineWidth: 3.5, blur: 3,
      shadowColor: "#00E5FF", shadowOpacity: 80, shadowDepth: 3, alignment: 2, marginV: 90 },
    layout: BASE_LAYOUT,
  },
  {
    name: "Cinema",
    fonts: SERIF,
    style: { fontSize: 60, bold: false, italic: true, textColor: "#F4F1E8", textOpacity: 96, outlineWidth: 1,
      outlineColor: "#000000", shadowOpacity: 70, shadowDepth: 2, blur: 1, alignment: 2, marginV: 70, letterSpacing: 1 },
    layout: { ...BASE_LAYOUT, maxWidthPct: 70 },
  },
  {
    name: "Top Caption",
    fonts: SANS,
    style: { fontSize: 58, bold: true, textColor: "#FFFFFF", outlineWidth: 0, shadowDepth: 0, boxEnabled: true,
      boxColor: "#7C5CFF", boxOpacity: 90, boxPadding: 12, alignment: 8, marginV: 60 },
    layout: BASE_LAYOUT,
  },
];

export const SWATCHES = ["#FFFFFF", "#FFE600", "#FFD400", "#00E5FF", "#39FF14", "#FF2DAA", "#FF7A00", "#FF3B30", "#7C5CFF", "#111111"];
