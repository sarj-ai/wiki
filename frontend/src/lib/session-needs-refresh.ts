const SESSION_EXP_SKEW_MS = 30_000;
const SESSION_EXP_COOKIE = /(?:^|;\s*)session_exp=(\d+)/u;

export function sessionNeedsRefresh(): boolean {
  if (typeof document === "undefined") return false;
  const match = SESSION_EXP_COOKIE.exec(document.cookie);
  return match !== null && Date.now() > Number(match[1]) - SESSION_EXP_SKEW_MS;
}
