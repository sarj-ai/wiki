// Dark theming + tab splitting for Google's HTML export. Google sets an
// explicit hex color on every span (body text is color:#000000), so a static
// dark stylesheet loses on specificity — instead rewrite the hex values
// themselves by luminance: dark text lightens (hue kept), light backgrounds
// darken, borders normalize.
//
// Layout + chrome mirrors the pre-Navigator native DocViewer design system
// (purple accents, Nunito, polished tables, status pills) — not Google's
// plain export look. Continuous scroll on the app canvas, no paper sheets.
// ponytail: Dark-Reader-lite via string regex, no DOM/CSSOM pass. Ceilings: hex
// colors only (Google exports emit hex exclusively), and prose that literally
// contains "color: #hex" (CSS snippets in a doc) gets rewritten too. Upgrade to
// a real CSSOM walk if either bites.

// Tokens from the pre-Navigator design system (and today's brand purple).
// Dark secondary #4B3483 was the signature purple accent; light #D0C3DF / #251040.
const TOKENS = {
  light: {
    bg: "#f4f2ef",
    fg: "#1a1a1a",
    muted: "#737373",
    card: "#ffffff",
    border: "#d5d5d5",
    secondary: "#d0c3df",
    secondaryFg: "#251040",
    accent: "#4b3483",
    selection: "rgba(208, 195, 223, 0.45)",
  },
  dark: {
    bg: "#161618",
    fg: "#fafafa",
    muted: "#a1a1a1",
    card: "#1c1c1e",
    border: "rgba(255, 255, 255, 0.1)",
    secondary: "#4b3483",
    secondaryFg: "#fafafa",
    accent: "#9b87d4",
    selection: "rgba(75, 52, 131, 0.45)",
  },
} as const;

// Self-hosted Nunito (same faces as the app shell). Paths resolve against the
// parent origin; the read-view iframe uses allow-same-origin so @font-face can
// load them without CORS pain, and still has no allow-scripts.
const FONT_FACES = `
@font-face {
  font-family: "Nunito";
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url("/fonts/Nunito-Regular.ttf") format("truetype");
}
@font-face {
  font-family: "Nunito";
  font-style: normal;
  font-weight: 500;
  font-display: swap;
  src: url("/fonts/Nunito-Medium.ttf") format("truetype");
}
@font-face {
  font-family: "Nunito";
  font-style: normal;
  font-weight: 600;
  font-display: swap;
  src: url("/fonts/Nunito-SemiBold.ttf") format("truetype");
}
@font-face {
  font-family: "Nunito";
  font-style: normal;
  font-weight: 700;
  font-display: swap;
  src: url("/fonts/Nunito-Bold.ttf") format("truetype");
}
@font-face {
  font-family: "Nunito";
  font-style: normal;
  font-weight: 800;
  font-display: swap;
  src: url("/fonts/Nunito-Bold.ttf") format("truetype");
}
`;

type SkinTokens = (typeof TOKENS)[keyof typeof TOKENS];

function classicSkin(dark: boolean): string {
  const t = dark ? TOKENS.dark : TOKENS.light;
  return `<base target="_blank"><style>
${FONT_FACES}
${skinBase(t, dark)}
${skinType(t)}
${skinTables(t, dark)}
${skinStatus(t, dark)}
${skinMisc(t, dark)}
</style>`;
}

function skinBase(t: SkinTokens, dark: boolean): string {
  return `
:root { color-scheme: ${dark ? "dark" : "light"}; }
html {
  height: 100%;
  background: ${t.bg} !important;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
body {
  max-width: 64rem !important;
  margin: 0 auto !important;
  padding: 2rem 1.75rem 4rem !important;
  min-height: 100%;
  box-sizing: border-box !important;
  box-shadow: none !important;
  background: transparent !important;
  color: ${t.fg} !important;
  font-family: "Nunito", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif !important;
  font-size: 1rem !important;
  line-height: 1.8 !important;
  letter-spacing: 0.01em;
}
/* Override Google's per-span font-family so the doc reads in Nunito, not Arial/Times. */
body, body p, body span, body td, body th, body li, body a, body h1, body h2, body h3 {
  font-family: "Nunito", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif !important;
}
::selection { background: ${t.selection}; color: ${t.fg}; }
`;
}

