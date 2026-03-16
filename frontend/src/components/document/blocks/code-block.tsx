"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { codeToHtml } from "shiki";
import { Check, Copy } from "lucide-react";
import { guessLanguage } from "./utils";

export function CodeBlockComponent({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null);
  const lang = useMemo(() => guessLanguage(code), [code]);

  useEffect(() => {
    let cancelled = false;
    codeToHtml(code, {
      lang,
      theme: "github-dark-default",
    }).then((html) => {
      if (!cancelled) setHighlightedHtml(html);
    }).catch(() => {
      // Fallback to plain text if language not supported
      if (!cancelled) setHighlightedHtml(null);
    });
    return () => { cancelled = true; };
  }, [code, lang]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [code]);

  return (
    <div className="doc-code-block">
      <button
        type="button"
        onClick={handleCopy}
        className="doc-code-copy"
        aria-label="Copy code"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
      {highlightedHtml ? (
        <div
          className="doc-code-shiki"
          dangerouslySetInnerHTML={{ __html: highlightedHtml }}
        />
      ) : (
        <pre><code>{code}</code></pre>
      )}
    </div>
  );
}
