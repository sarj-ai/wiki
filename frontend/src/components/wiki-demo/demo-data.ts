import { MIME_DOCX, MIME_HTML, MIME_PPTX } from "@/types/wiki";

export interface DemoFile {
  description: string;
  downloadFilename: string;
  folder: "documents" | "project";
  format: string;
  id: string;
  keywords: string;
  mimeType: string;
  name: string;
  previewFilename: string;
  viewUrl?: string;
}

const DEFAULT_DEMO_FILE: DemoFile = {
  description: "A clear launch brief with proper headings, lists, and tables.",
  downloadFilename: "northstar-launch-formatted.docx",
  folder: "documents",
  format: "Word document",
  id: "launch-brief-clean",
  keywords:
    "formatted clean polished launch brief Maya Chen Luca Reed Sam Rivera pilot 50 24 November 2026 onboarding 18400",
  mimeType: MIME_DOCX,
  name: "Launch brief · formatted.docx",
  previewFilename: "northstar-launch-formatted.html",
  viewUrl: "https://drive.google.com/file/d/1JcdRzLkmDqLwFIGTU_OHAl25xJF8KZtU/view",
};

export const DEMO_FILES: readonly DemoFile[] = [
  DEFAULT_DEMO_FILE,
  {
    description: "The same brief, with the messy formatting of an AI-pasted draft.",
    downloadFilename: "northstar-launch-original.docx",
    folder: "documents",
    format: "Word document",
    id: "launch-brief-messy",
    keywords:
      "original messy draft AI pasted before formatting launch brief Maya Chen pilot 50 24 November 2026 onboarding 18400",
    mimeType: MIME_DOCX,
    name: "Launch brief · original.docx",
    previewFilename: "northstar-launch-original.html",
    viewUrl: "https://drive.google.com/file/d/1Bwh_Vg0kt2wy6q_ZN4qYdVI_bkvERcl_/view",
  },
  {
    description: "The project kickoff, from the pilot plan to the launch checklist.",
    downloadFilename: "northstar-kickoff.pptx",
    folder: "project",
    format: "PowerPoint presentation",
    id: "kickoff",
    keywords:
      "PowerPoint presentation slides kickoff project launch plan timeline milestones pilot 50 24 November 2026",
    mimeType: MIME_PPTX,
    name: "Project kickoff.pptx",
    previewFilename: "northstar-kickoff.html",
    viewUrl: "https://drive.google.com/file/d/1CzQ6lA_qy49StmgV6oCN5ZnQgeFWOc2A/view",
  },
  {
    description: "A working launch budget, with line items, formulas, and totals.",
    downloadFilename: "northstar-budget.xlsx",
    folder: "project",
    format: "Excel spreadsheet",
    id: "budget",
    keywords: "Excel spreadsheet workbook budget costs formulas finance 18400 launch resources",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    name: "Launch budget.xlsx",
    previewFilename: "northstar-budget.html",
    viewUrl: "https://drive.google.com/file/d/17E2YpntsN4VKF7vLHG43_E9snygJk8Mc/view",
  },
  {
    description: "A standalone HTML status page, displayed right inside Wiki.",
    downloadFilename: "northstar-rollout.html",
    folder: "project",
    format: "HTML page",
    id: "status",
    keywords:
      "HTML page rollout status dashboard readiness onboarding setup completion 70 90 week one activation 42 60",
    mimeType: MIME_HTML,
    name: "Rollout status.html",
    previewFilename: "northstar-rollout.html",
    viewUrl: "https://drive.google.com/file/d/1nh6P2t8fW1tNt-mhz3UHEZVcjTlRc2Mf/view",
  },
];

export function demoFileFor(id: null | string): DemoFile {
  return DEMO_FILES.find((file) => file.id === id) ?? DEFAULT_DEMO_FILE;
}

export function demoHref(file: DemoFile): `/demo?file=${string}` {
  return `/demo?file=${encodeURIComponent(file.id)}`;
}

export function demoAssetUrl(filename: string): string {
  return `/demo/files/${encodeURIComponent(filename)}`;
}