function skinType(t: SkinTokens): string {
  return `
/* ── Title / headings (Google uses p.title, not real heading tags) ── */
p.title {
  font-size: 2.25rem !important;
  font-weight: 800 !important;
  letter-spacing: -0.03em !important;
  line-height: 1.2 !important;
  color: ${t.fg} !important;
  margin: 0 0 1.25rem !important;
  padding-bottom: 0.35em !important;
  border-bottom: 2px solid ${t.secondary} !important;
}
p.title span { font-size: inherit !important; font-weight: inherit !important; color: inherit !important; }
p.subtitle {
  font-size: 1.25rem !important;
  font-weight: 400 !important;
  line-height: 1.6 !important;
  color: ${t.muted} !important;
  margin-top: 0.25em !important;
}
h1 {
  font-size: 1.875rem !important;
  font-weight: 700 !important;
  letter-spacing: -0.025em !important;
  line-height: 1.3 !important;
  margin-top: 1.4em !important;
  margin-bottom: 0.25em !important;
  padding-bottom: 0.2em !important;
  border-bottom: 2px solid ${t.secondary} !important;
  color: ${t.fg} !important;
}
h2 {
  font-size: 1.5rem !important;
  font-weight: 650 !important;
  letter-spacing: -0.02em !important;
  line-height: 1.35 !important;
  margin-top: 1.2em !important;
  color: ${t.fg} !important;
}
h3 {
  font-size: 1.25rem !important;
  font-weight: 600 !important;
  letter-spacing: -0.015em !important;
  margin-top: 1em !important;
  color: ${t.fg} !important;
}
p { margin-top: 0.35em !important; margin-bottom: 0 !important; }
strong, b { font-weight: 650 !important; color: ${t.fg} !important; }

/* ── Links: purple underline (classic doc-link), not Google blue ── */
a {
  color: ${t.fg} !important;
  text-decoration: underline !important;
  text-decoration-color: ${t.secondary} !important;
  text-underline-offset: 3px !important;
  text-decoration-thickness: 2px !important;
  transition: text-decoration-color 0.15s ease;
}
a:hover { text-decoration-color: ${t.fg} !important; }
a span { color: inherit !important; }

/* ── Lists: purple bullets ── */
ul { list-style: none !important; padding-inline-start: 1.5em !important; }
ul > li { position: relative !important; padding-inline-start: 0.5em !important; line-height: 1.7 !important; }
ul > li::before {
  content: "" !important;
  position: absolute !important;
  inset-inline-start: -0.9em !important;
  top: 0.75em !important;
  width: 6px !important;
  height: 6px !important;
  border-radius: 50% !important;
  background: ${t.secondary} !important;
}
ol { padding-inline-start: 1.5em !important; }
ol > li::marker { color: ${t.muted} !important; font-weight: 500 !important; }
`;
}

function skinTables(t: SkinTokens, dark: boolean): string {
  const shadow = dark
    ? "0 0 0 1px rgba(255,255,255,0.02), 0 2px 8px rgba(0,0,0,0.3)"
    : "0 1px 3px rgba(0,0,0,0.04)";
  const headerBg = dark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.02)";
  const headerBorder = dark ? "rgba(75, 52, 131, 0.45)" : "rgba(208, 195, 223, 0.9)";
  const zebra = dark ? "rgba(255,255,255,0.025)" : "rgba(0,0,0,0.015)";
  const hover = dark ? "rgba(75, 52, 131, 0.18)" : "rgba(208, 195, 223, 0.35)";
  return `
/* ── Tables: rounded card, uppercase header row, zebra, purple hover ── */
table {
  width: 100% !important;
  max-width: 100% !important;
  border-collapse: separate !important;
  border-spacing: 0 !important;
  margin: 1.75em 0 !important;
  font-size: 0.875rem !important;
  line-height: 1.6 !important;
  background: ${t.card} !important;
  border: 1px solid ${t.border} !important;
  border-radius: 0.75rem !important;
  overflow: hidden !important;
  box-shadow: ${shadow} !important;
}
table td, table th {
  padding: 0.65rem 1rem !important;
  vertical-align: top !important;
  border: none !important;
  border-top: 1px solid ${t.border} !important;
  color: ${t.fg} !important;
  background: transparent !important;
  overflow-wrap: break-word !important;
  word-break: normal !important;
}
table tr:first-child td,
table tr:first-child th {
  font-size: 0.7rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.05em !important;
  text-transform: uppercase !important;
  color: ${t.muted} !important;
  background: ${headerBg} !important;
  border-top: none !important;
  border-bottom: 2px solid ${headerBorder} !important;
}
table tr:first-child td span,
table tr:first-child th span {
  font-size: inherit !important;
  font-weight: inherit !important;
  letter-spacing: inherit !important;
  color: inherit !important;
  text-transform: inherit !important;
}
table tr:nth-child(even):not(:first-child) td { background: ${zebra} !important; }
table tr:not(:first-child):hover td { background: ${hover} !important; }
`;
}

