import { cookies } from "next/headers";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/sidebar/app-sidebar";
import { ErrorBoundary } from "@/components/error-boundary";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const sidebarState = cookieStore.get("sidebar_state")?.value;
  const defaultOpen = sidebarState !== undefined ? sidebarState === "true" : true;

  return (
    <SidebarProvider defaultOpen={defaultOpen}>
      <ErrorBoundary>
        <AppSidebar />
      </ErrorBoundary>
      <main className="flex-1 overflow-auto">
        <ErrorBoundary>
          {children}
        </ErrorBoundary>
      </main>
    </SidebarProvider>
  );
}
