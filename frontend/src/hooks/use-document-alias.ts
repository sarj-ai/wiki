"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { retireDocumentAliasAction, saveDocumentAliasAction } from "@/app/actions/wiki-actions";
import { runAction } from "@/lib/action-client";
import { wikiApi } from "@/lib/wiki-api";

const aliasKey = (fileId: string) => ["document-alias", fileId] as const;

export function useDocumentAlias(fileId: string, enabled: boolean) {
  return useQuery({
    enabled,
    queryFn: async () => wikiApi.getDocumentAlias(fileId),
    queryKey: aliasKey(fileId),
  });
}

export function useSaveDocumentAlias(fileId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (alias: string) =>
      await runAction(async () => await saveDocumentAliasAction(fileId, alias)),
    onSuccess: (result) => {
      queryClient.setQueryData(aliasKey(fileId), { alias: result.alias });
    },
  });
}

export function useRetireDocumentAlias(fileId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => await runAction(async () => await retireDocumentAliasAction(fileId)),
    onSuccess: () => {
      queryClient.setQueryData(aliasKey(fileId), { alias: undefined });
    },
  });
}
