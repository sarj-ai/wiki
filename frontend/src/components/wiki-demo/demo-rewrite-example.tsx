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
          className="mt-2 h-auto max-w-full flex-wrap justify-start text-left whitespace-normal"
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
      <DialogContent className="flex max-h-[85svh] flex-col overflow-hidden sm:max-w-4xl">
        <DialogHeader className="shrink-0">
          <DialogTitle>{comparison.scopeLabel}: before and after</DialogTitle>
          <DialogDescription>
            {WORDS.format(comparison.beforeWords)} → {WORDS.format(comparison.afterWords)} words
            overall. This passage comes from the actual files.
          </DialogDescription>
        </DialogHeader>
        <div className="grid min-h-0 gap-4 overflow-y-auto sm:grid-cols-2">
          <section aria-label="Original passage" className="min-w-0 rounded-md border p-4">
            <h3 className="mb-2 text-xs font-semibold">Before</h3>
            <p className="text-muted-foreground text-sm leading-relaxed wrap-anywhere whitespace-pre-wrap">
              {comparison.beforeText}
            </p>
          </section>
          <section
            aria-label="Processed passage"
            className="border-primary/20 bg-primary/5 min-w-0 rounded-md border p-4"
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
