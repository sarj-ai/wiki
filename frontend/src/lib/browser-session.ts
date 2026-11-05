let currentSession = new AbortController();
let currentOwner: null | object = null;

export function captureBrowserSession(): AbortSignal {
  return currentSession.signal;
}

export function claimBrowserSession(owner: object): void {
  currentOwner = owner;
}

export function retireBrowserSession(owner: object): void {
  if (currentOwner !== owner) return;
  currentOwner = null;
  const retired = currentSession;
  currentSession = new AbortController();
  retired.abort(new DOMException("The browser session was retired", "AbortError"));
}
