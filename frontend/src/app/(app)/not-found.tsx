import { WikiNotFound } from "@/components/wiki-not-found";

export default function AppNotFound() {
  return (
    <WikiNotFound
      title="Page not found"
      description="We couldn't find what you were looking for in Sarj Wiki. Pick a file from the sidebar to continue."
    />
  );
}
