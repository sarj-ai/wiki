"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Bot, ChevronLeft, FileStack, FolderOpen, Loader2, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api-error";
import { documentAliasError } from "@/lib/document-alias-validation";
import { smartCreateApi } from "@/lib/smart-create-client";
import {
  defaultAlias,
  defaultEmoji,
  initialSmartCreateState,
  type SmartCreateState,
  type SmartCreationPlan,
  sourceForTemplate,
  switchAuthoringMode,
} from "@/lib/smart-create-types";
import { cn } from "@/lib/utils";
import { injectNewFileIntoCaches } from "@/lib/wiki-cache";
import { MIME_DOC } from "@/types/wiki";

import { FolderPickerDialog } from "./folder-picker-dialog";

export function SmartCreateDialog({
  invokingDriveId,
  invokingFolderId,
  onOpenChange,
  open,
}: SmartCreateDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [state, setState] = useState<SmartCreateState>(initialSmartCreateState);
  const [templates, setTemplates] = useState<
    Awaited<ReturnType<typeof smartCreateApi.listTemplates>>
  >([]);
  const [catalogStatus, setCatalogStatus] = useState<"error" | "loading" | "ready">("loading");
  const [submitting, setSubmitting] = useState(false);
  const [creationStage, setCreationStage] = useState<"enqueuing" | "idle" | "waiting">("idle");
  const [destinationOpen, setDestinationOpen] = useState(false);
  const catalogSequence = useRef(0);
  const proposalSequence = useRef(0);
  const submitLatch = useRef(false);
  const createAbort = useRef<AbortController | null>(null);
  const idempotencyKey = useRef<null | string>(null);

  useEffect(() => {
    if (!open) return;
    const sequence = ++catalogSequence.current;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Opening the dialog intentionally starts a fresh catalog load.
    setCatalogStatus("loading");
    void smartCreateApi
      .listTemplates()
      .then((result) => {
        if (sequence !== catalogSequence.current) return;
        setTemplates(result);
        setState((current) => reconcileTemplateSelection(current, result));
        setCatalogStatus("ready");
      })
      .catch(() => {
        if (sequence !== catalogSequence.current) return;
        setCatalogStatus("error");
      });
    return () => {
      catalogSequence.current += 1;
    };
  }, [open]);

  const close = () => {
    catalogSequence.current += 1;
    proposalSequence.current += 1;
    createAbort.current?.abort();
    createAbort.current = null;
    submitLatch.current = false;
    idempotencyKey.current = null;
    setSubmitting(false);
    setCreationStage("idle");
    setState(initialSmartCreateState());
    onOpenChange(false);
  };

  const refreshTemplates = async () => {
    const sequence = ++catalogSequence.current;
    setCatalogStatus("loading");
    try {
      const result = await smartCreateApi.listTemplates(true);
      if (sequence !== catalogSequence.current) return;
      setTemplates(result);
      setState((current) => reconcileTemplateSelection(current, result));
      setCatalogStatus("ready");
    } catch {
      if (sequence !== catalogSequence.current) return;
      setCatalogStatus("error");
      toast.error("Couldn’t refresh templates");
    }
  };

  const updateMode = (next: SmartCreateState["mode"]) => {
    proposalSequence.current += 1;
    submitLatch.current = false;
    setSubmitting(false);
    idempotencyKey.current = null;
    setState({ mode: next, phase: "compose" });
  };

  const continueFromCompose = async () => {
    if (state.phase !== "compose" || submitLatch.current) return;
    if (state.mode.kind === "template") {
      if (state.mode.selection.kind !== "selected") return;
      const template = state.mode.selection.template;
      setState({
        mode: { kind: "template", selection: state.mode.selection },
        phase: "review",
        plan: {
          alias: defaultAlias(template.name),
          destination: {
            driveId: template.driveId,
            driveName: template.driveName,
            folderId: template.driveId === invokingDriveId ? invokingFolderId : template.driveId,
            folderPath: template.driveId === invokingDriveId ? ["Selected folder"] : [],
          },
          emoji: defaultEmoji(template.mimeType),
          name: template.name,
          source: sourceForTemplate(template),
        },
      });
      return;
    }

    const description = state.mode.description.trim();
    if (!description) return;
    const sequence = ++proposalSequence.current;
    submitLatch.current = true;
    setSubmitting(true);
    try {
      const plan = await smartCreateApi.propose({
        description,
        fileType: state.mode.fileType,
        invokingDriveId,
        invokingFolderId,
      });
      if (sequence !== proposalSequence.current) return;
      setState({ mode: state.mode, phase: "review", plan });
    } catch (error: unknown) {
      if (sequence !== proposalSequence.current) return;
      toast.error(errorMessage(error, "Couldn’t suggest file details"));
    } finally {
      if (sequence === proposalSequence.current) {
        // eslint-disable-next-line require-atomic-updates -- Sequence identity ensures this request still owns the latch.
        submitLatch.current = false;
        setSubmitting(false);
      }
    }
  };

  const updatePlan = (update: (plan: SmartCreationPlan) => SmartCreationPlan) => {
    idempotencyKey.current = null;
    setState((current) =>
      current.phase === "review" ? { ...current, plan: update(current.plan) } : current,
    );
  };

  const create = async () => {
    if (state.phase !== "review" || submitLatch.current || !isValidPlan(state.plan)) return;
    submitLatch.current = true;
    setSubmitting(true);
    setCreationStage("enqueuing");
    idempotencyKey.current ??= crypto.randomUUID();
    const controller = new AbortController();
    createAbort.current?.abort();
    createAbort.current = controller;
    try {
      const task = await smartCreateApi.create(state.plan, idempotencyKey.current);
      controller.signal.throwIfAborted();
      setCreationStage("waiting");
      const result = await smartCreateApi.waitForCompletion(task, controller.signal);
      injectNewFileIntoCaches(queryClient, result.file);
      for (const warning of result.warnings) toast.warning(warning.detail);
      toast.success(`Created “${result.file.name}”`);
      close();
      router.push(`/d/${encodeURIComponent(result.file.id)}`);
    } catch (error: unknown) {
      toast.error(errorMessage(error, "Couldn’t create file"));
    } finally {
      if (createAbort.current === controller) {
        createAbort.current = null;
        // eslint-disable-next-line require-atomic-updates -- Controller identity ensures this request still owns the latch.
        submitLatch.current = false;
        setSubmitting(false);
        setCreationStage("idle");
      }
    }
  };

  return (
    <>
      <Dialog
        onOpenChange={(next) => {
          if (!next && !submitting) close();
        }}
        open={open}
      >
        <DialogContent className="max-h-[calc(100dvh-1rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-2xl">
          <div className="border-b px-4 pt-5 pb-4 sm:px-6 sm:pt-6">
            <DialogHeader>
              <DialogTitle>{state.phase === "compose" ? "Create file" : "Review file"}</DialogTitle>
              <DialogDescription className="sr-only">
                {state.phase === "compose"
                  ? "Choose how to create the file"
                  : "Review file details"}
              </DialogDescription>
            </DialogHeader>
          </div>

          <div className="overflow-y-auto px-4 py-4 sm:px-6 sm:py-5">
            {state.phase === "compose" ? (
              <ComposeStep
                catalogStatus={catalogStatus}
                disabled={submitting}
                mode={state.mode}
                onModeChange={updateMode}
                onRefresh={() => void refreshTemplates()}
                onSwitch={(kind) => {
                  updateMode(switchAuthoringMode(state, kind).mode);
                }}
                templates={templates}
              />
            ) : (
              <div className="space-y-4">
                {creationStage !== "idle" ? (
                  <div
                    aria-live="polite"
                    className="bg-primary/5 border-primary/20 flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm"
                  >
                    <Loader2 className="text-primary size-4 shrink-0 animate-spin" />
                    <span>
                      {creationStage === "enqueuing"
                        ? "Starting file creation…"
                        : "Creating in Drive… You can keep this window open."}
                    </span>
                  </div>
                ) : null}
                <ReviewStep
                  onChange={updatePlan}
                  onChooseDestination={() => {
                    setDestinationOpen(true);
                  }}
                  plan={state.plan}
                  templates={templates}
                />
              </div>
            )}
          </div>

          <DialogFooter className="bg-background grid grid-cols-2 border-t px-4 py-3 sm:flex sm:justify-between sm:px-6 sm:py-4">
            <Button
              className="w-full sm:w-auto"
              disabled={submitting}
              onClick={() => {
                if (state.phase === "review") {
                  idempotencyKey.current = null;
                  setState({ mode: state.mode, phase: "compose" });
                } else {
                  close();
                }
              }}
              type="button"
              variant="ghost"
            >
              {state.phase === "review" ? <ChevronLeft className="size-4" /> : null}
              {state.phase === "review" ? "Back" : "Cancel"}
            </Button>
            <Button
              className="w-full sm:w-auto"
              disabled={
                submitting ||
                (state.phase === "compose" ? !canContinue(state) : !isValidPlan(state.plan))
              }
              onClick={() => {
                void (state.phase === "compose" ? continueFromCompose() : create());
              }}
              type="button"
            >
              {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
              {submitting
                ? state.phase === "compose"
                  ? "Thinking…"
                  : creationStage === "enqueuing"
                    ? "Starting…"
                    : "Creating…"
                : state.phase === "compose"
                  ? state.mode.kind === "ai"
                    ? "Suggest details"
                    : "Review"
                  : "Create file"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {destinationOpen ? (
        <FolderPickerDialog
          {...(state.phase === "review" && state.mode.kind === "template"
            ? { allowedDriveId: state.mode.selection.template.driveId }
            : {})}
          onOpenChange={setDestinationOpen}
          onSelect={(destination) => {
            updatePlan((plan) => {
              if (
                state.phase === "review" &&
                state.mode.kind === "template" &&
                destination.driveId !== state.mode.selection.template.driveId
              ) {
                return plan;
              }
              return {
                ...plan,
                destination,
                source:
                  state.phase === "review" &&
                  state.mode.kind === "ai" &&
                  plan.source.kind === "template" &&
                  plan.source.templateDriveId !== destination.driveId
                    ? {
                        fileType: plan.source.mimeType === MIME_DOC ? "document" : "presentation",
                        kind: "blank",
                      }
                    : plan.source,
              };
            });
          }}
          open
        />
      ) : null}
    </>
  );
}

interface SmartCreateDialogProps {
  invokingDriveId: string;
  invokingFolderId: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}

interface ComposeStepProps {
  catalogStatus: "error" | "loading" | "ready";
  disabled: boolean;
  mode: SmartCreateState["mode"];
  onModeChange: (mode: SmartCreateState["mode"]) => void;
  onRefresh: () => void;
  onSwitch: (kind: SmartCreateState["mode"]["kind"]) => void;
  templates: Awaited<ReturnType<typeof smartCreateApi.listTemplates>>;
}

function ComposeStep({
  catalogStatus,
  disabled,
  mode,
  onModeChange,
  onRefresh,
  onSwitch,
  templates,
}: ComposeStepProps) {
  return (
    <div className="space-y-5">
      <Tabs
        onValueChange={(value) => {
          onSwitch(value === "template" ? "template" : "ai");
        }}
        value={mode.kind}
      >
        <TabsList aria-label="Starting point" className="grid w-full grid-cols-2">
          <TabsTrigger
            disabled={disabled || (catalogStatus === "ready" && templates.length === 0)}
            value="template"
          >
            <FileStack className="size-4" /> Template
          </TabsTrigger>
          <TabsTrigger disabled={disabled} value="ai">
            <Bot className="size-4" /> Describe
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {mode.kind === "ai" ? (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="smart-description">What are you making?</Label>
            <Textarea
              className="min-h-28"
              disabled={disabled}
              id="smart-description"
              maxLength={2_000}
              onChange={(event) => {
                onModeChange({ ...mode, description: event.target.value });
              }}
              placeholder="A PRD for improving the new-hire onboarding flow…"
              value={mode.description}
            />
          </div>
          <div className="space-y-1.5">
            <Label>File type</Label>
            <Tabs
              onValueChange={(value) => {
                onModeChange({ ...mode, fileType: value === "presentation" ? value : "document" });
              }}
              value={mode.fileType}
            >
              <TabsList className="grid w-full grid-cols-2 sm:w-72">
                <TabsTrigger disabled={disabled} value="document">
                  Document
                </TabsTrigger>
                <TabsTrigger disabled={disabled} value="presentation">
                  Presentation
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>
      ) : (
        <TemplateCatalog
          catalogStatus={catalogStatus}
          disabled={disabled}
          onRefresh={onRefresh}
          onSelect={(selectedTemplate) => {
            onModeChange({
              kind: "template",
              selection: { kind: "selected", template: selectedTemplate },
            });
          }}
          {...(mode.selection.kind === "selected"
            ? { selectedId: mode.selection.template.fileId }
            : {})}
          templates={templates}
        />
      )}
    </div>
  );
}

interface TemplateCatalogProps {
  catalogStatus: "error" | "loading" | "ready";
  disabled: boolean;
  onRefresh: () => void;
  onSelect: (template: Awaited<ReturnType<typeof smartCreateApi.listTemplates>>[number]) => void;
  selectedId?: string;
  templates: Awaited<ReturnType<typeof smartCreateApi.listTemplates>>;
}

function TemplateCatalog({
  catalogStatus,
  disabled,
  onRefresh,
  onSelect,
  selectedId,
  templates,
}: TemplateCatalogProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Templates in Drive</Label>
        <Button
          disabled={disabled || catalogStatus === "loading"}
          onClick={onRefresh}
          size="xs"
          type="button"
          variant="ghost"
        >
          <RefreshCw className={cn("size-3.5", catalogStatus === "loading" && "animate-spin")} />
          Refresh
        </Button>
      </div>
      <ScrollArea className="h-[min(16rem,40dvh)] rounded-md border">
        <div className="space-y-1 p-1.5">
          {catalogStatus === "loading" ? (
            <div className="text-muted-foreground flex h-24 items-center justify-center gap-2 text-sm">
              <Loader2 className="size-4 animate-spin" /> Looking in Templates…
            </div>
          ) : null}
          {catalogStatus !== "loading" && templates.length === 0 ? (
            <div className="text-muted-foreground flex h-36 flex-col items-center justify-center gap-2 px-6 text-center text-sm">
              <FolderOpen className="size-6" />
              <p>No templates available.</p>
              <p className="text-xs">
                Add a native Doc or Slides file to a root-level Templates folder.
              </p>
            </div>
          ) : null}
          {catalogStatus !== "loading"
            ? templates.map((template) => (
                <Button
                  className={cn(
                    "h-auto w-full items-start justify-start gap-3 rounded-md px-3 py-2.5 text-left whitespace-normal",
                    selectedId === template.fileId && "bg-primary/10",
                  )}
                  disabled={disabled}
                  key={template.fileId}
                  onClick={() => {
                    onSelect(template);
                  }}
                  type="button"
                  variant="ghost"
                >
                  <span className="pt-0.5 text-lg">{defaultEmoji(template.mimeType)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{template.name}</span>
                    <span className="text-muted-foreground block truncate text-xs">
                      {[template.driveName, ...template.categoryPath].join(" / ")}
                    </span>
                    {template.description ? (
                      <span className="text-muted-foreground mt-1 line-clamp-2 block text-xs">
                        {template.description}
                      </span>
                    ) : null}
                  </span>
                  <Badge variant="outline">
                    {template.mimeType === MIME_DOC ? "Doc" : "Slides"}
                  </Badge>
                </Button>
              ))
            : null}
        </div>
      </ScrollArea>
    </div>
  );
}

interface ReviewStepProps {
  onChange: (update: (plan: SmartCreationPlan) => SmartCreationPlan) => void;
  onChooseDestination: () => void;
  plan: SmartCreationPlan;
  templates: Awaited<ReturnType<typeof smartCreateApi.listTemplates>>;
}

function ReviewStep({ onChange, onChooseDestination, plan, templates }: ReviewStepProps) {
  const templateFileId = plan.source.kind === "template" ? plan.source.templateFileId : undefined;
  const selectedTemplate = templates.find((template) => template.fileId === templateFileId);
  return (
    <div className="space-y-5">
      <div className="bg-muted/45 flex items-center justify-between rounded-lg border px-3 py-2.5">
        <div className="min-w-0">
          <p className="text-muted-foreground text-xs">Starting point</p>
          <p className="truncate text-sm font-medium">
            {plan.source.kind === "blank"
              ? `Blank ${plan.source.fileType}`
              : (selectedTemplate?.name ?? "Drive template")}
          </p>
        </div>
        <Badge variant="secondary">{plan.source.kind === "blank" ? "Blank" : "Template"}</Badge>
      </div>
      <div className="grid gap-4 sm:grid-cols-[5rem_1fr]">
        <div className="space-y-1.5">
          <Label htmlFor="smart-emoji">Emoji</Label>
          <Input
            className="text-center text-xl"
            id="smart-emoji"
            maxLength={16}
            onChange={(event) => {
              onChange((current) => ({ ...current, emoji: event.target.value }));
            }}
            value={plan.emoji}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="smart-name">Name</Label>
          <Input
            id="smart-name"
            maxLength={255}
            onChange={(event) => {
              onChange((current) => ({ ...current, name: event.target.value }));
            }}
            value={plan.name}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="smart-alias">Short link</Label>
        <div className="border-input focus-within:border-ring focus-within:ring-ring/50 flex rounded-md border shadow-xs focus-within:ring-[3px]">
          <span className="bg-muted text-muted-foreground border-input h-9 rounded-l-md border-r px-3 font-mono text-xs leading-9">
            go/
          </span>
          <Input
            aria-describedby="smart-alias-error"
            aria-invalid={documentAliasError(plan.alias) !== null}
            autoCapitalize="none"
            autoComplete="off"
            className="border-0 font-mono shadow-none focus-visible:ring-0"
            id="smart-alias"
            onChange={(event) => {
              onChange((current) => ({ ...current, alias: event.target.value }));
            }}
            spellCheck={false}
            value={plan.alias}
          />
        </div>
        {documentAliasError(plan.alias) !== null ? (
          <p className="text-destructive text-xs" id="smart-alias-error" role="alert">
            {documentAliasError(plan.alias)}
          </p>
        ) : null}
      </div>
      <div className="space-y-1.5">
        <Label>Destination</Label>
        <Button
          className="h-auto w-full justify-between px-3 py-2 text-left"
          onClick={onChooseDestination}
          type="button"
          variant="outline"
        >
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{plan.destination.driveName}</span>
            <span className="text-muted-foreground block truncate text-xs">
              {plan.destination.folderPath.length > 0
                ? plan.destination.folderPath.join(" / ")
                : "Workspace root"}
            </span>
          </span>
          <span className="text-muted-foreground shrink-0 text-xs">Change</span>
        </Button>
      </div>
    </div>
  );
}

function canContinue(state: Extract<SmartCreateState, { phase: "compose" }>): boolean {
  return state.mode.kind === "ai"
    ? state.mode.description.trim().length > 0
    : state.mode.selection.kind === "selected";
}

function isValidPlan(plan: SmartCreationPlan): boolean {
  return (
    plan.name.trim().length > 0 &&
    plan.emoji.trim().length > 0 &&
    documentAliasError(plan.alias) === null
  );
}

function reconcileTemplateSelection(
  state: SmartCreateState,
  templates: Awaited<ReturnType<typeof smartCreateApi.listTemplates>>,
): SmartCreateState {
  if (
    state.phase !== "compose" ||
    state.mode.kind !== "template" ||
    state.mode.selection.kind !== "selected"
  ) {
    return state;
  }
  const selected = state.mode.selection.template;
  const current = templates.find(
    (template) =>
      template.fileId === selected.fileId &&
      template.driveId === selected.driveId &&
      template.mimeType === selected.mimeType &&
      template.modifiedTime === selected.modifiedTime,
  );
  return current === undefined
    ? { mode: { kind: "template", selection: { kind: "unselected" } }, phase: "compose" }
    : {
        mode: { kind: "template", selection: { kind: "selected", template: current } },
        phase: "compose",
      };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}
