import { MIME_DOC, MIME_DOCX, MIME_HTML, MIME_PPTX, MIME_SHEET, MIME_SLIDE } from "@/types/wiki";

export interface DemoRewriteComparison {
  afterText: string;
  afterWords: number;
  beforeText: string;
  beforeWords: number;
  scopeLabel: string;
}

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
  processingReviewed?: boolean;
  rewriteComparison?: DemoRewriteComparison;
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
  "Recorded Unslop + Format: 5 edits. See the processing record for scope and findings.";
const SHEET_DISCLOSURE =
  "Six cells processed with Docs Unslop + Format, then copied to Sheets. Workbook formatting adjusted separately.";
const SLIDES_DISCLOSURE =
  "Recorded Unslop + Format: 10 edits. See the processing record for scope and findings.";
const ARTICLE_KEYWORDS =
  "nightingale bird Luscinia megarhynchos article song ecology habitat migration breeding conservation science sources photographs";
const SHEET_KEYWORDS =
  "nightingale evidence workbook spreadsheet data measurements song figures formulas charts photo scientific sources";
const SLIDES_KEYWORDS =
  "nightingale bird presentation slides song habitat migration conservation photographs scientific sources";
const MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const PROCESSING_RECORD = "nightingale-processing-v9.json";

const DOC_REWRITE: DemoRewriteComparison = {
  afterText:
    "BTO habitat guidance describes dense low vegetation for cover, patches of bare ground or short vegetation where birds can find invertebrates, and a thick field layer that can shelter nests. These features work together within a territory. BTO habitat guidance",
  afterWords: 377,
  beforeText:
    "There is something rather lovely, at least to my taste, about arriving at the habitat section of a nightingale article with a generous amount of room to linger. The heading feels like an invitation to a slower paragraph, a softer cadence, and a little more verbal breathing space. For me, this is a pleasantly unhurried moment in the article, a pause with a touch of literary ceremony. BTO’s habitat guidance describes dense low vegetation for cover, patches of bare ground or short vegetation where birds can find invertebrates, and a thick field layer that can shelter nests. I find myself returning to the atmosphere of that sentence, enjoying the rhythm of its phrasing and the gentle sense of a paragraph taking its time. There is a certain quiet charm in letting the words settle before proceeding, with another reflective sentence, another small flourish, and another moment of appreciation for the topic simply as a topic. These features work together within a territory. From an editorial point of view, I rather like the feeling of a rounded paragraph here, complete with a thoughtful opening, an expansive middle, and a lingering conclusion. The subject feels appealing to me, the prose feels comfortably spacious, and the closing lines offer another opportunity for a warm, leisurely farewell to this brief and rather inviting corner of the article. To my ear, the paragraph feels pleasantly ample, with a softly reflective mood and a fondness for its own cadence. BTO habitat guidance",
  beforeWords: 1335,
  scopeLabel: "Article body",
};

const SHEET_REWRITE: DemoRewriteComparison = {
  afterText:
    "These distinctions make a spreadsheet useful. Measurements retain their units, every published count retains its source, and formulas expose how a summary was obtained. A chart can then help a reader see variation without converting a sample into a universal rule. The numbers add detail to the article while leaving room for what was not measured.",
  afterWords: 484,
  beforeText:
    "This deliberately wordy AI draft offers a rather extended farewell to the passage. I enjoy the idea of a closing reflection that takes its time, lingering over the mood of the page and the sound of its own sentences. To my ear, there is a certain charm in a generous verbal flourish, with a little atmosphere and a little ceremony. My editorial enthusiasm remains firmly attached to the pleasure of an unhurried ending. I like the feeling of a paragraph settling gently into place, accompanied by a thoughtful aside and a lingering echo of its opening. The result feels leisurely and contemplative to me, an expansive final gesture in an exercise devoted to the art of taking the scenic route. These distinctions make a spreadsheet useful. Measurements retain their units, every published count retains its source, and formulas expose how a summary was obtained. A chart can then help a reader see variation without converting a sample into a universal rule. The numbers add detail to the article while leaving room for what was not measured.\n",
  beforeWords: 1032,
  scopeLabel: "Six Field notes prose cells",
};

