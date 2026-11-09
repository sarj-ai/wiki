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
  "https://docs.google.com/document/d/15bCeAdgUfl65nQ9OGR1Q-7GhU52lbZZxFwr78dJK0j8";
const DRAFT_BASE =
  "https://docs.google.com/document/d/1at-tDSuHIho4SsnetRaOz7_SZj54FEd4WZym51wmZIs";
const WORKBOOK_BASE =
  "https://docs.google.com/spreadsheets/d/1y9828lQc8IsOPVIxibb7pkFan5Tw2vUwurHhdhriGGc";
const SLIDES_BASE =
  "https://docs.google.com/presentation/d/18ndeWAssiqogeu9_itNYHTDxOWbb8FplV_QTjX9tH90";
const WORKBOOK_PREVIEW = `${WORKBOOK_BASE}/preview#gid=1174504614`;

const GUIDE_FILE: DemoFile = {
  description: "A guide to Google SRE latency estimates.",
  downloadFilename: "latency-guide.docx",
  downloadUrl: `${GUIDE_BASE}/export?format=docx`,
  format: "Document",
  id: "latency-guide",
  keywords:
    "latency guide Google SRE reference estimates cache memory storage network",
  mimeType: MIME_DOC,
  name: "Latency guide",
  previewUrl: `${GUIDE_BASE}/preview`,
  viewUrl: `${GUIDE_BASE}/preview`,
};

export const DEMO_FILES: readonly DemoFile[] = [
  GUIDE_FILE,
  {
    description: "Working notes on Google SRE latency estimates.",
    downloadFilename: "ai-draft.docx",
    downloadUrl: `${DRAFT_BASE}/export?format=docx`,
    format: "Document",
    id: "ai-draft",
    keywords: "AI draft notes document prose Google SRE latency reference",
    mimeType: MIME_DOC,
    name: "AI draft",
    previewUrl: `${DRAFT_BASE}/preview`,
    viewUrl: `${DRAFT_BASE}/preview`,
  },
  {
    description: "Latency comparisons, conversions, and worked calculations.",
    downloadFilename: "latency-workbook.xlsx",
    downloadUrl: `${WORKBOOK_BASE}/export?format=xlsx`,
    format: "Spreadsheet",
    id: "latency-workbook",
    keywords:
      "workbook spreadsheet formulas units conversions comparison calculations",
    mimeType: MIME_SHEET,
    name: "Latency workbook",
    previewUrl: WORKBOOK_PREVIEW,
    viewUrl: WORKBOOK_PREVIEW,
  },
  {
    description: "A visual introduction to latency and throughput.",
    downloadFilename: "latency-slides.pptx",
    downloadUrl: `${SLIDES_BASE}/export/pptx`,
    format: "Presentation",
    id: "latency-slides",
    keywords:
      "slides presentation deck latency throughput Google SRE reference",
    mimeType: MIME_SLIDE,
    name: "Latency slides",
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
