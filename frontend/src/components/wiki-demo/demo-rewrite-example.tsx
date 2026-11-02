"use client";

import { ArrowDownRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

import type { DemoRewriteComparison } from "./demo-data";

const WORDS = new Intl.NumberFormat("en-US");

export function DemoRewriteExample({ comparison }: { comparison: DemoRewriteComparison }) {
  if (
    comparison.beforeWords <= 0 ||
    comparison.afterWords <= 0 ||
    comparison.afterWords >= comparison.beforeWords
  )
    return null;
  const reduction = Math.floor(
    (100 * (comparison.beforeWords - comparison.afterWords)) / comparison.beforeWords,
  );
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          className="h-auto max-w-full flex-wrap justify-start text-left whitespace-normal"
          size="sm"
          variant="outline"
        >
          <ArrowDownRight aria-hidden className="size-3.5" />
          <span className="font-semibold">
            {reduction === 0 ? "Less than 1% fewer words" : `${reduction}% fewer words`}
          </span>
          <span className="text-muted-foreground">See an actual edit</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="flex h-[min(85svh,48rem)] flex-col overflow-hidden sm:max-w-4xl">
        <DialogHeader className="shrink-0 text-left">
          <div className="space-y-2 pr-6">
            <DialogTitle>{comparison.scopeLabel}: before and after</DialogTitle>
            <DialogDescription>
              <span className="text-foreground block text-lg font-semibold tabular-nums">
                {WORDS.format(comparison.beforeWords)} → {WORDS.format(comparison.afterWords)} words
              </span>
              <span className="mt-1 block text-xs">
                {comparison.scopeLabel} totals. Excerpts are verbatim.
              </span>
            </DialogDescription>
          </div>
        </DialogHeader>
        <div className="grid min-h-0 flex-1 grid-rows-2 gap-3 sm:grid-cols-2 sm:grid-rows-1 sm:gap-4">
          <section
            aria-label="Original passage"
            className="focus-visible:ring-ring min-h-0 min-w-0 overflow-y-auto rounded-md border p-3 focus-visible:ring-2 focus-visible:outline-none sm:p-4"
            tabIndex={0}
          >
            <h3 className="mb-2 text-xs font-semibold">Before</h3>
            <p className="text-muted-foreground text-sm leading-relaxed wrap-anywhere whitespace-pre-wrap">
              {comparison.beforeText}
            </p>
          </section>
          <section
            aria-label="Processed passage"
            className="border-primary/20 bg-primary/5 focus-visible:ring-ring min-h-0 min-w-0 overflow-y-auto rounded-md border p-3 focus-visible:ring-2 focus-visible:outline-none sm:p-4"
            tabIndex={0}
          >
            <h3 className="text-primary mb-2 text-xs font-semibold">After</h3>
            <p className="text-sm leading-relaxed wrap-anywhere whitespace-pre-wrap">
              {comparison.afterText}
            </p>
          </section>
        </div>
        <DialogFooter className="shrink-0">
          <DialogClose asChild>
            <Button variant="outline">Close comparison</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
