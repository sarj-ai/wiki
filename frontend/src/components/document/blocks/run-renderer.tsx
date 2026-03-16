import { Fragment } from "react";
import {
  CalendarDays,
  ExternalLink,
  Hash,
  ImageIcon,
  LayoutGrid,
  User,
} from "lucide-react";
import type { TextRun } from "@/types";
import { sanitizeUrl } from "./utils";

export function RunRenderer({ runs }: { runs: TextRun[] }) {
  return (
    <>
      {runs.map((run, i) => {
        // ── Images ──
        if (run.type === "image" && run.url) {
          const safeSrc = sanitizeUrl(run.url);
          if (!safeSrc) return <Fragment key={i} />;
          return (
            <img
              key={i}
              src={safeSrc}
              alt={run.alt || ""}
              className="my-6 max-w-full rounded-xl shadow-sm ring-1 ring-black/[0.04] dark:ring-white/[0.06] block"
              style={run.width ? { maxWidth: `${run.width}pt` } : undefined}
              width={run.width ? Math.round(run.width * 1.333) : undefined}
              height={run.height ? Math.round(run.height * 1.333) : undefined}
              loading="lazy"
              decoding="async"
            />
          );
        }

        // ── Person mention ──
        if (run.type === "person") {
          return (
            <span
              key={i}
              className="doc-chip-person"
              title={run.email}
            >
              <User className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Rich link ──
        if (run.type === "rich_link") {
          const safeHref = sanitizeUrl(run.link);
          return (
            <a
              key={i}
              href={safeHref ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="doc-chip-link"
            >
              <ExternalLink className="size-3 shrink-0" />
              <span>{run.text}</span>
            </a>
          );
        }

        // ── Footnote reference ──
        if (run.type === "footnote_ref") {
          const safeId = run.footnoteId?.replace(/[^a-zA-Z0-9_-]/g, "") ?? "";
          return (
            <sup key={i} className="doc-footnote-ref">
              <a href={`#fn-${safeId}`}>
                {run.number}
              </a>
            </sup>
          );
        }

        // ── Smart chip placeholder (fallback when HTML export can't resolve) ──
        if (run.type === "chip_placeholder") {
          return (
            <span key={i} className="doc-chip-placeholder">
              <LayoutGrid className="size-3 shrink-0" />
              <span>Smart chip</span>
            </span>
          );
        }

        // ── Date ──
        if (run.type === "date") {
          return (
            <span key={i} className="doc-chip-date">
              <CalendarDays className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Equation ──
        if (run.type === "equation") {
          return (
            <code key={i} className="doc-equation">
              {run.text}
            </code>
          );
        }

        // ── Auto text ──
        if (run.type === "auto_text") {
          return (
            <span key={i} className="text-muted-foreground text-sm italic">
              {run.text}
            </span>
          );
        }

        // ── Drawing ──
        if (run.type === "drawing") {
          return (
            <span key={i} className="doc-chip-placeholder">
              <ImageIcon className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Chart ──
        if (run.type === "chart") {
          return (
            <span key={i} className="doc-chip-placeholder">
              <Hash className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Inline code ──
        if (run.type === "code") {
          return (
            <code key={i} className="doc-inline-code">
              {run.text}
            </code>
          );
        }

        // ── Regular text run ──
        let rendered: React.ReactNode = run.text || "";

        if (run.bold) rendered = <strong>{rendered}</strong>;
        if (run.italic) rendered = <em>{rendered}</em>;
        if (run.underline) rendered = <u className="decoration-foreground/30 underline-offset-[3px]">{rendered}</u>;
        if (run.strikethrough) rendered = <s className="decoration-muted-foreground/60">{rendered}</s>;
        if (run.smallCaps)
          rendered = <span style={{ fontVariant: "small-caps" }}>{rendered}</span>;
        if (run.superscript) rendered = <sup>{rendered}</sup>;
        if (run.subscript) rendered = <sub>{rendered}</sub>;

        // Inline styles (color, background, font)
        const inlineStyle: React.CSSProperties = {};
        if (run.color) inlineStyle.color = run.color;
        if (run.backgroundColor) inlineStyle.backgroundColor = run.backgroundColor;
        if (run.fontFamily) inlineStyle.fontFamily = run.fontFamily;
        if (run.fontSize) inlineStyle.fontSize = `${run.fontSize}pt`;

        const hasInlineStyle = Object.keys(inlineStyle).length > 0;

        if (run.link) {
          const safeHref = sanitizeUrl(run.link);
          return (
            <a
              key={i}
              href={safeHref ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="doc-link"
              style={hasInlineStyle ? inlineStyle : undefined}
            >
              {rendered}
            </a>
          );
        }

        if (hasInlineStyle) {
          return (
            <span key={i} style={inlineStyle}>
              {rendered}
            </span>
          );
        }

        return <Fragment key={i}>{rendered}</Fragment>;
      })}
    </>
  );
}
