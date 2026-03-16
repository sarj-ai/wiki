import { sanitizeUrl } from "./utils";

export function ImageBlock({
  url,
  alt,
  width,
}: {
  url: string;
  alt?: string;
  width?: number;
}) {
  const safeSrc = sanitizeUrl(url);
  if (!safeSrc) return null;

  return (
    <figure className="my-8">
      <img
        src={safeSrc}
        alt={alt || ""}
        className="max-w-full rounded-xl shadow-sm ring-1 ring-black/[0.04] dark:ring-white/[0.06]"
        style={width ? { maxWidth: `${width}pt` } : undefined}
        width={width ? Math.round(width * 1.333) : undefined}
        loading="lazy"
        decoding="async"
      />
    </figure>
  );
}
