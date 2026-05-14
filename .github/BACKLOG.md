# Backlog — Known Issues & Future Work

> Items discovered during testing. Prioritized by impact.

## UI/UX Polish

- [ ] **Hard refresh flashes light theme** — page briefly shows light mode then switches to dark. Likely a `next-themes` hydration issue where the class isn't set before first paint.
- [ ] **Emoji picker search not auto-focused** — when opening the emoji picker (change icon), the search input should be focused immediately so user can type to filter.
- [ ] **Spacing fine-tuning** — view mode spacing may need further adjustment to match Google Docs more closely. Current values are a middle ground.
- [ ] **Section expand arrow vs workspace header hierarchy** — section chevrons could be more visually distinct from workspace-level headers.
- [ ] **Long page title only visible on hover** — tooltip shows full title, but consider allowing text wrap or a wider sidebar option.

## Content Rendering

- [ ] **Smart chips in paragraphs** — chips outside tables show as "Smart chip" placeholder. Chip enrichment currently only works for table cells via HTML export. Needs full-doc HTML export matching.
- [ ] **Pageless/headless mode header image** — header images in pageless Google Docs templates don't render. Need to investigate where the Docs API stores these.
- [ ] **Checklist detection edge cases** — Google Docs uses `GLYPH_TYPE_UNSPECIFIED` with no glyph symbol for native checklists. Current detection covers this, but third-party checklist formats may differ.

## Auth & Security

- [ ] **Mobile login (cross-domain cookies)** — mobile browsers block `SameSite=none` cookies from different domains. Fix: put backend on same domain as frontend (e.g., `api-wiki.sarj.ai`) and set cookie `domain=.sarj.ai`.
- [ ] **Login page flash on expired token** — mostly fixed (silent refresh before redirect), but edge cases may remain if refresh token is also expired.

## Sync

- [ ] **Force sync slow for large trees** — scanning 1600+ folders takes minutes. Optimization: with SA as Shared Drive member, use flat `files.list` query instead of per-folder listing.
- [ ] **Incremental sync may miss new folders** — Changes API tracks file changes but may not detect new folders created by users (not the SA). Force sync is the workaround.

## Deferred from Audit

- [ ] **Prop drilling in sidebar** — `WorkspaceSection` accepts 22+ props. Consider React context to reduce drilling.
- [ ] **Synthetic keyboard event for search** — search button fakes `Cmd+K` event. Should use a shared callback/context instead.
