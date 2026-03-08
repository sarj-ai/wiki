import { FileText } from "lucide-react";

export default function HomePage() {
  return (
    <div className="flex h-full min-h-svh flex-col items-center justify-center gap-4">
      <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
        <FileText className="size-8 text-muted-foreground" />
      </div>
      <div className="text-center">
        <h2 className="text-lg font-medium text-foreground">
          Select a document
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Choose a page from the sidebar to get started
        </p>
      </div>
    </div>
  );
}
