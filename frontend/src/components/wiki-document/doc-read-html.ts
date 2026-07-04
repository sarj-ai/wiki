// Dark theming + tab splitting for Google's HTML export. Google sets an
// explicit hex color on every span (body text is color:#000000), so a static
// dark stylesheet loses on specificity — instead rewrite the hex values
// themselves by luminance: dark text lightens (hue kept), light backgrounds
// darken, borders normalize. The export's <body> is already the 8.5in "page";
// DARK_HEAD paints it as a dark paper sheet on a darker canvas.
// ponytail: Dark-Reader-lite via string regex, no DOM/CSSOM pass. Ceilings: hex
// colors only (Google exports emit hex exclusively), and prose that literally
// contains "color: #hex" (CSS snippets in a doc) gets rewritten too. Upgrade to
// a real CSSOM walk if either bites.

// Paged.js re-flows the export into discrete Letter pages like the Docs canvas.
// The export's own body padding (the page margins) moves to @page; each
// .pagedjs_page renders as a paper sheet on the canvas.
// ponytail: fixed Letter/1in — read the doc's real page setup from the Docs API
// if mixed page sizes ever matter.
const PAGE_SETUP = `<style>
  @page { size: Letter; margin: 1in; }
  body { margin: 0 !important; padding: 0 !important; max-width: none !important; background: transparent !important; }
  .pagedjs_pages { display: flex; flex-direction: column; align-items: center; gap: 16px; padding: 16px 8px; }
  .pagedjs_page { background: var(--paper); border-radius: 2px; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35); }
</style><script src="/vendor/paged.polyfill.js"></script>`;

// Light variant mirrors the website's light palette (globals.css :root) so the
// reading surface matches the app instead of Google's plain white.
const LIGHT_HEAD = `<base target="_blank"><style>
  :root { --paper: #ffffff; }
  html { background: #ede8dd !important; }
</style>${PAGE_SETUP}`;

const DARK_HEAD = `<base target="_blank"><style>
  :root { color-scheme: dark; --paper: #1e1e1e; }
  html { background: #131313 !important; }
  body { color: #e6e6e6; }
  a { color: #8ab4f8; }
</style>${PAGE_SETUP}`;

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

export interface DocSection {
  title: string;
  html: string;
}

export function themedSrcdoc(html: string, dark: boolean): string {
  const base = dark ? recolorDeclarations(html) : html;
  const themed = addFontFallbacks(base)
    .replaceAll(BROKEN_BULLET, 'content:"•  "')
    .replaceAll(HASH_ANCHOR, '<a target="_self"$1href="#');
  const head = dark ? DARK_HEAD : LIGHT_HEAD;
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
// — inline style="" attributes would need quote-safe parsing.
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
