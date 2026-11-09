"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  retireDocumentAliasAction,
  saveDocumentAliasAction,
} from "@/app/actions/wiki-actions";
import { useSessionMutation } from "@/hooks/use-session-mutation";
import { runAction } from "@/lib/action-client";
import { WIKI_READ_API } from "@/lib/wiki-read-api";

const documentAliasKeys = {
  all: ["document-alias"] as const,
  file: (fileId: string) => ["document-alias", fileId] as const,
} as const;

export const documentAliasKey = documentAliasKeys.file;

export function useDocumentAlias(fileId: string, enabled: boolean) {
  return useQuery({
    enabled,
    queryFn: async ({ signal }) =>
      await WIKI_READ_API.getDocumentAlias(fileId, signal),
    queryKey: documentAliasKey(fileId),
  });
}

export function useSaveDocumentAlias(fileId: string) {
  const queryClient = useQueryClient();
  return useSessionMutation({
    mutationFn: async (alias: string, _context, session) =>
      await runAction(
        async () => await saveDocumentAliasAction(fileId, alias),
        session,
      ),
    onSuccess: (result) => {
      queryClient.setQueryData(documentAliasKey(fileId), {
        alias: result.alias,
      });
    },
  });
}

export function useRetireDocumentAlias(fileId: string) {
  const queryClient = useQueryClient();
  return useSessionMutation({
    mutationFn: async (_input, _context, session) =>
      await runAction(
        async () => await retireDocumentAliasAction(fileId),
        session,
      ),
    onSuccess: () => {
      queryClient.setQueryData(documentAliasKey(fileId), { alias: undefined });
    },
  });
}
