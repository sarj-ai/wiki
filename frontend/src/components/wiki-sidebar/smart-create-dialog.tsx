"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ChevronLeft,
  FileStack,
  FolderOpen,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  type Dispatch,
  type RefObject,
  type SetStateAction,
  useEffect,
  useRef,
  useState,
} from "react";
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
import { useCurrentUser } from "@/hooks/use-current-user";
import { ApiError } from "@/lib/api-error";
import { documentAliasError } from "@/lib/document-alias-error";
import { smartCreateApi } from "@/lib/smart-create-client";
import {
  clearSmartCreateReceipt,
  readSmartCreateReceipt,
  saveSmartCreateReceipt,
  type SmartCreateReceipt,
} from "@/lib/smart-create-recovery";
import {
  defaultAlias,
  defaultEmoji,
  type DriveTemplate,
  initialSmartCreateState,
  type SmartCreateState,
  type SmartCreationPlan,
  type SmartCreateTask,
  sourceForTemplate,
  switchAuthoringMode,
} from "@/lib/smart-create-types";
import { cn } from "@/lib/utils";
import { injectNewFileIntoCaches } from "@/lib/wiki-cache";
import { MIME_DOC } from "@/types/wiki";

import { FolderPickerDialog } from "./folder-picker-dialog";

type TemplateCatalogStatus =
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "closed" }
  | { readonly kind: "loading"; readonly controller: AbortController }
  | { readonly kind: "ready"; readonly templates: DriveTemplate[] };

type CreationState =
  | { readonly kind: "idle" | "proposing" | "restoring" | "unmounted" }
  | {
      readonly kind: "enqueuing";
      readonly receipt: Extract<SmartCreateReceipt, { kind: "unconfirmed" }>;
      readonly controller: AbortController;
    }
  | {
      readonly kind: "waiting";
      readonly receipt: Extract<SmartCreateReceipt, { kind: "admitted" }>;
      readonly controller: AbortController;
    }
  | {
      readonly kind: "checking";
      readonly receipt: SmartCreateReceipt;
      readonly controller: AbortController;
    }
  | { readonly kind: "blocked"; readonly message: string }
  | {
      readonly kind: "uncertain";
      readonly receipt: SmartCreateReceipt;
      readonly message: string;
    }
  | {
      readonly kind: "terminal";
      readonly task: Extract<
        SmartCreateTask,
        { status: "failed" | "cancelled" }
      >;
    };

export function SmartCreateDialog(props: SmartCreateDialogProps) {
  const user = useCurrentUser();
  const owner = user.data?.id;
  return owner === undefined ? null : (
    <DialogForOwner
      key={JSON.stringify([
        owner,
        props.invokingDriveId,
        props.invokingFolderId,
      ])}
      {...props}
      owner={owner}
    />
  );
}

