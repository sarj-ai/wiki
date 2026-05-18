import { FileText } from "lucide-react";

export default function HomePage() {
  return (
    <div className="flex h-full min-h-svh flex-col items-center justify-center gap-4">
      <div className="bg-muted flex size-16 items-center justify-center rounded-2xl">
        <FileText className="text-muted-foreground size-8" />
      </div>
      <div className="text-center">
        <h2 className="text-foreground text-lg font-medium">Select a document</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          Choose a page from the sidebar to get started
        </p>
      </div>
    </div>
  );
}
