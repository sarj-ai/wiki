"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { createContext, useContext, useEffect, useRef, useState } from "react";

import { FocusReturnProvider } from "@/components/ui/focus-return";
import { useSidebar } from "@/components/ui/sidebar";
import { ApiError } from "@/lib/api-error";
import { captureMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveSummary, DriveTree, FileDetail } from "@/types/wiki";

import type { ItemMenuProps } from "./item-menu";

const MoveFileDialog = dynamic(
  async () => {
    const loadedModule = await import("./move-file-dialog");
    return loadedModule.MoveFileDialog;
  },
  { ssr: false },
);

type OpenerRef = React.RefObject<HTMLButtonElement | null>;

interface MoveSelection {
  item: ItemMenuProps;
  open: boolean;
  opener: OpenerRef;
  session: AbortSignal;
  token: symbol;
}

const MoveDialogContext = createContext<{
  hasActiveScope: (item: ItemMenuProps) => boolean;
  isOpenFor: (item: ItemMenuProps) => boolean;
  observeItem: (item: ItemMenuProps, opener: OpenerRef) => void;
  openItem: (item: ItemMenuProps, opener: OpenerRef) => void;
} | null>(null);

export function useMoveDialogOutlet() {
  return useContext(MoveDialogContext);
}

export function MoveDialogOutletProvider({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: null | string;
}) {
  return (
    <MoveDialogOwner key={userId ?? ""} userId={userId}>
      {children}
    </MoveDialogOwner>
  );
}

function MoveDialogOwner({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: null | string;
}) {
  const queryClient = useQueryClient();
  const { mobileTriggerRef } = useSidebar();
  const [move, setMove] = useState<MoveSelection | null>(null);
  const contentRef = useRef<{ content: HTMLElement | null; token: symbol } | null>(null);
  const closeTimer = useRef<null | number>(null);

  useEffect(
    () => () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    },
    [],
  );

  useEffect(() => {
    if (!move) return;
    const retire = () => setMove((current) => (current?.token === move.token ? null : current));
    if (move.session.aborted) {
      retire();
      return;
    }
    move.session.addEventListener("abort", retire);
    const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== "updated") return;
      const rawKey: unknown = event.query.queryKey;
      if (!Array.isArray(rawKey)) return;
      const key: readonly unknown[] = rawKey;
      const denied =
        event.action.type === "error" &&
        sameKey(key, wikiKeys.file(move.item.fileId)) &&
        event.action.error instanceof ApiError &&
        (event.action.error.status === 403 || event.action.error.status === 404);
      const changed =
        event.action.type === "success" && !sourceStillMatches(queryClient, move.item, key);
      if (denied || changed) retire();
    });
    return () => {
      move.session.removeEventListener("abort", retire);
      unsubscribe();
    };
  }, [move, queryClient]);

  const currentMove = move?.session.aborted ? null : move;
  return (
    <MoveDialogContext
      value={{
        hasActiveScope: (item) =>
          !!currentMove &&
          sameItem(currentMove.item, item) &&
          contentRef.current?.token === currentMove.token &&
          !!contentRef.current.content?.isConnected,
        isOpenFor: (item) => !!currentMove?.open && sameItem(currentMove.item, item),
        observeItem: (item, opener) => {
          setMove((current) =>
            current?.opener === opener && !sameItem(current.item, item) ? null : current,
          );
        },
        openItem: (item, opener) => {
          const session = captureMutationSession(queryClient);
          if (!userId || session.aborted) return;
          setMove({
            item: { ...item },
            open: true,
            opener,
            session,
            token: Symbol(),
          });
        },
      }}
    >
      {children}
      {currentMove ? (
        <FocusReturnProvider fallbackTargetRef={mobileTriggerRef} targetRef={currentMove.opener}>
          <MoveFileDialog
            currentDriveId={currentMove.item.driveId}
            currentParentFolderId={currentMove.item.parentFolderId}
            fileId={currentMove.item.fileId}
            fileName={currentMove.item.currentName}
            key={JSON.stringify([
              currentMove.item.fileId,
              currentMove.item.driveId,
              currentMove.item.parentFolderId,
            ])}
            onCloseAutoFocus={(event) => {
              if (
                contentRef.current?.token !== currentMove.token ||
                event.target !== contentRef.current.content
              )
                return;
              contentRef.current = null;
              if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
              // Radix emits this after the real exit animation. Keep the
              // focus owner alive through the shared return-focus microtask.
              closeTimer.current = window.setTimeout(() => {
                closeTimer.current = null;
                setMove((current) =>
                  current?.token === currentMove.token && !current.open ? null : current,
                );
              }, 0);
            }}
            onOpenAutoFocus={(event) => {
              contentRef.current = {
                content: event.target instanceof HTMLElement ? event.target : null,
                token: currentMove.token,
              };
            }}
            onOpenChange={(open) => {
              setMove((current) =>
                current?.token === currentMove.token ? { ...current, open } : current,
              );
            }}
            open={currentMove.open}
          />
        </FocusReturnProvider>
      ) : null}
    </MoveDialogContext>
  );
}