function DialogForOwner({
  owner,
  invokingDriveId,
  invokingFolderId,
  onOpenChange,
  open,
}: SmartCreateDialogProps & { owner: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [state, setState] = useState<SmartCreateState>(initialSmartCreateState);
  const [catalogStatus, setCatalogStatus] = useState<TemplateCatalogStatus>({
    kind: "closed",
  });
  const catalogPhase = useRef<TemplateCatalogStatus>(catalogStatus);
  const templates = catalogTemplates(catalogStatus);
  const scope = JSON.stringify([invokingDriveId, invokingFolderId]);
  const [creation, setCreation] = useState<CreationState>({
    kind: "restoring",
  });
  const phase = useRef<CreationState>(creation);
  const publish = (next: CreationState) => {
    publishCreation(phase, setCreation, next);
  };
  const activity = creationActivity(creation);
  const submitting = activity === "busy";
  const locked = activity !== "editable";
  const creationStage =
    creation.kind === "enqueuing"
      ? "enqueuing"
      : creation.kind === "waiting"
        ? "waiting"
        : "idle";
  useEffect(() => {
    publishCreation(phase, setCreation, initialCreation(owner, scope));
    return () => {
      const current = phase.current;
      if (
        current.kind === "enqueuing" ||
        current.kind === "waiting" ||
        current.kind === "checking"
      )
        current.controller.abort();
      phase.current = { kind: "unmounted" };
    };
  }, [owner, scope]);
  const [destinationOpen, setDestinationOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const loading: Extract<TemplateCatalogStatus, { kind: "loading" }> = {
      kind: "loading",
      controller: new AbortController(),
    };
    publishCatalog(catalogPhase, setCatalogStatus, loading);
    void (async () => {
      const result = await smartCreateApi.listTemplates(
        false,
        loading.controller.signal,
      );
      if (catalogPhase.current !== loading) return;
      const selected = templatesInDrive(result, invokingDriveId);
      publishCatalog(catalogPhase, setCatalogStatus, {
        kind: "ready",
        templates: selected,
      });
      if (phase.current.kind === "idle")
        setState((current) => reconcileTemplateSelection(current, selected));
    })().catch((error: unknown) => {
      if (catalogPhase.current !== loading) return;
      publishCatalog(catalogPhase, setCatalogStatus, {
        kind: "error",
        message: errorMessage(error, "Templates couldn’t be loaded."),
      });
    });
    return () => {
      if (catalogPhase.current.kind === "loading")
        catalogPhase.current.controller.abort();
      catalogPhase.current = { kind: "closed" };
    };
  }, [invokingDriveId, open]);

  const close = () => {
    if (catalogPhase.current.kind === "loading")
      catalogPhase.current.controller.abort();
    publishCatalog(catalogPhase, setCatalogStatus, { kind: "closed" });
    if (phase.current.kind === "idle" || phase.current.kind === "terminal")
      publish({ kind: "idle" });
    setState(initialSmartCreateState());
    onOpenChange(false);
  };

  const editable = () => phase.current.kind === "idle";
  const refreshTemplates = async () => {
    if (catalogPhase.current.kind === "closed" || phase.current.kind !== "idle")
      return;
    if (catalogPhase.current.kind === "loading")
      catalogPhase.current.controller.abort();
    const loading: Extract<TemplateCatalogStatus, { kind: "loading" }> = {
      kind: "loading",
      controller: new AbortController(),
    };
    publishCatalog(catalogPhase, setCatalogStatus, loading);
    try {
      const result = await smartCreateApi.listTemplates(
        true,
        loading.controller.signal,
      );
      if (catalogPhase.current !== loading) return;
      const selected = templatesInDrive(result, invokingDriveId);
      publishCatalog(catalogPhase, setCatalogStatus, {
        kind: "ready",
        templates: selected,
      });
      if (editable())
        setState((current) => reconcileTemplateSelection(current, selected));
    } catch (error: unknown) {
      if (catalogPhase.current !== loading) return;
      const message = errorMessage(error, "Templates couldn’t be loaded.");
      publishCatalog(catalogPhase, setCatalogStatus, {
        kind: "error",
        message,
      });
      toast.error(message);
    }
  };

  const updateMode = (next: SmartCreateState["mode"]) => {
    if (phase.current.kind !== "idle") return;
    setState({ mode: next, phase: "compose" });
  };

  const continueFromCompose = async () => {
    if (state.phase !== "compose" || phase.current.kind !== "idle") return;
    if (state.mode.kind === "template") {
      if (state.mode.selection.kind !== "selected") return;
      const template = state.mode.selection.template;
      if (template.driveId !== invokingDriveId) {
        toast.error("Choose a template from the current drive");
        return;
      }
      setState({
        mode: { kind: "template", selection: state.mode.selection },
        phase: "review",
        plan: {
          alias: defaultAlias(template.name),
          destination: {
            driveId: invokingDriveId,
            driveName: template.driveName,
            folderId: invokingFolderId,
            folderPath: ["Selected folder"],
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
    const proposing: CreationState = { kind: "proposing" };
    publish(proposing);
    try {
      const plan = await smartCreateApi.propose({
        description,
        fileType: state.mode.fileType,
        invokingDriveId,
        invokingFolderId,
      });
      if (phase.current !== proposing) return;
      setState({ mode: state.mode, phase: "review", plan });
    } catch (error: unknown) {
      if (phase.current !== proposing) return;
      toast.error(errorMessage(error, "Couldn’t suggest file details"));
    } finally {
      if (phase.current === proposing) publish({ kind: "idle" });
    }
  };

  const updatePlan = (
    update: (plan: SmartCreationPlan) => SmartCreationPlan,
  ) => {
    if (phase.current.kind !== "idle") return;
    setState((current) =>
      current.phase === "review"
        ? { ...current, plan: update(current.plan) }
        : current,
    );
  };

  const active = (controller: AbortController) => {
    const current = phase.current;
    return (current.kind === "enqueuing" ||
      current.kind === "waiting" ||
      current.kind === "checking") &&
      current.controller === controller &&
      !controller.signal.aborted
      ? current
      : null;
  };
  const failRead = (controller: AbortController, error: unknown) => {
    const current = active(controller);
    if (current)
      publish({
        kind: "uncertain",
        receipt: current.receipt,
        message: errorMessage(
          error,
          "File creation may still be running. Check the original request before starting another.",
        ),
      });
  };
  const observe = async (
    task: SmartCreateTask,
    key: string,
    controller: AbortController,
  ) => {
    if (!active(controller)) return;
    const receipt: SmartCreateReceipt = {
      kind: "admitted",
      key,
      taskId: task.id,
    };
    saveSmartCreateReceipt(owner, scope, receipt);
    publish({ kind: "waiting", receipt, controller });
    const terminal = await smartCreateApi.waitForCompletion(
      task,
      controller.signal,
    );
    if (!active(controller)) return;
    switch (terminal.status) {
      case "needs_review":
        publish({ kind: "uncertain", receipt, message: terminal.errorDetail });
        return;
      case "failed":
      case "cancelled":
        clearSmartCreateReceipt(owner, scope, key);
        publish({ kind: "terminal", task: terminal });
        return;
      case "succeeded": {
        clearSmartCreateReceipt(owner, scope, key);
        publish({ kind: "idle" });
        const result = terminal.result;
        injectNewFileIntoCaches(queryClient, result.file);
        for (const warning of result.warnings) toast.warning(warning.detail);
        toast.success(`Created “${result.file.name}”`);
        close();
        router.push(`/d/${encodeURIComponent(result.file.id)}`);
      }
    }
  };
  const create = async () => {
    if (
      state.phase !== "review" ||
      phase.current.kind !== "idle" ||
      !isValidPlan(state.plan)
    )
      return;
    const receipt: SmartCreateReceipt = {
      kind: "unconfirmed",
      key: crypto.randomUUID(),
    };
    const controller = new AbortController();
    try {
      // Persist the request identity before enqueueing; a lost response never authorizes a new POST.
      saveSmartCreateReceipt(owner, scope, receipt);
    } catch (error: unknown) {
      toast.error(
        errorMessage(
          error,
          "Save the recovery request before creating a file.",
        ),
      );
      return;
    }
    publish({ kind: "enqueuing", receipt, controller });
    try {
      await observe(
        await smartCreateApi.create(state.plan, receipt.key),
        receipt.key,
        controller,
      );
    } catch (error: unknown) {
      failRead(controller, error);
    }
  };
  const recover = async () => {
    if (phase.current.kind !== "uncertain") return;
    const receipt = phase.current.receipt;
    const controller = new AbortController();
    publish({ kind: "checking", receipt, controller });
    try {
      const taskId =
        receipt.kind === "admitted"
          ? receipt.taskId
          : await smartCreateApi.getAdmission(receipt.key, controller.signal);
      if (!active(controller)) return;
      if (taskId === null) {
        publish({
          kind: "uncertain",
          receipt,
          message:
            "No retained admission was found. This does not prove creation never started. Keep this request key for recovery.",
        });
        return;
      }
      await observe(
        await smartCreateApi.getTask(taskId, controller.signal),
        receipt.key,
        controller,
      );
    } catch (error: unknown) {
      failRead(controller, error);
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
              <DialogTitle>
                {state.phase === "compose" ? "Create file" : "Review file"}
              </DialogTitle>
              <DialogDescription className="sr-only">
                {state.phase === "compose"
                  ? "Choose how to create the file"
                  : "Review file details"}
              </DialogDescription>
            </DialogHeader>
          </div>

          <div className="overflow-y-auto p-4 sm:px-6 sm:py-5">
            {creation.kind === "uncertain" ? (
              <div role="alert" className="mb-4 space-y-2 text-sm">
                <p>{creation.message}</p>
                <p>Request: {creation.receipt.key}</p>
                <Button type="button" onClick={() => void recover()}>
                  Check original request
                </Button>
              </div>
            ) : null}
            {creation.kind === "blocked" ? (
              <p role="alert">{creation.message}</p>
            ) : null}
            {creation.kind === "terminal" ? (
              <p role="alert">
                {creation.task.status === "cancelled"
                  ? "File creation was cancelled."
                  : (creation.task.errorDetail ??
                    `File creation failed (${creation.task.errorCode})`)}
              </p>
            ) : null}
            <fieldset disabled={locked} className="min-w-0 space-y-4">
              {state.phase === "compose" ? (
                <ComposeStep
                  catalogStatus={catalogStatus}
                  disabled={locked}
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
            </fieldset>
          </div>

          <DialogFooter className="bg-background grid grid-cols-2 border-t px-4 py-3 sm:flex sm:justify-between sm:px-6 sm:py-4">
            <Button
              className="w-full sm:w-auto"
              disabled={submitting || creation.kind === "uncertain"}
              onClick={() => {
                if (state.phase === "review") {
                  publish({ kind: "idle" });
                  setState({ mode: state.mode, phase: "compose" });
                } else {
                  close();
                }
              }}
              type="button"
              variant="ghost"
            >
              {state.phase === "review" ? (
                <ChevronLeft className="size-4" />
              ) : null}
              {state.phase === "review" ? "Back" : "Cancel"}
            </Button>
            <Button
              className="w-full sm:w-auto"
              disabled={
                locked ||
                creation.kind === "terminal" ||
                (state.phase === "compose"
                  ? !canContinue(state)
                  : !isValidPlan(state.plan))
              }
              onClick={() => {
                void (state.phase === "compose"
                  ? continueFromCompose()
                  : create());
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
                        fileType:
                          plan.source.mimeType === MIME_DOC
                            ? "document"
                            : "presentation",
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
  catalogStatus: TemplateCatalogStatus;
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
        <TabsList
          aria-label="Starting point"
          className="grid w-full grid-cols-2"
        >
          <TabsTrigger
            disabled={
              disabled ||
              (catalogStatus.kind === "ready" && templates.length === 0)
            }
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
          {catalogStatus.kind === "ready" && templates.length === 0 ? (
            <p className="text-muted-foreground text-sm" role="status">
              This drive has no templates. Describe your file, or add a Doc or
              Slides file to a top-level Templates folder.
            </p>
          ) : null}
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
                onModeChange({
                  ...mode,
                  fileType: value === "presentation" ? value : "document",
                });
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
  catalogStatus: TemplateCatalogStatus;
  disabled: boolean;
  onRefresh: () => void;
  onSelect: (
    template: Awaited<ReturnType<typeof smartCreateApi.listTemplates>>[number],
  ) => void;
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
          disabled={disabled || catalogStatus.kind === "loading"}
          onClick={onRefresh}
          size="xs"
          type="button"
          variant="ghost"
        >
          <RefreshCw
            className={cn(
              "size-3.5",
              catalogStatus.kind === "loading" && "animate-spin",
            )}
          />
          Refresh
        </Button>
      </div>
      <ScrollArea className="h-[min(16rem,40dvh)] rounded-md border">
        <div className="space-y-1 p-1.5">
          {catalogStatus.kind === "loading" ? (
            <div className="text-muted-foreground flex h-24 items-center justify-center gap-2 text-sm">
              <Loader2 className="size-4 animate-spin" /> Looking in Templates…
            </div>
          ) : null}
          {catalogStatus.kind === "error" ? (
            <div
              className="text-muted-foreground flex h-24 items-center justify-center px-6 text-center text-sm"
              role="alert"
            >
              {catalogStatus.message}
            </div>
          ) : null}
          {catalogStatus.kind === "ready" && templates.length === 0 ? (
            <div className="text-muted-foreground flex h-36 flex-col items-center justify-center gap-2 px-6 text-center text-sm">
              <FolderOpen className="size-6" />
              <p>No templates available.</p>
              <p className="text-xs">
                Add a native Doc or Slides file to a root-level Templates
                folder.
              </p>
            </div>
          ) : null}
          {catalogStatus.kind !== "loading"
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
                  <span className="pt-0.5 text-lg">
                    {defaultEmoji(template.mimeType)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {template.name}
                    </span>
                    <span className="text-muted-foreground block truncate text-xs">
                      {[template.driveName, ...template.categoryPath].join(
                        " / ",
                      )}
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

function ReviewStep({
  onChange,
  onChooseDestination,
  plan,
  templates,
}: ReviewStepProps) {
  const templateFileId =
    plan.source.kind === "template" ? plan.source.templateFileId : undefined;
  const selectedTemplate = templates.find(
    (template) => template.fileId === templateFileId,
  );
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
        <Badge variant="secondary">
          {plan.source.kind === "blank" ? "Blank" : "Template"}
        </Badge>
      </div>
      <div className="grid gap-4 sm:grid-cols-[5rem_1fr]">
        <div className="space-y-1.5">
          <Label htmlFor="smart-emoji">Emoji</Label>
          <Input
            className="text-center text-xl"
            id="smart-emoji"
            maxLength={16}
            onChange={(event) => {
              onChange((current) => ({
                ...current,
                emoji: event.target.value,
              }));
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
              onChange((current) => ({
                ...current,
                alias: event.target.value,
              }));
            }}
            spellCheck={false}
            value={plan.alias}
          />
        </div>
        {documentAliasError(plan.alias) !== null ? (
          <p
            className="text-destructive text-xs"
            id="smart-alias-error"
            role="alert"
          >
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
            <span className="block truncate text-sm font-medium">
              {plan.destination.driveName}
            </span>
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

function canContinue(
  state: Extract<SmartCreateState, { phase: "compose" }>,
): boolean {
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
    ? {
        mode: { kind: "template", selection: { kind: "unselected" } },
        phase: "compose",
      }
    : {
        mode: {
          kind: "template",
          selection: { kind: "selected", template: current },
        },
        phase: "compose",
      };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : fallback;
}

function templatesInDrive(
  templates: DriveTemplate[],
  driveId: string,
): DriveTemplate[] {
  return templates.filter((template) => template.driveId === driveId);
}

function initialCreation(owner: string, scope: string): CreationState {
  try {
    const receipt = readSmartCreateReceipt(owner, scope);
    return receipt === null
      ? { kind: "idle" }
      : {
          kind: "uncertain",
          receipt,
          message:
            "An earlier file creation needs recovery. Check the original request before starting another.",
        };
  } catch {
    // Corrupt evidence must not be discarded or replaced with a new request.
    return {
      kind: "blocked",
      message:
        "The saved creation request is unreadable. Preserve it for recovery before creating another file.",
    };
  }
}

function publishCatalog(
  phase: RefObject<TemplateCatalogStatus>,
  setStatus: Dispatch<SetStateAction<TemplateCatalogStatus>>,
  next: TemplateCatalogStatus,
): void {
  phase.current = next;
  setStatus(next);
}

function creationActivity(
  state: CreationState,
): "editable" | "busy" | "recovery" | "terminal" | "unmounted" {
  switch (state.kind) {
    case "idle":
      return "editable";
    case "restoring":
    case "proposing":
    case "enqueuing":
    case "waiting":
    case "checking":
      return "busy";
    case "uncertain":
    case "blocked":
      return "recovery";
    case "terminal":
      return "terminal";
    case "unmounted":
      return "unmounted";
    default:
      return unreachableCreation(state);
  }
}

function unreachableCreation(state: never): never {
  throw new Error(`Unhandled creation state: ${JSON.stringify(state)}`);
}

function publishCreation(
  phase: RefObject<CreationState>,
  setCreation: Dispatch<SetStateAction<CreationState>>,
  next: CreationState,
): void {
  phase.current = next;
  setCreation(next);
}

function catalogTemplates(state: TemplateCatalogStatus): DriveTemplate[] {
  switch (state.kind) {
    case "ready":
      return state.templates;
    case "closed":
    case "loading":
    case "error":
      return [];
    default:
      return unreachableCreation(state);
  }
}
