export const SHORT_LINK_COPY_FEEDBACK_MS = 2000;
export const SHORT_LINK_DISPLAY_PREFIX = "wiki.sarj.ai/go/";

export function documentShortLink(origin: string, alias: string): string {
  return `${origin}/go/${alias}`;
}
