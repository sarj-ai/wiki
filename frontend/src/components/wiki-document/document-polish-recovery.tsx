"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { DOCUMENT_TRANSFORM_API } from "@/lib/document-transform-api";
import type { TransformRecovery } from "@/lib/document-transform-recovery";
import type { DocumentTransformSource } from "@/lib/document-transform-types";
import {
  makeMarkdownRecovery,
  type MarkdownRecoveryBundle,
  markdownRecoveryStorageKey,
  saveMarkdownRecovery,
  validateMarkdownRecovery,
} from "@/lib/markdown-transform-recovery";

interface Props {
  onRestore: (
    operation: TransformRecovery,
    source: MarkdownRecoveryBundle["source"] | null,
  ) => void;
  operation: TransformRecovery;
  owner: string;
  source: DocumentTransformSource | null;
}

type RecoveryAction =
  | "check"
  | "export"
  | "import"
  | "legacy"
  | "reload"
  | "restore";

type RecoveryState =
  | { kind: "idle" }
  | { kind: "running"; action: RecoveryAction; controller: AbortController }
  | { kind: "notice"; message: string }
  | { kind: "unavailable" };

export function DocumentPolishRecovery(props: Props) {
  return (
    <RecoveryForOperation
      key={JSON.stringify([props.owner, props.operation.key])}
      {...props}
    />
  );
}

