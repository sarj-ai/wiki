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
  "Try “incident”, “NYC”, “trail map”, or “sheets”.";

function documentFile(
  documentId: string,
  details: Pick<
    DemoFile,
    "description" | "downloadFilename" | "id" | "keywords" | "name"
  >,
): DemoFile {
  const base = `https://docs.google.com/document/d/${documentId}`;
  const preview = `${base}/preview`;
  return {
    ...details,
    downloadUrl: `${base}/export?format=docx`,
    format: "Document",
    mimeType: MIME_DOC,
    previewUrl: preview,
    viewUrl: preview,
  };
}

const AI_BEFORE_FILE = documentFile(
  "1sOpAx9F5lZsZhzxK7ciM5CKog45UQ0M-Pne8zE0DGBo",
  {
    description:
      "Checkout incident review with the incident timeline, rollback commands and follow-up owners. 160 body words.",
    downloadFilename: "ai-example-before.docx",
    id: "unslop-ai-before",
    keywords:
      "unslop format before checkout incident review payment gateway timeline table rollback commands",
    name: "AI example · Before",
  },
);

const WORKBOOK_BASE =
  "https://docs.google.com/spreadsheets/d/1YdtEf6kXaO5t9-hg9BD4o7QExJsikqfmhoiTSDP0oP0";
const WORKBOOK_PREVIEW = `${WORKBOOK_BASE}/preview#gid=696566412`;

export const DEMO_FILES: readonly DemoFile[] = [
  AI_BEFORE_FILE,
  documentFile("1NQ3WcuEHQ8IvzbK7A8ZSV5e5eKL4n25aW83oBEvUR1c", {
    description:
      "Checkout incident review with the incident timeline, rollback commands and follow-up owners. Wiki Unslop and Format took the draft from 160 body words to 133.",
    downloadFilename: "ai-example-after.docx",
    id: "unslop-ai",
    keywords:
      "unslop format after checkout incident review payment gateway timeline table rollback commands",
    name: "AI example · After",
  }),
  documentFile("1DN2d88NmUmBel1-pfNYK20rpP6fUDZCi5MG8me-3Tfc", {
    description:
      "A two-stop guide to New York City for November 13–15, 2026, with venue times and booking links. 133 body words.",
    downloadFilename: "nyc-weekend-before.docx",
    id: "nyc-weekend-before",
    keywords:
      "unslop format before NYC New York City weekend guide comedy train show tickets venue links",
    name: "NYC weekend · Before",
  }),
  documentFile("1HTBeanpKzsNPr_TMV89bddbPvSy2x_m_8zxmTVWoeNY", {
    description:
      "A two-stop guide to New York City for November 13–15, 2026, with venue times and booking links. Wiki Unslop and Format took the draft from 133 body words to 101.",
    downloadFilename: "nyc-weekend-after.docx",
    id: "nyc-weekend",
    keywords:
      "unslop format after NYC New York City weekend guide comedy train show tickets venue links",
    name: "NYC weekend · After",
  }),
  documentFile("1P4WpzlDdXTcquocIXtjIdqsPXp0IXadecNRt4En61DM", {
    description:
      "Print approval notes with two decisions, three numbered review steps and a file-check command. 133 body words.",
    downloadFilename: "trail-map-before.docx",
    id: "proof-review-before",
    keywords:
      "unslop format before trail map proof review meeting notes printing approval numbered procedure code file",
    name: "Trail map · Before",
  }),
  documentFile("1OmNc350P7YaIcGIuW6DUtYuhpQuels2aqpnBJ_pUZgk", {
    description:
      "Print approval notes with two decisions, three numbered review steps and a file-check command. Wiki Unslop and Format took the draft from 133 body words to 118.",
    downloadFilename: "trail-map-after.docx",
    id: "proof-review",
    keywords:
      "unslop format after trail map proof review meeting notes printing approval numbered procedure code file",
    name: "Trail map · After",
  }),
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
];

const DRAFT_BASE =
  "https://docs.google.com/document/d/1ld4ojYAI6fZwS0NTqkz6DVNx12Mdypsv8kWhA6zVco8";
export const PUBLIC_DEMO_FILES: readonly DemoFile[] = [
  ...DEMO_FILES,
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
];

export function demoFileFor(id: null | string): DemoFile {
  return PUBLIC_DEMO_FILES.find((file) => file.id === id) ?? AI_BEFORE_FILE;
}

export function demoHref(file: DemoFile): `/demo?file=${string}` {
  return `/demo?file=${encodeURIComponent(file.id)}`;
}
