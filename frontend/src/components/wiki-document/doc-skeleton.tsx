import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const DOC_SKELETON_LINES = [
  { id: "l1", w: "w-full" },
  { id: "l2", w: "w-11/12" },
  { id: "l3", w: "w-10/12" },
  { id: "l4", w: "w-full" },
  { id: "l5", w: "w-9/12" },
  { id: "l6", w: "w-full" },
  { id: "l7", w: "w-11/12" },
  { id: "l8", w: "w-8/12" },
  { id: "l9", w: "w-full" },
  { id: "l10", w: "w-10/12" },
];

export function DocSkeleton({
  animated = true,
  status,
}: {
  animated?: boolean;
  status?: React.ReactNode;
}) {
  return (
    <div className="mx-auto flex size-full max-w-3xl flex-col gap-4 px-6 py-10">
      {status}
      <div aria-hidden="true" className="flex flex-col gap-4">
        <Skeleton className={cn("h-8 w-2/3", !animated && "animate-none")} />
        <Skeleton className={cn("h-4 w-1/3", !animated && "animate-none")} />
        <div className="mt-4 flex flex-col gap-3">
          {DOC_SKELETON_LINES.map((line) => (
            <Skeleton className={cn("h-4", line.w, !animated && "animate-none")} key={line.id} />
          ))}
        </div>
      </div>
    </div>
  );
}