function RecoveryForOperation({ owner, operation, source, onRestore }: Props) {
  const [transfer, setTransfer] = useState("");
  const [state, setState] = useState<RecoveryState>({ kind: "idle" });
  const phase = useRef<RecoveryState>(state);
  const busy = state.kind === "running";
  const message = recoveryMessage(state);
  const publish = (next: RecoveryState) => {
    phase.current = next;
    setState(next);
  };
  useEffect(
    () => () => {
      if (phase.current.kind === "running") phase.current.controller.abort();
      phase.current = { kind: "idle" };
    },
    [],
  );
  const [legacyContent, setLegacyContent] = useState("");
  const [legacyName, setLegacyName] = useState("");
  const [legacyDestination, setLegacyDestination] = useState("");
  const run = async (action: RecoveryAction) => {
    if (phase.current.kind === "running") return;
    const running: Extract<RecoveryState, { kind: "running" }> = {
      kind: "running",
      action,
      controller: new AbortController(),
    };
    publish(running);
    const current = () =>
      phase.current === running && !running.controller.signal.aborted;
    try {
      const result = await performRecovery(action, current);
      if (current())
        publish(
          result ? { kind: "notice", message: result } : { kind: "idle" },
        );
    } catch {
      if (current()) publish({ kind: "unavailable" });
    }
  };
  const performRecovery = async (
    action: RecoveryAction,
    current: () => boolean,
  ): Promise<string> => {
    switch (action) {
      case "check":
        return await resolve(current);
      case "reload": {
        const bundle = makeMarkdownRecovery(owner, operation, source);
        saveMarkdownRecovery(bundle);
        window.location.reload();
        return "";
      }
      case "export":
        setTransfer(
          JSON.stringify(makeMarkdownRecovery(owner, operation, source)),
        );
        return "";
      case "restore": {
        const raw = window.sessionStorage.getItem(
          markdownRecoveryStorageKey(owner),
        );
        if (!raw) throw new Error("No saved recovery draft");
        return await resolve(
          current,
          validateMarkdownRecovery(raw, owner, operation),
        );
      }
      case "import":
        return await resolve(
          current,
          validateMarkdownRecovery(transfer, owner, operation),
        );
      case "legacy":
        return await resolve(
          current,
          makeMarkdownRecovery(owner, operation, {
            kind: "markdown",
            content: legacyContent,
            name: legacyName,
            destinationFolderId: legacyDestination,
          }),
        );
      default:
        return unreachable(action);
    }
  };
  const resolve = async (
    current: () => boolean,
    bundle?: MarkdownRecoveryBundle,
  ): Promise<string> => {
    const admission = await DOCUMENT_TRANSFORM_API.admission(operation.key);
    if (!current()) return "";
    if (admission) {
      onRestore({ ...operation, taskId: admission["task_id"] }, null);
      return "Original task found. Resume checking its status.";
    }
    if (bundle) {
      onRestore(operation, bundle.source);
      return "Exact recovery draft restored. Resume checking reuses the original submission key; it does not start a separate operation.";
    }
    return "No task was found yet. This does not prove the earlier submission was rejected. Restore the exact original draft before resuming.";
  };
  return (
    <div className="space-y-3 rounded-lg border p-3 text-sm">
      <p className="font-medium">Recover original import</p>
      <p className="break-all">Recovery reference: {operation.key}</p>
      <p>
        Do not start a new import. Reloading without saving the exact draft can
        lose it. Saving below stores sensitive draft text in this tab’s session
        storage, readable by this site, until the task is recovered or you sign
        out.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={busy}
          onClick={() => {
            void run("check");
          }}
          type="button"
          variant="outline"
        >
          Check original admission
        </Button>
        <Button
          disabled={busy || !source}
          onClick={() => {
            void run("reload");
          }}
          type="button"
          variant="outline"
        >
          Save recovery draft and reload
        </Button>
        <Button
          disabled={busy || !source}
          onClick={() => {
            void run("export");
          }}
          type="button"
          variant="outline"
        >
          Show exact recovery bundle
        </Button>
        <Button
          disabled={busy}
          onClick={() => {
            void run("restore");
          }}
          type="button"
          variant="outline"
        >
          Restore saved recovery draft
        </Button>
      </div>
      <Label className="block space-y-1" htmlFor="polish-recovery-bundle">
        <span>Exact recovery bundle (private; never share publicly)</span>
        <Textarea
          disabled={busy}
          id="polish-recovery-bundle"
          onChange={(event) => setTransfer(event.target.value)}
          value={transfer}
        />
      </Label>
      <Button
        disabled={busy || !transfer.trim()}
        onClick={() => {
          void run("import");
        }}
        type="button"
        variant="outline"
      >
        Restore exact recovery bundle
      </Button>
      <p>
        For an older tab without an export button: preserve the original
        Markdown, source name, destination, and this recovery reference before
        reloading. Do not clear its reservation. A matching recovery bundle is
        required if admission lookup finds no task.
      </p>
      <details>
        <summary>Recover an older tab from the exact original values</summary>
        <p>
          Use only the exact values from this reserved submission. Changed
          values may conflict with the original request. This never changes the
          recovery reference or submits automatically.
        </p>
        <Label className="block" htmlFor="polish-recovery-original-name">
          Original source name
          <Input
            className="block w-full border"
            disabled={busy}
            id="polish-recovery-original-name"
            onChange={(event) => setLegacyName(event.target.value)}
            value={legacyName}
          />
        </Label>
        <Label className="block" htmlFor="polish-recovery-original-destination">
          Original destination folder ID
          <Input
            className="block w-full border"
            disabled={busy}
            id="polish-recovery-original-destination"
            onChange={(event) => setLegacyDestination(event.target.value)}
            value={legacyDestination}
          />
        </Label>
        <Label className="block" htmlFor="polish-recovery-original-markdown">
          Exact original Markdown
          <Textarea
            disabled={busy}
            id="polish-recovery-original-markdown"
            onChange={(event) => setLegacyContent(event.target.value)}
            value={legacyContent}
          />
        </Label>
        <Button
          disabled={busy || !legacyContent || !legacyName || !legacyDestination}
          onClick={() => {
            void run("legacy");
          }}
          type="button"
          variant="outline"
        >
          Restore original values with reserved key
        </Button>
      </details>
      {message ? <p role="status">{message}</p> : null}
    </div>
  );
}

function recoveryMessage(state: RecoveryState): string {
  switch (state.kind) {
    case "idle":
    case "running":
      return "";
    case "notice":
      return state.message;
    case "unavailable":
      return "Recovery could not complete. Keep the original operation reserved and preserve your draft.";
    default:
      return unreachable(state);
  }
}

function unreachable(value: never): never {
  throw new Error(`Unsupported recovery state: ${String(value)}`);
}
