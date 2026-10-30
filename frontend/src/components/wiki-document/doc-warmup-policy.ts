import { allowsIframeSpeculation, type NavigatorHints } from "@/lib/speculation-policy";

export { readNavigatorHints } from "@/lib/speculation-policy";

const WARM_COUNT = 1;

export function warmCountFor(navigatorHints: NavigatorHints): number {
  return allowsIframeSpeculation(navigatorHints) ? WARM_COUNT : 0;
}