const SLIDES_REWRITE: DemoRewriteComparison = {
  afterText:
    "The common nightingale's warm brown back, paler underside and broad tail can disappear against the stems and shadows of a thicket.",
  afterWords: 330,
  beforeText:
    "Deliberately wordy AI-draft input: In the spirit of a leisurely introduction, the common nightingale feels like a pleasing subject for a broad, expansive opening, with room for a graceful preamble and then a further preamble to the preamble. There is, to my taste, something inviting about taking the scenic route through an introduction, lingering over the prospect of description, and giving the thought of identification a suitably generous welcome. Its warm brown back, paler underside and broad tail can disappear against the stems and shadows of a thicket.",
  beforeWords: 1287,
  scopeLabel: "Visible slide text",
};

const DEFAULT_DEMO_FILE: DemoFile = {
  comparison: DOC_COMPARISON,
  description: "Article body: 1,335 → 377 words after Unslop + Format.",
  downloadFilename: "nightingale-edited-article.docx",
  downloadUrl:
    "https://docs.google.com/document/d/1wroTuWbI9mVByQvZhZjM2i2FtZx-IzxYxfcRIG547XE/export?format=docx",
  folder: "documents",
  format: "Google Docs",
  id: "nightingale-doc-unslop",
  keywords: ARTICLE_KEYWORDS,
  mimeType: MIME_DOC,
  name: "Nightingale article · edited",
  rewriteComparison: DOC_REWRITE,
  processingNeedsReview: true,
  processingReviewed: true,
  previewUrl:
    "https://docs.google.com/document/d/1wroTuWbI9mVByQvZhZjM2i2FtZx-IzxYxfcRIG547XE/preview",
  processingDisclosure: DOC_DISCLOSURE,
  processingRecordFilename: PROCESSING_RECORD,
  viewUrl:
    "https://docs.google.com/document/d/1wroTuWbI9mVByQvZhZjM2i2FtZx-IzxYxfcRIG547XE/preview",
};

