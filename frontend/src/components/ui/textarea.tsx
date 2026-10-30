import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const textareaVariants = cva(
  "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 min-h-24 w-full resize-y rounded-md border bg-transparent px-3 py-2 text-base shadow-xs outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
  {
    defaultVariants: {
      variant: "default",
    },
    variants: {
      variant: {
        code: "font-mono leading-relaxed md:text-xs",
        default: "md:text-sm",
      },
    },
  },
);

function Textarea({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<"textarea"> & VariantProps<typeof textareaVariants>) {
  return (
    <textarea
      className={cn(textareaVariants({ className, variant }))}
      data-slot="textarea"
      {...props}
    />
  );
}

export { Textarea };
