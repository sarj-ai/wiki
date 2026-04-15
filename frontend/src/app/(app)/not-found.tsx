import { NavigatorNotFound } from "@/components/navigator-not-found";

/**
 * 404 boundary for routes inside the (app) layout. Rendering it from
 * this location means the parent layout — which wraps NavigatorSidebar
 * + SidebarProvider — stays mounted, so the user keeps the full
 * sidebar instead of being bounced to the default Next.js 404 chrome.
 */
export default function AppNotFound() {
  return (
    <NavigatorNotFound
      title="Page not found"
      description="We couldn't find what you were looking for in the Navigator. Pick a file from the sidebar to continue."
    />
  );
}
