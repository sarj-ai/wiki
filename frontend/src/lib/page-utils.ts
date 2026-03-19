import type { DocTab, Page } from "@/types";

/**
 * Check if a document's tabs contain no meaningful content.
 */
export function isDocEmpty(tabs: DocTab[] | undefined): boolean {
  if (!tabs || tabs.length === 0) return true;
  return tabs.every((tab) =>
    tab.blocks.every(
      (b) =>
        b.type === "paragraph" &&
        (!b.runs || b.runs.length === 0 || b.runs.every((r) => !r.text?.trim())),
    ),
  );
}

/**
 * Build a flat index of all pages keyed by ID from a nested page tree.
 */
export function buildPageIndex(pages: Page[]): Map<string, Page> {
  const index = new Map<string, Page>();

  function walk(pageList: Page[]) {
    for (const page of pageList) {
      index.set(page.id, page);
      if (page.children.length > 0) {
        walk(page.children);
      }
    }
  }

  walk(pages);
  return index;
}

/**
 * Find a page by ID in a nested page tree.
 * Accepts an optional pre-built index to avoid redundant traversals.
 */
export function findPageById(
  pageId: string,
  allPages: Page[],
  index?: Map<string, Page>,
): Page | undefined {
  const pageIndex = index ?? buildPageIndex(allPages);
  return pageIndex.get(pageId);
}

/**
 * Returns an array of pages from the root ancestor down to the given page,
 * forming a breadcrumb trail. Walks up the parentId chain.
 * Accepts an optional pre-built index to avoid redundant traversals.
 */
export function getPageBreadcrumbs(
  pageId: string,
  allPages: Page[],
  index?: Map<string, Page>,
): Page[] {
  const pageIndex = index ?? buildPageIndex(allPages);
  const breadcrumbs: Page[] = [];
  const visited = new Set<string>();

  let current = pageIndex.get(pageId);
  while (current) {
    if (visited.has(current.id)) break; // cycle protection
    visited.add(current.id);
    breadcrumbs.unshift(current);
    if (current.parentId) {
      current = pageIndex.get(current.parentId);
    } else {
      break;
    }
  }

  return breadcrumbs;
}
