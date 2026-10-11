let currentSession = new AbortController();
let currentOwner: null | object = null;

// Capture before asynchronous work, including work started by child effects
// before the provider's own effect claims this session.
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
