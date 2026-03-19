"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import type { DocTab } from "@/types";
import { BlockRenderer } from "./blocks";
import { TabScrollSpy, TabDivider } from "./tab-scroll-spy";

interface DocViewerProps {
  tabs: DocTab[];
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DocViewer({ tabs }: DocViewerProps) {
  const isMultiTab = tabs.length > 1;
  const [activeTab, setActiveTab] = useState(0);

  // Scroll-spy: observe which tab divider is at the top of the viewport
  useEffect(() => {
    if (!isMultiTab) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // Pick the topmost intersecting divider (smallest top value)
        let topmost: IntersectionObserverEntry | null = null;
        for (const entry of entries) {
          if (entry.isIntersecting) {
            if (!topmost || entry.boundingClientRect.top < topmost.boundingClientRect.top) {
              topmost = entry;
            }
          }
        }
        if (topmost) {
          const idx = Number(topmost.target.id.replace("doc-tab-", ""));
          if (!Number.isNaN(idx)) setActiveTab(idx);
        }
      },
      { rootMargin: "-10% 0px -80% 0px" },
    );

    // Slight delay to let DOM settle
    const timer = setTimeout(() => {
      document.querySelectorAll("[data-tab-divider]").forEach((el) => {
        observer.observe(el);
      });
    }, 100);

    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [isMultiTab, tabs]);

  const scrollToTab = useCallback((id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // Single tab — render blocks directly, no dividers
  if (!isMultiTab) {
    const blocks = tabs[0]?.blocks ?? [];
    return (
      <article className="doc-viewer">
        {blocks.map((block, i) => (
          <BlockRenderer key={i} block={block} />
        ))}
      </article>
    );
  }

  return (
    <div className="doc-tabbed-layout">
      <TabScrollSpy tabs={tabs} activeIndex={activeTab} onClickTab={scrollToTab} />
      <article className="doc-viewer">
        {tabs.map((tab, tabIdx) => (
          <Fragment key={tabIdx}>
            <TabDivider title={tab.title} id={`doc-tab-${tabIdx}`} depth={tab.depth} />
            {tab.blocks.map((block, blockIdx) => (
              <BlockRenderer key={`${tabIdx}-${blockIdx}`} block={block} />
            ))}
          </Fragment>
        ))}
      </article>
    </div>
  );
}
