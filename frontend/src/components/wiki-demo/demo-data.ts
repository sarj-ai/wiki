import { MIME_DOC, MIME_DOCX, MIME_HTML, MIME_PPTX, MIME_SHEET, MIME_SLIDE } from "@/types/wiki";

export interface DemoFile {
  comparison?: { afterId: string; beforeId: string };
  description: string;
  downloadFilename: string;
  downloadUrl?: string;
  folder: "documents" | "project";
  format: string;
  id: string;
  keywords: string;
  mimeType: string;
  name: string;
  previewFilename?: string;
  previewUrl?: string;
  processingDisclosure?: string;
  processingNeedsReview?: boolean;
  processingRecordFilename?: string;
  viewUrl?: string;
}

export const DEMO_WORKSPACE_NAME = "Nightingale";
export const DEMO_SAMPLE_DESCRIPTION = "Public sample files";
export const DEMO_SEARCH_SUGGESTION = "Try “song”, “migration”, or “habitat”.";
export const DEMO_FOLDERS = [
  { id: "documents", label: "Article" },
  { id: "project", label: "Sheets, slides & files" },
] as const;

const DOC_COMPARISON = {
  afterId: "nightingale-doc-unslop",
  beforeId: "nightingale-doc-source",
} as const;
const SHEET_COMPARISON = {
  afterId: "nightingale-sheet-unslop",
  beforeId: "nightingale-sheet-source",
} as const;
const SLIDES_COMPARISON = {
  afterId: "nightingale-slides-unslop",
  beforeId: "nightingale-slides-source",
} as const;
const DOC_DISCLOSURE =
  "Actual Wiki Unslop run: 0 text edits. 33 rich-text blocks were preserved without text cleanup.";
const SHEET_DISCLOSURE =
  "Wiki’s shared Unslop engine reviewed 6 prose cells with 0 edits. Spreadsheet Unslop is not available in the Wiki app.";
const SLIDES_DISCLOSURE =
  "Actual Wiki Unslop run: 0 text edits. 15 rich-text targets could not be processed.";
const ARTICLE_KEYWORDS =
  "nightingale bird Luscinia megarhynchos article song ecology habitat migration breeding conservation science sources photographs";
const SHEET_KEYWORDS =
  "nightingale evidence workbook spreadsheet data measurements song figures formulas charts photo scientific sources";
const SLIDES_KEYWORDS =
  "nightingale bird presentation slides song habitat migration conservation photographs scientific sources";
const MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const PROCESSING_RECORD = "nightingale-processing.json";

const DEFAULT_DEMO_FILE: DemoFile = {
  comparison: DOC_COMPARISON,
  description: "The five-agent article after its recorded Wiki Unslop run.",
  downloadFilename: "nightingale-article-actual-Unslop-export.docx",
  downloadUrl:
    "https://docs.google.com/document/d/11ZKT_SKqzN17KzjRQ_iyoqgM6HjYEdFvokNOQFpExlk/export?format=docx",
  folder: "documents",
  format: "Google Docs",
  id: "nightingale-doc-unslop",
  keywords: ARTICLE_KEYWORDS,
  mimeType: MIME_DOC,
  name: "Nightingale article · Unslop result",
  previewUrl:
    "https://docs.google.com/document/d/11ZKT_SKqzN17KzjRQ_iyoqgM6HjYEdFvokNOQFpExlk/preview",
  processingDisclosure: DOC_DISCLOSURE,
  processingRecordFilename: PROCESSING_RECORD,
  viewUrl:
    "https://docs.google.com/document/d/11ZKT_SKqzN17KzjRQ_iyoqgM6HjYEdFvokNOQFpExlk/preview",
};

