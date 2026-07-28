import { WikiNotFound } from "@/components/wiki-not-found";

export default function AppNotFound() {
  return (
    <WikiNotFound
      description="We couldn't find what you were looking for in Sarj Wiki. Pick a file from the sidebar to continue."
      title="Page not found"
    />
  );
}