function sameItem(left: ItemMenuProps, right: ItemMenuProps): boolean {
  return (
    left.fileId === right.fileId &&
    left.currentName === right.currentName &&
    left.driveId === right.driveId &&
    left.parentFolderId === right.parentFolderId &&
    left.isFolder === right.isFolder
  );
}

function sameKey(left: readonly unknown[], right: readonly unknown[]): boolean {
  return left.length === right.length && left.every((part, index) => part === right[index]);
}

function sourceStillMatches(
  queryClient: QueryClient,
  item: ItemMenuProps,
  key: readonly unknown[],
): boolean {
  if (sameKey(key, wikiKeys.file(item.fileId))) {
    return fileStillMatches(queryClient, item);
  }
  if (sameKey(key, wikiKeys.drives)) {
    const drives = queryClient.getQueryData<DriveSummary[]>(wikiKeys.drives);
    return !item.driveId || !drives || drives.some((drive) => drive.id === item.driveId);
  }
  if (item.driveId && sameKey(key, wikiKeys.driveTree(item.driveId))) {
    const tree = queryClient.getQueryData<DriveTree>(wikiKeys.driveTree(item.driveId));
    if (!tree || (!item.isFolder && !tree.filesComplete)) return true;
    const node = (item.isFolder ? tree.folders : tree.files).find(
      (entry) => entry.id === item.fileId,
    );
    return (
      !!node &&
      node.name === item.currentName &&
      (node.parentId === item.driveId ? undefined : node.parentId) === item.parentFolderId
    );
  }
  const container = item.parentFolderId ?? item.driveId;
  if (container && !item.isFolder && sameKey(key, wikiKeys.folderFiles(container))) {
    const files = queryClient.getQueryData<DriveNode[]>(wikiKeys.folderFiles(container));
    return (
      !files || files.some((file) => file.id === item.fileId && file.name === item.currentName)
    );
  }
  return true;
}

function fileStillMatches(queryClient: QueryClient, item: ItemMenuProps): boolean {
  const file = queryClient.getQueryData<FileDetail>(wikiKeys.file(item.fileId));
  if (!file) return true;
  if (file.id !== item.fileId || file.name !== item.currentName) return false;
  if (file.driveId && item.driveId && file.driveId !== item.driveId) return false;
  if (!file.parentFolderId) return true;
  const driveId = file.driveId ?? item.driveId;
  const personal =
    queryClient
      .getQueryData<DriveSummary[]>(wikiKeys.drives)
      ?.some((drive) => drive.id === driveId && drive.isPersonal) ?? false;
  return (
    normalizedParent(file.parentFolderId, driveId, personal) ===
    normalizedParent(item.parentFolderId, item.driveId, personal)
  );
}

function normalizedParent(
  parentId: string | undefined,
  driveId: string | undefined,
  personal: boolean,
) {
  return parentId === driveId || (personal && parentId === "root") ? undefined : parentId;
}