export const DEMO_FILES: readonly DemoFile[] = [
  DEFAULT_DEMO_FILE,
  {
    comparison: DOC_COMPARISON,
    description: "The original article, with photographs and linked scientific sources.",
    downloadFilename: "nightingale-article-original.docx",
    downloadUrl:
      "https://docs.google.com/document/d/1xZIf87OCVJz85nFBn9J5CwAEZJhWeta7WPBVvTbcamo/export?format=docx",
    folder: "documents",
    format: "Google Docs",
    id: "nightingale-doc-source",
    keywords: ARTICLE_KEYWORDS,
    mimeType: MIME_DOC,
    name: "Nightingale article · original",
    previewUrl:
      "https://docs.google.com/document/d/1xZIf87OCVJz85nFBn9J5CwAEZJhWeta7WPBVvTbcamo/preview",
    processingDisclosure: "Original five-agent article, before the Wiki Unslop run.",
    viewUrl:
      "https://docs.google.com/document/d/1xZIf87OCVJz85nFBn9J5CwAEZJhWeta7WPBVvTbcamo/preview",
  },
  {
    description: "The actual Word export of the native Google Docs result.",
    downloadFilename: "nightingale-article-actual-Unslop-export.docx",
    downloadUrl: "https://drive.google.com/uc?export=download&id=1mj1NLODVwEKUQ-3WeQlN2GnLABIXssdW",
    folder: "documents",
    format: "Word document",
    id: "nightingale-docx",
    keywords: `${ARTICLE_KEYWORDS} Word DOCX export`,
    mimeType: MIME_DOCX,
    name: "Nightingale article.docx",
    previewUrl: "https://drive.google.com/file/d/1mj1NLODVwEKUQ-3WeQlN2GnLABIXssdW/preview",
    processingDisclosure: `Word export of the same result. ${DOC_DISCLOSURE}`,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/1mj1NLODVwEKUQ-3WeQlN2GnLABIXssdW/preview",
  },
  {
    comparison: SHEET_COMPARISON,
    description:
      "The original evidence workbook, with source links, formulas, charts, and a photo.",
    downloadFilename: "nightingale-evidence-original.xlsx",
    downloadUrl:
      "https://docs.google.com/spreadsheets/d/1ALMVW5gpAJxeQqJJbbW92odtuS-kLtupD3eMXLO-U38/export?format=xlsx",
    folder: "project",
    format: "Google Sheets",
    id: "nightingale-sheet-source",
    keywords: SHEET_KEYWORDS,
    mimeType: MIME_SHEET,
    name: "Nightingale evidence · original",
    previewUrl:
      "https://docs.google.com/spreadsheets/d/1ALMVW5gpAJxeQqJJbbW92odtuS-kLtupD3eMXLO-U38/preview",
    processingDisclosure:
      "Source workbook before the shared Unslop engine reviewed its prose cells.",
    viewUrl:
      "https://docs.google.com/spreadsheets/d/1ALMVW5gpAJxeQqJJbbW92odtuS-kLtupD3eMXLO-U38/preview",
  },
  {
    comparison: SHEET_COMPARISON,
    description:
      "The recorded prose review result, with the workbook’s formulas and data preserved.",
    downloadFilename: "nightingale-evidence-actual-Unslop-export.xlsx",
    downloadUrl:
      "https://docs.google.com/spreadsheets/d/11ICUt02qmX-j0Qja2RY_ctzvN2NdIkY_M2uIIVBWNxg/export?format=xlsx",
    folder: "project",
    format: "Google Sheets",
    id: "nightingale-sheet-unslop",
    keywords: SHEET_KEYWORDS,
    mimeType: MIME_SHEET,
    name: "Nightingale evidence · text review result",
    previewUrl:
      "https://docs.google.com/spreadsheets/d/11ICUt02qmX-j0Qja2RY_ctzvN2NdIkY_M2uIIVBWNxg/preview",
    processingDisclosure: SHEET_DISCLOSURE,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl:
      "https://docs.google.com/spreadsheets/d/11ICUt02qmX-j0Qja2RY_ctzvN2NdIkY_M2uIIVBWNxg/preview",
  },
  {
    description: "The actual Excel export of the native workbook after its prose review.",
    downloadFilename: "nightingale-evidence-actual-Unslop-export.xlsx",
    downloadUrl: "https://drive.google.com/uc?export=download&id=12qga5PeE1Fi0_ClsPuIF_6LBBFRFkk-E",
    folder: "project",
    format: "Excel spreadsheet",
    id: "nightingale-xlsx",
    keywords: `${SHEET_KEYWORDS} Excel XLSX export`,
    mimeType: MIME_XLSX,
    name: "Nightingale evidence.xlsx",
    previewUrl: "https://drive.google.com/file/d/12qga5PeE1Fi0_ClsPuIF_6LBBFRFkk-E/preview",
    processingDisclosure: `Excel export of the same result. ${SHEET_DISCLOSURE}`,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/12qga5PeE1Fi0_ClsPuIF_6LBBFRFkk-E/preview",
  },
  {
    comparison: SLIDES_COMPARISON,
    description: "The original illustrated presentation about the common nightingale.",
    downloadFilename: "nightingale-original.pptx",
    downloadUrl:
      "https://docs.google.com/presentation/d/10TEg5hEKaWjeJl8SZ-sFLXGTm68680pld9pj1_JfLGo/export/pptx",
    folder: "project",
    format: "Google Slides",
    id: "nightingale-slides-source",
    keywords: SLIDES_KEYWORDS,
    mimeType: MIME_SLIDE,
    name: "Nightingale presentation · original",
    previewUrl:
      "https://docs.google.com/presentation/d/10TEg5hEKaWjeJl8SZ-sFLXGTm68680pld9pj1_JfLGo/preview",
    processingDisclosure: "Source presentation before the Wiki Unslop run.",
    viewUrl:
      "https://docs.google.com/presentation/d/10TEg5hEKaWjeJl8SZ-sFLXGTm68680pld9pj1_JfLGo/preview",
  },
  {
    comparison: SLIDES_COMPARISON,
    description:
      "The actual Wiki Unslop result, with a text-coverage limitation that needs review.",
    downloadFilename: "nightingale-presentation-actual-Unslop-export.pptx",
    downloadUrl:
      "https://docs.google.com/presentation/d/1x6GBd68-hHWiv9VNVM5wGlBJ3fYZbqcLp9TOb1_ETro/export/pptx",
    folder: "project",
    format: "Google Slides",
    id: "nightingale-slides-unslop",
    keywords: SLIDES_KEYWORDS,
    mimeType: MIME_SLIDE,
    name: "Nightingale presentation · Unslop result",
    previewUrl:
      "https://docs.google.com/presentation/d/1x6GBd68-hHWiv9VNVM5wGlBJ3fYZbqcLp9TOb1_ETro/preview",
    processingDisclosure: SLIDES_DISCLOSURE,
    processingNeedsReview: true,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl:
      "https://docs.google.com/presentation/d/1x6GBd68-hHWiv9VNVM5wGlBJ3fYZbqcLp9TOb1_ETro/preview",
  },
  {
    description: "The actual PowerPoint export of the native Google Slides result.",
    downloadFilename: "nightingale-presentation-actual-Unslop-export.pptx",
    downloadUrl: "https://drive.google.com/uc?export=download&id=1eBtUOTy41H41P_ydabiy_Vqnli8daHuv",
    folder: "project",
    format: "PowerPoint presentation",
    id: "nightingale-pptx",
    keywords: `${SLIDES_KEYWORDS} PowerPoint PPTX export`,
    mimeType: MIME_PPTX,
    name: "Nightingale presentation.pptx",
    previewUrl: "https://drive.google.com/file/d/1eBtUOTy41H41P_ydabiy_Vqnli8daHuv/preview",
    processingDisclosure: `PowerPoint export of the same result. ${SLIDES_DISCLOSURE}`,
    processingNeedsReview: true,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/1eBtUOTy41H41P_ydabiy_Vqnli8daHuv/preview",
  },
  {
    description: "An HTML companion made from the actual Docs result and one of its photographs.",
    downloadFilename: "nightingale-field-guide.html",
    downloadUrl: "https://drive.google.com/uc?export=download&id=1wcoQfJEf9J_cfJnfa5YE9zeuAbjtd7-o",
    folder: "project",
    format: "HTML page",
    id: "nightingale-html",
    keywords: `${ARTICLE_KEYWORDS} HTML field guide companion`,
    mimeType: MIME_HTML,
    name: "Nightingale field guide.html",
    previewFilename: "nightingale-field-guide-preview.json",
    processingDisclosure: "Companion view of the actual Docs result. No separate Unslop run.",
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/1wcoQfJEf9J_cfJnfa5YE9zeuAbjtd7-o/view",
  },
];

export function demoFileFor(id: null | string): DemoFile {
  return DEMO_FILES.find((file) => file.id === id) ?? DEFAULT_DEMO_FILE;
}

export function demoHref(file: DemoFile): `/demo?file=${string}` {
  return `/demo?file=${encodeURIComponent(file.id)}`;
}

export function demoComparisonFor(
  file: DemoFile,
): readonly { file: DemoFile; label: "After" | "Before" }[] {
  const comparison = file.comparison;
  if (!comparison) return [];
  const before = DEMO_FILES.find((candidate) => candidate.id === comparison.beforeId);
  const after = DEMO_FILES.find((candidate) => candidate.id === comparison.afterId);
  if (!before || !after || before.id === after.id) return [];
  return [
    { file: before, label: "Before" },
    { file: after, label: "After" },
  ];
}

export function demoAssetUrl(filename: string): string {
  return `/demo/files/${encodeURIComponent(filename)}`;
}
