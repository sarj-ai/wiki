import { MIME_DOC, MIME_SHEET, MIME_SLIDE } from "@/types/wiki";

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
  "Try “unslop”, “NYC”, “guide”, or “slides”.";

const GUIDE_BASE =
  "https://docs.google.com/document/d/1kdnYpl3uHMASWYA0Pd92RhXpOoTFrJdYEspn6TTmQ74";
const DRAFT_BASE =
  "https://docs.google.com/document/d/1ld4ojYAI6fZwS0NTqkz6DVNx12Mdypsv8kWhA6zVco8";
const WORKBOOK_BASE =
  "https://docs.google.com/spreadsheets/d/1YdtEf6kXaO5t9-hg9BD4o7QExJsikqfmhoiTSDP0oP0";
const SLIDES_BASE =
  "https://docs.google.com/presentation/d/1tPlL5sQBQtfRzOcHTe8F0BYZ70RNMAo5a5E_G-D1tgs";
const AI_BEFORE_BASE =
  "https://docs.google.com/document/d/1U7L649uf2E94_5yeSIi7ijkYvsVVBau_-uLYfzTFIyc";
const AI_AFTER_BASE =
  "https://docs.google.com/document/d/169R8wVks02yMX6eg2I_U-zwF4XUqMZnfbj8fct-QR_U";
const NYC_BEFORE_BASE =
  "https://docs.google.com/document/d/1jU-ERlxXqfzS7Knipi-9R1GECrY-sDJRj2sV9DXgBtI";
const NYC_AFTER_BASE =
  "https://docs.google.com/document/d/19V4tScsSjVEDGZl-ImvclS2UUsaga02PIeaZmR_w-pk";
const WORKBOOK_PREVIEW = `${WORKBOOK_BASE}/preview#gid=696566412`;

const GUIDE_FILE: DemoFile = {
  description: "Reference estimates, a formatted table, and code examples.",
  downloadFilename: "numbers-guide.docx",
  downloadUrl: `${GUIDE_BASE}/export?format=docx&tab=t.0`,
  format: "Document",
  id: "latency-guide",
  keywords:
    "numbers programmer latency guide Google SRE reference estimates cache memory storage network Python TypeScript SQL code formatting",
  mimeType: MIME_DOC,
  name: "Numbers Every Programmer Should Know",
  previewUrl: `${GUIDE_BASE}/preview`,
  viewUrl: `${GUIDE_BASE}/preview`,
};

export const DEMO_FILES: readonly DemoFile[] = [
  GUIDE_FILE,
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
    name: "Numbers workbook",
    previewUrl: WORKBOOK_PREVIEW,
    viewUrl: WORKBOOK_PREVIEW,
  },
  {
    description: "A visual reference to the Google SRE estimates.",
    downloadFilename: "numbers-slides.pptx",
    downloadUrl: `${SLIDES_BASE}/export/pptx`,
    format: "Presentation",
    id: "latency-slides",
    keywords:
      "numbers programmer slides presentation deck latency throughput Google SRE reference",
    mimeType: MIME_SLIDE,
    name: "Numbers slides",
    previewUrl: `${SLIDES_BASE}/preview`,
    viewUrl: `${SLIDES_BASE}/preview`,
  },
  {
    description:
      "A deliberately padded AI draft with reference values and a code example.",
    downloadFilename: "ai-example-before.docx",
    downloadUrl: `${AI_BEFORE_BASE}/export?format=docx&tab=t.0`,
    format: "Document",
    id: "unslop-ai-before",
    keywords:
      "unslop AI example before draft latency reference prose code comparison",
    mimeType: MIME_DOC,
    name: "AI example · Before",
    previewUrl: `${AI_BEFORE_BASE}/preview`,
    viewUrl: `${AI_BEFORE_BASE}/preview`,
  },
  {
    description:
      "The same AI draft after Unslop: 303 body words reduced to 158.",
    downloadFilename: "ai-example-after.docx",
    downloadUrl: `${AI_AFTER_BASE}/export?format=docx&tab=t.0`,
    format: "Document",
    id: "unslop-ai-after",
    keywords:
      "unslop AI example after concise latency reference prose code comparison",
    mimeType: MIME_DOC,
    name: "AI example · After",
    previewUrl: `${AI_AFTER_BASE}/preview`,
    viewUrl: `${AI_AFTER_BASE}/preview`,
  },
  {
    description:
      "The original New York City weekend guide for November 13–15, 2026.",
    downloadFilename: "nyc-weekend-before.docx",
    downloadUrl: `${NYC_BEFORE_BASE}/export?format=docx&tab=t.0`,
    format: "Document",
    id: "nyc-before",
    keywords:
      "NYC New York weekend before guide November 13 14 15 2026 unslop comparison",
    mimeType: MIME_DOC,
    name: "NYC weekend · Before",
    previewUrl: `${NYC_BEFORE_BASE}/preview`,
    viewUrl: `${NYC_BEFORE_BASE}/preview`,
  },
  {
    description:
      "An edited and formatted version of the same historical weekend guide.",
    downloadFilename: "nyc-weekend-after.docx",
    downloadUrl: `${NYC_AFTER_BASE}/export?format=docx&tab=t.0`,
    format: "Document",
    id: "nyc-after",
    keywords:
      "NYC New York weekend after polished formatted guide November 13 14 15 2026 unslop comparison",
    mimeType: MIME_DOC,
    name: "NYC weekend · After",
    previewUrl: `${NYC_AFTER_BASE}/preview`,
    viewUrl: `${NYC_AFTER_BASE}/preview`,
  },
];

export function demoFileFor(id: null | string): DemoFile {
  return DEMO_FILES.find((file) => file.id === id) ?? GUIDE_FILE;
}

export function demoHref(file: DemoFile): `/demo?file=${string}` {
  return `/demo?file=${encodeURIComponent(file.id)}`;
}
