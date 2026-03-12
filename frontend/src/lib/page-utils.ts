import type { Page } from "@/types";

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

  let current = pageIndex.get(pageId);
  while (current) {
    breadcrumbs.unshift(current);
    if (current.parentId) {
      current = pageIndex.get(current.parentId);
    } else {
      break;
    }
  }

  return breadcrumbs;
}