function skinStatus(t: SkinTokens, dark: boolean): string {
  return `
/* ── Status pills (injected by promoteStatusBadges) ── */
.doc-status {
  display: inline-flex !important;
  align-items: center !important;
  padding: 0.15rem 0.55rem !important;
  border-radius: 9999px !important;
  font-size: 0.75rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.01em !important;
  white-space: nowrap !important;
  line-height: 1.4 !important;
  text-decoration: none !important;
  border: none !important;
}
.doc-status-active {
  background: ${dark ? "rgba(96, 165, 250, 0.15)" : "rgba(59, 130, 246, 0.15)"} !important;
  color: ${dark ? "#60a5fa" : "#2563eb"} !important;
}
.doc-status-success {
  background: ${dark ? "rgba(74, 222, 128, 0.15)" : "rgba(34, 197, 94, 0.15)"} !important;
  color: ${dark ? "#4ade80" : "#16a34a"} !important;
}
.doc-status-warning {
  background: ${dark ? "rgba(251, 191, 36, 0.15)" : "rgba(245, 158, 11, 0.15)"} !important;
  color: ${dark ? "#fbbf24" : "#d97706"} !important;
}
.doc-status-blocked {
  background: ${dark ? "rgba(248, 113, 113, 0.15)" : "rgba(239, 68, 68, 0.12)"} !important;
  color: ${dark ? "#f87171" : "#dc2626"} !important;
}
.doc-status-neutral {
  background: ${dark ? "rgba(161, 161, 161, 0.15)" : "rgba(115, 115, 115, 0.12)"} !important;
  color: ${t.muted} !important;
}
a:has(> .doc-status) { text-decoration: none !important; border-radius: 9999px !important; }
a:has(> .doc-status) > .doc-status { pointer-events: none; }
`;
}

function skinMisc(t: SkinTokens, dark: boolean): string {
  return `
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace !important; }
code {
  font-size: 0.875em !important;
  padding: 0.15em 0.4em !important;
  border-radius: 0.375rem !important;
  background: ${dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.04)"} !important;
  border: 1px solid ${t.border} !important;
}
hr {
  border: none !important;
  height: 1px !important;
  margin: 1.5em 0 !important;
  background: linear-gradient(to right, transparent, ${t.border}, ${t.border}, transparent) !important;
}
img { max-width: 100% !important; height: auto !important; border-radius: 0.375rem; }
`;
}

const COLOR_DECLARATION =
  /(color|background(?:-color)?|border[^:;{}]*|outline[^:;{}]*)(\s*:\s*)([^;}]+)/gi;

const HEX_TOKEN = /#([0-9a-f]{6}|[0-9a-f]{3})\b/gi;

const STYLE_BLOCK = /(<style[^>]*>)([\s\S]*?)(<\/style>)/gi;

const FONT_FAMILY = /(font-family\s*:\s*)([^;}]+)/gi;

const GENERIC_FAMILY = /\b(?:serif|sans-serif|monospace|inherit|initial|unset)\b|var\(/i;

// Google exports list bullet glyphs as literal question marks.
const BROKEN_BULLET = /content:"\?\s*"/g;

// In-doc links (TOC, bookmarks) are hash-only; without an explicit target they
// inherit <base target="_blank"> and open a new app tab instead of scrolling.
const HASH_ANCHOR = /<a\b(?![^>]*\btarget=)([^>]*?)href="#/g;

// Document tabs are flattened into sequential sections, each opening with the
// tab's name in a title-styled paragraph.
const TAB_TITLE = /<p class="title"[^>]*>([\s\S]*?)<\/p>/g;

const BODY_OPEN = /<body[^>]*>/;

const HTML_ENTITY = /&#(?:x([0-9a-f]+)|(\d+));|&(amp|quot|apos|lt|gt|nbsp);/gi;

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  quot: '"',
  apos: "'",
  lt: "<",
  gt: ">",
  nbsp: " ",
};

// Status labels the old native viewer turned into colored pills. Match leaf
// text nodes only (no nested tags) so we don't break markup.
const STATUS_LEAF =
  />(\s*)(Not started|In progress|In review|Blocked|Approved|Launched|Completed|Done|On hold|Paused|Cancelled|Canceled|Pending|Draft|Active|Deferred)(\s*)</gi;

const STATUS_CLASS: Record<string, string> = {
  "not started": "doc-status-neutral",
  draft: "doc-status-neutral",
  pending: "doc-status-warning",
  "on hold": "doc-status-warning",
  paused: "doc-status-warning",
  "in progress": "doc-status-active",
  "in review": "doc-status-active",
  active: "doc-status-active",
  approved: "doc-status-success",
  launched: "doc-status-success",
  completed: "doc-status-success",
  done: "doc-status-success",
  blocked: "doc-status-blocked",
  cancelled: "doc-status-blocked",
  canceled: "doc-status-blocked",
  deferred: "doc-status-warning",
};

export interface DocSection {
  title: string;
  html: string;
}

