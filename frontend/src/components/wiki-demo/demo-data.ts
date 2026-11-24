import { MIME_DOC, MIME_SHEET } from "@/types/wiki";

export interface DemoFile {
  description: string;
  downloadFilename: string;
  downloadUrl: string;
  format: string;
  id: string;
  keywords: string;
  mimeType: string;
  name: string;
  previewUrl: string;
  viewUrl: string;
}

export const DEMO_WORKSPACE_NAME = "Sarj Wiki";
export const DEMO_SEARCH_SUGGESTION =
  "Try “unslop”, “NYC”, “guide”, or “sheets”.";

const DRAFT_BASE =
  "https://docs.google.com/document/d/1ld4ojYAI6fZwS0NTqkz6DVNx12Mdypsv8kWhA6zVco8";
const WORKBOOK_BASE =
  "https://docs.google.com/spreadsheets/d/1YdtEf6kXaO5t9-hg9BD4o7QExJsikqfmhoiTSDP0oP0";
const AI_BEFORE_BASE =
  "https://docs.google.com/document/d/1PIYA6mIuDLh1vwPb-yM9DzdX9vv8t3xmGtvbdOu4zHs";
const AI_AFTER_BASE =
  "https://docs.google.com/document/d/1-qCwZc5ja9JsVxFfG1zb2vs-DvRPaun9HzmBy6lmNJU";
const NYC_EXAMPLE_BASE =
  "https://docs.google.com/document/d/1q24LIwuOzlXnh1xZFb7BoSzTNsr29nyfAAnwmEVv2Yg";
const WORKBOOK_PREVIEW = `${WORKBOOK_BASE}/preview#gid=696566412`;

const AI_BEFORE_FILE: DemoFile = {
  description:
    "An incident review drafted in a padded AI style, before Wiki Unslop and Format.",
  downloadFilename: "ai-example-before.docx",
  downloadUrl: `${AI_BEFORE_BASE}/export?format=docx`,
  format: "Document",
  id: "unslop-ai-before",
  keywords:
    "unslop format AI example before draft incident review prose comparison",
  mimeType: MIME_DOC,
  name: "AI example · Before",
  previewUrl: `${AI_BEFORE_BASE}/preview`,
  viewUrl: `${AI_BEFORE_BASE}/preview`,
};

export const DEMO_FILES: readonly DemoFile[] = [
  AI_BEFORE_FILE,
  {
    description:
      "The same incident review after Wiki Unslop and Format: 416 body words reduced to 281.",
    downloadFilename: "ai-example-after.docx",
    downloadUrl: `${AI_AFTER_BASE}/export?format=docx`,
    format: "Document",
    id: "unslop-ai",
    keywords: "unslop format AI example after incident review prose comparison",
    mimeType: MIME_DOC,
    name: "AI example · After",
    previewUrl: `${AI_AFTER_BASE}/preview`,
    viewUrl: `${AI_AFTER_BASE}/preview`,
  },
  {
    description: "An AI draft refined into a concise Google SRE reference.",
    downloadFilename: "numbers-ai-draft.docx",
    downloadUrl: `${DRAFT_BASE}/export?format=docx&tab=t.0`,
    format: "Document",
    id: "ai-draft",
    keywords:
      "numbers programmer AI draft document prose Google SRE latency reference",
    mimeType: MIME_DOC,
    name: "AI draft",
    previewUrl: `${DRAFT_BASE}/preview`,
    viewUrl: `${DRAFT_BASE}/preview`,
  },
  {
    description:
      "Google SRE reference values, formulas, and worked calculations.",
    downloadFilename: "numbers-workbook.xlsx",
    downloadUrl: `${WORKBOOK_BASE}/export?format=xlsx`,
    format: "Spreadsheet",
    id: "latency-workbook",
    keywords:
      "numbers programmer latency workbook spreadsheet formulas units conversions comparison calculations Google SRE reference",
    mimeType: MIME_SHEET,
    name: "Sheets Demo",
    previewUrl: WORKBOOK_PREVIEW,
    viewUrl: WORKBOOK_PREVIEW,
  },
  {
    description:
      "The New York City weekend guide for November 13–15, 2026, with Before and After tabs. Wiki Unslop and Format: 854 body words reduced to 746.",
    downloadFilename: "nyc-weekend-before-and-after.docx",
    downloadUrl: `${NYC_EXAMPLE_BASE}/export?format=docx`,
    format: "Document",
    id: "nyc-weekend",
    keywords:
      "NYC New York weekend before after guide November 13 14 15 2026 unslop comparison tabs",
    mimeType: MIME_DOC,
    name: "NYC weekend · Before and After",
    previewUrl: `${NYC_EXAMPLE_BASE}/preview`,
    viewUrl: `${NYC_EXAMPLE_BASE}/preview`,
  },
];

export function demoFileFor(id: null | string): DemoFile {
  return DEMO_FILES.find((file) => file.id === id) ?? AI_BEFORE_FILE;
}

export function demoHref(file: DemoFile): `/demo?file=${string}` {
  return `/demo?file=${encodeURIComponent(file.id)}`;
}
