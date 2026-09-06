"use client";

import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

type ConsentStatus = "approved" | "denied" | "expired" | "pending";
type DisplayStatus = "approved-recovered" | "checking" | "submitting" | "unknown" | ConsentStatus;

interface McpConsentProps {
  accountEmail: string;
  clientName: string;
  initialStatus?: ConsentStatus;
  onDecision: (decision: {
    approve: boolean;
    transformApproved: boolean;
  }) => Promise<"approved" | "denied">;
  onRecover: () => Promise<ConsentStatus>;
  requestsTransform: boolean;
}

const STATUS_COPY: Record<DisplayStatus, string> = {
  approved:
    "Access approved. Return to your app to finish connecting. No document has been changed.",
  "approved-recovered":
    "Access was approved, but the connection handoff was not confirmed. Start a new connection from your app. No document has been changed.",
  checking: "Checking your decision…",
  denied: "Access denied. This request cannot connect to Wiki.",
  expired: "This request expired. Start a new connection from your app.",
  pending: "You can disconnect this app later without signing out of Wiki.",
  submitting: "Saving your decision…",
  unknown: "We could not confirm your decision. Check its status before trying again.",
};

export function McpConsent({
  accountEmail,
  clientName,
  initialStatus = "pending",
  requestsTransform,
  onDecision,
  onRecover,
}: McpConsentProps) {
  const headingId = useId();
  const editingId = useId();
  const editingDescriptionId = useId();
  const [transformApproved, setTransformApproved] = useState(false);
  const [status, setStatus] = useState<DisplayStatus>(
    initialStatus === "approved" ? "approved-recovered" : initialStatus,
  );
  const decisionLocked = useRef(initialStatus !== "pending");
  const recoveryLocked = useRef(false);
  const disabled = status !== "pending";

  function unlockRecovery(): void {
    recoveryLocked.current = false;
  }

  async function decide(approve: boolean): Promise<void> {
    if (decisionLocked.current) return;
    decisionLocked.current = true;
    setStatus("submitting");
    try {
      setStatus(
        await onDecision({
          approve,
          transformApproved: approve && requestsTransform && transformApproved,
        }),
      );
    } catch {
      setStatus("unknown");
    }
  }

  async function recover(): Promise<void> {
    if (recoveryLocked.current) return;
    recoveryLocked.current = true;
    setStatus("checking");
    try {
      const confirmed = await onRecover();
      decisionLocked.current = confirmed !== "pending";
      setStatus(confirmed === "approved" ? "approved-recovered" : confirmed);
    } catch {
      setStatus("unknown");
    } finally {
      unlockRecovery();
    }
  }

  return (
    <section
      aria-labelledby={headingId}
      className="bg-card text-card-foreground mx-auto w-full max-w-lg rounded-xl border p-6 sm:p-8"
    >
      <h1 className="text-2xl font-semibold" id={headingId}>
        Connect an app to Wiki
      </h1>
      <dl className="mt-6 space-y-3 text-sm">
        <div>
          <dt className="text-muted-foreground">Requesting app</dt>
          <dd className="font-semibold wrap-anywhere">
            <bdi>{clientName || "Unnamed app"}</bdi>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Your Wiki account</dt>
          <dd className="wrap-anywhere">
            <bdi>{accountEmail}</bdi>
          </dd>
        </div>
      </dl>
      <p className="text-muted-foreground mt-3 text-sm">
        The app supplies its name. Only continue if you started this connection and trust the app.
      </p>
      <div className="my-6 border-y py-6">
        <h2 className="font-semibold">Read documents you can access</h2>
        <p className="text-muted-foreground mt-2 text-sm">
          Find and read your accessible Wiki documents and existing short links. This does not give
          the app access to documents you cannot open.
        </p>
        {requestsTransform ? (
          <div className="mt-6 flex items-start gap-3">
            <Checkbox
              aria-describedby={editingDescriptionId}
              checked={transformApproved}
              className="mt-1"
              disabled={disabled}
              id={editingId}
              onCheckedChange={(checked) => setTransformApproved(checked === true)}
            />
            <div>
              <label className="font-semibold" htmlFor={editingId}>
                Also allow document changes
              </label>
              <p className="text-muted-foreground mt-2 text-sm" id={editingDescriptionId}>
                The app can request polishing and reorganization, including editing original
                documents in place. Leave this unchecked for read-only access.
              </p>
            </div>
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap justify-end gap-3">
        <Button disabled={disabled} onClick={() => void decide(false)} variant="outline">
          Deny
        </Button>
        <Button disabled={disabled} onClick={() => void decide(true)}>
          {transformApproved && requestsTransform
            ? "Allow read and edit access"
            : "Allow read access"}
        </Button>
      </div>
      <p
        className="text-muted-foreground mt-4 text-sm"
        role={status === "unknown" ? "alert" : "status"}
      >
        {STATUS_COPY[status]}
      </p>
      {(status === "unknown" || status === "checking") && (
        <Button
          className="mt-3"
          disabled={status === "checking"}
          onClick={() => void recover()}
          variant="outline"
        >
          Check status
        </Button>
      )}
    </section>
  );
}
