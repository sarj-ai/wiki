import { WikiNotFound } from "@/components/wiki-not-found";

export default function RootNotFound() {
  return (
    <main className="bg-background text-foreground h-svh">
      <WikiNotFound
        description="That URL doesn’t match anything in Sarj Wiki. Head back to the home page and pick a file from the sidebar."
        title="Page not found"
      />
    </main>
  );
}