export function themedSrcdoc(html: string, dark: boolean): string {
  const base = dark ? recolorDeclarations(html) : html;
  const themed = promoteStatusBadges(
    addFontFallbacks(base)
      .replaceAll(BROKEN_BULLET, 'content:"•  "')
      .replaceAll(HASH_ANCHOR, '<a target="_self"$1href="#'),
  );
  const head = classicSkin(dark);
  return themed.includes("</head>") ? themed.replace("</head>", `${head}</head>`) : head + themed;
}

// Partition the export into one self-contained document per tab, so the viewer
// can rebuild the Document-tabs sidebar. A tabless doc yields a single section.
export function splitDocTabs(html: string, fallbackTitle: string): DocSection[] {
  const bodyOpen = BODY_OPEN.exec(html);
  const bodyEnd = html.lastIndexOf("</body>");
  if (!bodyOpen || bodyEnd < 0) return [{ title: fallbackTitle, html }];
  const bodyStart = bodyOpen.index + bodyOpen[0].length;

  const prefix = html.slice(0, bodyStart);
  const body = html.slice(bodyStart, bodyEnd);
  const suffix = html.slice(bodyEnd);

  const markers = [...body.matchAll(TAB_TITLE)];
  if (markers.length < 2) return [{ title: fallbackTitle, html }];

  return markers.map((marker, i) => {
    const next = markers[i + 1];
    const end = next === undefined ? body.length : next.index;
    return {
      title: decodeEntities((marker[1] ?? "").replaceAll(/<[^>]+>/g, "")).trim() || fallbackTitle,
      html: prefix + body.slice(marker.index, end) + suffix,
    };
  });
}

function recolorDeclarations(html: string): string {
  return html.replaceAll(COLOR_DECLARATION, (_match, prop: string, sep: string, value: string) => {
    const p = prop.toLowerCase();
    const kind =
      p === "color" || p.endsWith("-fill-color")
        ? "text"
        : p.startsWith("background")
          ? "bg"
          : "border";
    return prop + sep + value.replaceAll(HEX_TOKEN, (hex) => mapHex(hex, kind));
  });
}

// Docs fonts (Proxima Nova, Lato, …) are rarely installed locally; without a
// fallback the browser lands on Times and the doc reads serif. Style block only
// — inline style="" attributes would need quote-safe parsing. The classic skin
// then forces Nunito on body descendants with !important.
function addFontFallbacks(html: string): string {
  return html.replaceAll(STYLE_BLOCK, (_match, open: string, css: string, close: string) => {
    const patched = css.replaceAll(FONT_FAMILY, (decl, prefix: string, value: string) => {
      if (GENERIC_FAMILY.test(value)) return decl;
      const generic = /courier|mono/i.test(value) ? ",monospace" : ",Arial,sans-serif";
      return prefix + value + generic;
    });
    return open + patched + close;
  });
}

function promoteStatusBadges(html: string): string {
  return html.replaceAll(STATUS_LEAF, (_match, pre: string, label: string, post: string) => {
    const cls = STATUS_CLASS[label.toLowerCase()] ?? "doc-status-neutral";
    return `>${pre}<span class="doc-status ${cls}">${label}</span>${post}<`;
  });
}

function decodeEntities(text: string): string {
  return text.replaceAll(
    HTML_ENTITY,
    (_match, hex: string | undefined, dec: string | undefined, named: string | undefined) => {
      if (named !== undefined) return NAMED_ENTITIES[named.toLowerCase()] ?? "";
      return String.fromCodePoint(parseInt(hex ?? dec ?? "0", hex === undefined ? 10 : 16));
    },
  );
}

function mapHex(hex: string, kind: "text" | "bg" | "border"): string {
  const [h, s, l] = hexToHsl(hex);
  let mapped = l;
  if (kind === "text") {
    if (l < 0.55) mapped = 0.92 - l * 0.6; // black -> #ebebeb, mid tones -> light tints
  } else if (kind === "bg") {
    if (l > 0.5) mapped = 1 - l * 0.82; // white -> #2e2e2e, highlights -> dark tints
  } else if (l > 0.6) {
    mapped = 1 - l * 0.75; // light borders darken
  } else if (l < 0.3) {
    mapped = 0.38; // black borders become visible gray
  }
  return mapped === l ? hex : hslToHex(h, s, mapped);
}

function hexToHsl(hex: string): [number, number, number] {
  let value = hex.slice(1);
  if (value.length === 3) {
    value = value.replaceAll(/[0-9a-f]/gi, "$&$&");
  }
  const r = parseInt(value.slice(0, 2), 16) / 255;
  const g = parseInt(value.slice(2, 4), 16) / 255;
  const b = parseInt(value.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}

function hslToHex(h: number, s: number, l: number): string {
  const channel = (n: number): string => {
    const k = (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const c = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(c * 255)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${channel(0)}${channel(8)}${channel(4)}`;
}
