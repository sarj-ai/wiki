"use client";

import { useQuery } from "@tanstack/react-query";
import { MessageSquare, Pencil } from "lucide-react";
import { useTheme } from "next-themes";
import { useMemo, useState } from "react";

import type { DocComment } from "@/types/wiki";

import { Button } from "@/components/ui/button";
import { splitDocTabs, themedSrcdoc } from "@/components/wiki-document/doc-read-html";
import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";
import { cn } from "@/lib/utils";
import { wikiApi } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";

interface DocReadViewProps {
  fileId: string;
  title: string;
  onEdit: () => void;
}

// Google has no themable editor to embed, so docs open in a read view we fully
// control: the doc is exported as self-contained HTML, split back into its
// document tabs, themed to match the app (both modes), and rendered in a
// sandboxed iframe. Comments come from the Drive API into a side panel.
// Editing swaps in the live Google editor.
export function DocReadView({ fileId, title, onEdit }: DocReadViewProps) {
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";
  const [tab, setTab] = useState(0);
  const [showComments, setShowComments] = useState(false);

  const { data } = useQuery({
    queryKey: wikiKeys.docHtml(fileId),
    queryFn: () => wikiApi.getDocHtml(fileId),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
  const comments = useQuery({
    queryKey: wikiKeys.docComments(fileId),
    queryFn: () => wikiApi.getDocComments(fileId),
    staleTime: 60 * 1000,
    enabled: showComments,
  });

  const sections = useMemo(
    () => (data === undefined ? undefined : splitDocTabs(data.html, title)),
    [data, title],
  );
  // Docs API tab tree arrives in the same depth-first order the export flattens
  // to, so levels map to sections by index; on mismatch everything renders flat.
  const levels =
    data !== undefined && sections !== undefined && data.tabs.length === sections.length
      ? data.tabs.map((t) => t.level)
      : undefined;
  const active = sections?.[tab] ?? sections?.[0];
  const srcDoc = useMemo(
    () => (active === undefined ? undefined : themedSrcdoc(active.html, dark)),
    [active, dark],
  );
  const open = comments.data?.filter((c) => !c.resolved);

  return (
    <div className="bg-background flex h-full w-full">
      {sections !== undefined && sections.length > 1 && (
        <aside className="border-border w-56 shrink-0 overflow-y-auto border-r p-2">
          <p className="text-muted-foreground px-3 py-2 text-xs font-medium">Document tabs</p>
          {sections.map((section, i) => (
            <Button
              key={section.title + String(i)}
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setTab(i);
              }}
              style={{ paddingLeft: `${String(12 + (levels?.[i] ?? 0) * 16)}px` }}
              className={cn(
                "w-full justify-start truncate font-normal",
                i === tab && "bg-accent font-medium",
              )}
            >
              {section.title}
            </Button>
          ))}
        </aside>
      )}
      <div className="relative min-w-0 flex-1">
        {srcDoc === undefined ? (
          <div className="absolute inset-0 overflow-hidden" aria-label="Loading document">
            <DocSkeleton />
          </div>
        ) : (
          // ponytail: allow-scripts runs only our injected Paged.js (opaque
          // origin, no same-origin grant — it can't reach the app). Links open
          // in a new tab via allow-popups. The export's own ACL already gated
          // this content to the signed-in user.
          <iframe
            title={active?.title ?? title}
            srcDoc={srcDoc}
            sandbox="allow-scripts allow-popups"
            className="block h-full w-full border-0"
          />
        )}
        <div className="absolute top-3 right-4 z-10 flex gap-2">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => {
              setShowComments((v) => !v);
            }}
            className="shadow"
            aria-pressed={showComments}
          >
            <MessageSquare className="size-4" />
            Comments
          </Button>
          <Button type="button" size="sm" onClick={onEdit} className="shadow">
            <Pencil className="size-4" />
            Edit
          </Button>
        </div>
      </div>
      {showComments && (
        <aside className="border-border w-80 shrink-0 overflow-y-auto border-l p-3">
          <p className="text-muted-foreground px-1 pb-2 text-xs font-medium">Comments</p>
          {comments.isPending && <p className="text-muted-foreground px-1 text-sm">Loading…</p>}
          {comments.isError && (
            <p className="text-muted-foreground px-1 text-sm">Couldn&apos;t load comments</p>
          )}
          {open?.length === 0 && (
            <p className="text-muted-foreground px-1 text-sm">No open comments</p>
          )}
          {open?.map((comment) => (
            <CommentCard key={comment.id} comment={comment} />
          ))}
        </aside>
      )}
    </div>
  );
}

function CommentCard({ comment }: { comment: DocComment }) {
  return (
    <div className="border-border mb-3 rounded-md border p-3">
      <p className="text-sm font-medium">{comment.author}</p>
      {comment.quotedText !== undefined && (
        <p className="text-muted-foreground border-border mt-1 line-clamp-2 border-l-2 pl-2 text-xs italic">
          {comment.quotedText}
        </p>
      )}
      <p className="mt-1 text-sm whitespace-pre-wrap">{comment.content}</p>
      {comment.replies.map((reply, i) => (
        <div key={i} className="border-border mt-2 border-l pl-3">
          <p className="text-xs font-medium">{reply.author}</p>
          <p className="text-sm whitespace-pre-wrap">{reply.content}</p>
        </div>
      ))}
    </div>
  );
}