export const DEMO_FILES: readonly DemoFile[] = [
  DEFAULT_DEMO_FILE,
  {
    comparison: DOC_COMPARISON,
    description: "Deliberately wordy five-author AI draft, with real photos and sources.",
    downloadFilename: "nightingale-article-original.docx",
    downloadUrl:
      "https://docs.google.com/document/d/1cnuCOd5dqD_Fka8I967ZFKUCCCLGMn77h4VEwFRMIX8/export?format=docx",
    folder: "documents",
    format: "Google Docs",
    id: "nightingale-doc-source",
    keywords: ARTICLE_KEYWORDS,
    mimeType: MIME_DOC,
    name: "Nightingale article · wordy draft",
    rewriteComparison: DOC_REWRITE,
    previewUrl:
      "https://docs.google.com/document/d/1cnuCOd5dqD_Fka8I967ZFKUCCCLGMn77h4VEwFRMIX8/preview",
    processingDisclosure: "Recorded input, before Unslop + Format.",
    viewUrl:
      "https://docs.google.com/document/d/1cnuCOd5dqD_Fka8I967ZFKUCCCLGMn77h4VEwFRMIX8/preview",
  },
  {
    description: "Word export of the edited article.",
    downloadFilename: "nightingale-edited-article.docx",
    downloadUrl: "https://drive.google.com/uc?export=download&id=1tjV9yCX4wJA1j1m6Rn-CDIBJzwZFYIYH",
    folder: "documents",
    format: "Word document",
    id: "nightingale-docx",
    keywords: `${ARTICLE_KEYWORDS} Word DOCX export`,
    mimeType: MIME_DOCX,
    name: "Nightingale article.docx",
    rewriteComparison: DOC_REWRITE,
    processingNeedsReview: true,
    processingReviewed: true,
    previewUrl: "https://drive.google.com/file/d/1tjV9yCX4wJA1j1m6Rn-CDIBJzwZFYIYH/preview",
    processingDisclosure: `Word export. ${DOC_DISCLOSURE}`,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/1tjV9yCX4wJA1j1m6Rn-CDIBJzwZFYIYH/preview",
  },
  {
    comparison: SHEET_COMPARISON,
    description: "Original workbook: source links, formulas, charts and a photo.",
    downloadFilename: "nightingale-evidence-original.xlsx",
    downloadUrl:
      "https://docs.google.com/spreadsheets/d/1H1LExrmtelvvC8IXPrBBZ3jD9TxgLe1P5t_Cqvq2NuE/export?format=xlsx",
    folder: "project",
    format: "Google Sheets",
    id: "nightingale-sheet-source",
    keywords: SHEET_KEYWORDS,
    mimeType: MIME_SHEET,
    name: "Nightingale evidence · original",
    rewriteComparison: SHEET_REWRITE,
    previewUrl:
      "https://docs.google.com/spreadsheets/d/1H1LExrmtelvvC8IXPrBBZ3jD9TxgLe1P5t_Cqvq2NuE/preview#gid=43542055",
    processingDisclosure:
      "Deliberately wordy notes. Data, formulas, sources and photo match the edited workbook.",
    viewUrl:
      "https://docs.google.com/spreadsheets/d/1H1LExrmtelvvC8IXPrBBZ3jD9TxgLe1P5t_Cqvq2NuE/preview#gid=43542055",
  },
  {
    comparison: SHEET_COMPARISON,
    description: "Six prose cells: 1,032 → 484 words. Data and formulas preserved.",
    downloadFilename: "nightingale-evidence.xlsx",
    downloadUrl:
      "https://docs.google.com/spreadsheets/d/1sqewxfMNnKOUkAESJZliuZPz9sYXJzY0HSXTAcq2CnM/export?format=xlsx",
    folder: "project",
    format: "Google Sheets",
    id: "nightingale-sheet-unslop",
    keywords: SHEET_KEYWORDS,
    mimeType: MIME_SHEET,
    name: "Nightingale evidence · edited notes",
    rewriteComparison: SHEET_REWRITE,
    processingNeedsReview: true,
    processingReviewed: true,
    previewUrl:
      "https://docs.google.com/spreadsheets/d/1sqewxfMNnKOUkAESJZliuZPz9sYXJzY0HSXTAcq2CnM/preview#gid=1278732035",
    processingDisclosure: SHEET_DISCLOSURE,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl:
      "https://docs.google.com/spreadsheets/d/1sqewxfMNnKOUkAESJZliuZPz9sYXJzY0HSXTAcq2CnM/preview#gid=1278732035",
  },
  {
    description: "Excel export of the edited workbook.",
    downloadFilename: "nightingale-evidence.xlsx",
    downloadUrl: "https://drive.google.com/uc?export=download&id=1HYw0uWHr9zP6KE_Cwt8a-4lYuLKXgZtv",
    folder: "project",
    format: "Excel spreadsheet",
    id: "nightingale-xlsx",
    keywords: `${SHEET_KEYWORDS} Excel XLSX export`,
    mimeType: MIME_XLSX,
    name: "Nightingale evidence.xlsx",
    rewriteComparison: SHEET_REWRITE,
    processingNeedsReview: true,
    processingReviewed: true,
    previewUrl: "https://drive.google.com/file/d/1HYw0uWHr9zP6KE_Cwt8a-4lYuLKXgZtv/preview",
    processingDisclosure: `Excel export. ${SHEET_DISCLOSURE}`,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/1HYw0uWHr9zP6KE_Cwt8a-4lYuLKXgZtv/preview",
  },
  {
    comparison: SLIDES_COMPARISON,
    description: "Deliberately wordy five-author AI draft, with photos and sources.",
    downloadFilename: "nightingale-original.pptx",
    downloadUrl:
      "https://docs.google.com/presentation/d/15f3WRbuw4-lyHLk89uGV_d2B7UrHL1OUSvBX2TJb0aY/export/pptx",
    folder: "project",
    format: "Google Slides",
    id: "nightingale-slides-source",
    keywords: SLIDES_KEYWORDS,
    mimeType: MIME_SLIDE,
    name: "Nightingale presentation · wordy draft",
    rewriteComparison: SLIDES_REWRITE,
    previewUrl:
      "https://docs.google.com/presentation/d/15f3WRbuw4-lyHLk89uGV_d2B7UrHL1OUSvBX2TJb0aY/preview",
    processingDisclosure: "Recorded input, before Unslop + Format.",
    viewUrl:
      "https://docs.google.com/presentation/d/15f3WRbuw4-lyHLk89uGV_d2B7UrHL1OUSvBX2TJb0aY/preview",
  },
  {
    comparison: SLIDES_COMPARISON,
    description: "Ten slides: 1,287 → 330 visible words after Unslop + Format.",
    downloadFilename: "nightingale-concise-presentation.pptx",
    downloadUrl:
      "https://docs.google.com/presentation/d/1DwW6SHaEjP-pHZ2AOczTPIm1KFZ5gRRr8TiKozL65mU/export/pptx",
    folder: "project",
    format: "Google Slides",
    id: "nightingale-slides-unslop",
    keywords: SLIDES_KEYWORDS,
    mimeType: MIME_SLIDE,
    name: "Nightingale presentation · edited",
    rewriteComparison: SLIDES_REWRITE,
    previewUrl:
      "https://docs.google.com/presentation/d/1DwW6SHaEjP-pHZ2AOczTPIm1KFZ5gRRr8TiKozL65mU/preview",
    processingDisclosure: SLIDES_DISCLOSURE,
    processingNeedsReview: true,
    processingReviewed: true,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl:
      "https://docs.google.com/presentation/d/1DwW6SHaEjP-pHZ2AOczTPIm1KFZ5gRRr8TiKozL65mU/preview",
  },
  {
    description: "PowerPoint export of the edited slides.",
    downloadFilename: "nightingale-concise-presentation.pptx",
    downloadUrl: "https://drive.google.com/uc?export=download&id=1ZNI-yN_qU4p8XBvK4JljH_BJ8_aRNAsE",
    folder: "project",
    format: "PowerPoint presentation",
    id: "nightingale-pptx",
    keywords: `${SLIDES_KEYWORDS} PowerPoint PPTX export`,
    mimeType: MIME_PPTX,
    name: "Nightingale presentation.pptx",
    rewriteComparison: SLIDES_REWRITE,
    previewUrl: "https://drive.google.com/file/d/1ZNI-yN_qU4p8XBvK4JljH_BJ8_aRNAsE/preview",
    processingDisclosure: `PowerPoint export. ${SLIDES_DISCLOSURE}`,
    processingNeedsReview: true,
    processingReviewed: true,
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/1ZNI-yN_qU4p8XBvK4JljH_BJ8_aRNAsE/preview",
  },
  {
    description: "HTML companion from the edited article, with one photo.",
    downloadFilename: "nightingale-field-guide.html",
    downloadUrl: "https://drive.google.com/uc?export=download&id=161nwyqh_5h2-F5s5lsj0nn8HfeNcSHnV",
    folder: "project",
    format: "HTML page",
    id: "nightingale-html",
    keywords: `${ARTICLE_KEYWORDS} HTML field guide companion`,
    mimeType: MIME_HTML,
    name: "Nightingale field guide.html",
    previewFilename: "nightingale-field-guide-v9-preview.json",
    processingDisclosure: "From the recorded Docs result; no extra processing run.",
    processingRecordFilename: PROCESSING_RECORD,
    viewUrl: "https://drive.google.com/file/d/161nwyqh_5h2-F5s5lsj0nn8HfeNcSHnV/view",
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
