import { MIME_DOCX, MIME_PPTX } from "@/types/wiki";

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
  "Try “overview”, “stats”, or “walkthrough”.";

const MIME_XLSX =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const OVERVIEW_FILE: DemoFile = {
  description: "Why Wiki brings a Notion-like interface to Google Workspace.",
  downloadFilename: "wiki-overview.docx",
  downloadUrl:
    "https://drive.google.com/uc?export=download&id=1Np8L4CDH1_jeWUE6iaNSRwseZGzVclma",
  format: "Word document",
  id: "wiki-overview",
  keywords:
    "Notion Google Workspace Drive documents knowledge overview why Wiki exists",
  mimeType: MIME_DOCX,
  name: "Overview",
  previewUrl:
    "https://drive.google.com/file/d/1Np8L4CDH1_jeWUE6iaNSRwseZGzVclma/preview",
  viewUrl:
    "https://drive.google.com/file/d/1Np8L4CDH1_jeWUE6iaNSRwseZGzVclma/view",
};

export const DEMO_FILES: readonly DemoFile[] = [
  OVERVIEW_FILE,
  {
    description: "Fictional example statistics with formulas and a chart.",
    downloadFilename: "wiki-statistics.xlsx",
    downloadUrl:
      "https://drive.google.com/uc?export=download&id=1t0p3gzYTggh93PXA-SQijc9RxwndQ4aW",
    format: "Excel spreadsheet",
    id: "wiki-statistics",
    keywords:
      "statistics stats spreadsheet fictional sample data formulas chart",
    mimeType: MIME_XLSX,
    name: "Wiki stats",
    previewUrl:
      "https://drive.google.com/file/d/1t0p3gzYTggh93PXA-SQijc9RxwndQ4aW/preview",
    viewUrl:
      "https://drive.google.com/file/d/1t0p3gzYTggh93PXA-SQijc9RxwndQ4aW/view",
  },
  {
    description:
      "A short walkthrough of connecting Drive, browsing files and searching.",
    downloadFilename: "how-wiki-works.pptx",
    downloadUrl:
      "https://drive.google.com/uc?export=download&id=1sCDzYyVNwP5WpZNi14pJpUVxcLsa18h_",
    format: "PowerPoint presentation",
    id: "wiki-walkthrough",
    keywords:
      "how Wiki works walkthrough presentation slides connect Drive browse search",
    mimeType: MIME_PPTX,
    name: "How Wiki works",
    previewUrl:
      "https://drive.google.com/file/d/1sCDzYyVNwP5WpZNi14pJpUVxcLsa18h_/preview",
    viewUrl:
      "https://drive.google.com/file/d/1sCDzYyVNwP5WpZNi14pJpUVxcLsa18h_/view",
  },
];

export function demoFileFor(id: null | string): DemoFile {
  return DEMO_FILES.find((file) => file.id === id) ?? OVERVIEW_FILE;
}

export function demoHref(file: DemoFile): `/demo?file=${string}` {
  return `/demo?file=${encodeURIComponent(file.id)}`;
}
