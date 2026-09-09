"use client";

import { useRef, useState } from "react";

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

type RecoveryAction = "check" | "export" | "import" | "legacy" | "reload" | "restore";

export function DocumentPolishRecovery({ owner, operation, source, onRestore }: Props) {
  const [transfer, setTransfer] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [legacyContent, setLegacyContent] = useState("");
  const [legacyName, setLegacyName] = useState("");
  const [legacyDestination, setLegacyDestination] = useState("");
  const running = useRef(false);
  const finishRecovery = () => {
    running.current = false;
    setBusy(false);
  };
  const run = async (action: RecoveryAction) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setMessage("");
    try {
      await performRecovery(action);
    } catch {
      setMessage(
        "Recovery could not complete. Keep the original operation reserved and preserve your draft.",
      );
    } finally {
      finishRecovery();
    }
  };
  const resolve = async (bundle?: MarkdownRecoveryBundle) => {
    const admission = await DOCUMENT_TRANSFORM_API.admission(operation.key);
    if (admission) {
      onRestore({ ...operation, taskId: admission.task_id }, null);
      setMessage("Original task found. Resume checking its status.");
    } else if (bundle) {
      onRestore(operation, bundle.source);
      setMessage(
        "Exact recovery draft restored. Resume checking reuses the original submission key; it does not start a separate operation.",
      );
    } else {
      setMessage(
        "No task was found yet. This does not prove the earlier submission was rejected. Restore the exact original draft before resuming.",
      );
    }
  };
  const performRecovery = async (action: RecoveryAction) => {
    switch (action) {
      case "check":
        await resolve();
        break;
      case "reload": {
        const bundle = makeMarkdownRecovery(owner, operation, source);
        saveMarkdownRecovery(bundle);
        window.location.reload();
        break;
      }
      case "export":
        setTransfer(JSON.stringify(makeMarkdownRecovery(owner, operation, source)));
        break;
      case "restore": {
        const raw = window.sessionStorage.getItem(markdownRecoveryStorageKey(owner));
        if (!raw) throw new Error("No saved recovery draft");
        await resolve(validateMarkdownRecovery(raw, owner, operation));
        break;
      }
      case "import":
        await resolve(validateMarkdownRecovery(transfer, owner, operation));
        break;
      case "legacy":
        await resolve(
          makeMarkdownRecovery(owner, operation, {
            kind: "markdown",
            content: legacyContent,
            name: legacyName,
            destinationFolderId: legacyDestination,
          }),
        );
        break;
    }
  };
  return (
    <div className="space-y-3 rounded-lg border p-3 text-sm">
      <p className="font-medium">Recover original import</p>
      <p className="break-all">Recovery reference: {operation.key}</p>
      <p>
        Do not start a new import. Reloading without saving the exact draft can lose it. Saving
        below stores sensitive draft text in this tab’s session storage, readable by this site,
        until the task is recovered or you sign out.
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
        For an older tab without an export button: preserve the original Markdown, source name,
        destination, and this recovery reference before reloading. Do not clear its reservation. A
        matching recovery bundle is required if admission lookup finds no task.
      </p>
      <details>
        <summary>Recover an older tab from the exact original values</summary>
        <p>
          Use only the exact values from this reserved submission. Changed values may conflict with
          the original request. This never changes the recovery reference or submits automatically.
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
