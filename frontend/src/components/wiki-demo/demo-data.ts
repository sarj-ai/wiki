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
  "Try “guide”, “draft”, “workbook”, or “slides”.";

const GUIDE_BASE =
  "https://docs.google.com/document/d/1kdnYpl3uHMASWYA0Pd92RhXpOoTFrJdYEspn6TTmQ74";
const DRAFT_BASE =
  "https://docs.google.com/document/d/1OABiTptZ9hM6NOoa5FCLR8OoTEVg7VU6EdE0JTZIt4I";
const WORKBOOK_BASE =
  "https://docs.google.com/spreadsheets/d/1YdtEf6kXaO5t9-hg9BD4o7QExJsikqfmhoiTSDP0oP0";
const SLIDES_BASE =
  "https://docs.google.com/presentation/d/1tPlL5sQBQtfRzOcHTe8F0BYZ70RNMAo5a5E_G-D1tgs";
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
];

export function demoFileFor(id: null | string): DemoFile {
  return DEMO_FILES.find((file) => file.id === id) ?? GUIDE_FILE;
}

export function demoHref(file: DemoFile): `/demo?file=${string}` {
  return `/demo?file=${encodeURIComponent(file.id)}`;
}
