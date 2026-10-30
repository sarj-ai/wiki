import { SarjLoader } from "@/components/sarj-loader";

export function DocumentLoading({ label = "Loading document…" }: { label?: string }) {
  return <SarjLoader className="size-full px-6 py-10" label={label} />;
}
