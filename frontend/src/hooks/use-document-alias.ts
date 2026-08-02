"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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
    mutationFn: async (alias: string) => wikiApi.saveDocumentAlias(fileId, alias),
    onSuccess: (result) => {
      queryClient.setQueryData(aliasKey(fileId), { alias: result.alias });
    },
  });
}

export function useRetireDocumentAlias(fileId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => wikiApi.retireDocumentAlias(fileId),
    onSuccess: () => {
      queryClient.setQueryData(aliasKey(fileId), { alias: undefined });
    },
  });
}
