"use client";

import {
  type MutateOptions,
  type MutationFunctionContext,
  useMutation,
  type UseMutationOptions,
  useQueryClient,
} from "@tanstack/react-query";

import { captureMutationSession } from "@/lib/mutation-session";

interface OwnedVariables<TVariables> {
  input: TVariables;
  session: AbortSignal;
}

export function useSessionMutation<TData, TVariables = void, TContext = unknown>(
  options: Omit<UseMutationOptions<TData, Error, TVariables, TContext>, "mutationFn"> & {
    mutationFn: (
      input: TVariables,
      context: MutationFunctionContext,
      session: AbortSignal,
    ) => Promise<TData>;
  },
) {
  const queryClient = useQueryClient();
  const { onMutate, ...otherOptions } = options;
  const ownedOptions: UseMutationOptions<TData, Error, OwnedVariables<TVariables>, TContext> = {
    ...otherOptions,
    mutationFn: async ({ input, session }, context) => {
      session.throwIfAborted();
      const result = await options.mutationFn(input, context, session);
      session.throwIfAborted();
      return result;
    },
    onError: (error, { input, session }, result, context) =>
      session.aborted ? undefined : options.onError?.(error, input, result, context),
    onSettled: (data, error, { input, session }, result, context) =>
      session.aborted ? undefined : options.onSettled?.(data, error, input, result, context),
    onSuccess: (data, { input, session }, result, context) =>
      session.aborted ? undefined : options.onSuccess?.(data, input, result, context),
  };
  if (onMutate) {
    ownedOptions.onMutate = async ({ input, session }, context) => {
      session.throwIfAborted();
      const result = await onMutate(input, context);
      session.throwIfAborted();
      return result;
    };
  }
  const mutation = useMutation(ownedOptions);

  return {
    ...mutation,
    mutate: (input: TVariables, callbacks?: MutateOptions<TData, Error, TVariables, TContext>) => {
      // TanStack can await MutationCache.onMutate before calling this hook's
      // callbacks or mutationFn. Bind ownership before it queues that work.
      const session = captureMutationSession(queryClient);
      mutation.mutate({ input, session }, ownedCallbacks(callbacks));
    },
    mutateAsync: async (
      input: TVariables,
      callbacks?: MutateOptions<TData, Error, TVariables, TContext>,
    ) => {
      const session = captureMutationSession(queryClient);
      try {
        const result = await mutation.mutateAsync({ input, session }, ownedCallbacks(callbacks));
        session.throwIfAborted();
        return result;
      } catch (error: unknown) {
        session.throwIfAborted();
        throw error;
      }
    },
    variables: mutation.variables?.input,
  };
}

function ownedCallbacks<TData, TVariables, TContext>(
  callbacks: MutateOptions<TData, Error, TVariables, TContext> | undefined,
): MutateOptions<TData, Error, OwnedVariables<TVariables>, TContext> | undefined {
  if (!callbacks) return undefined;
  return {
    onError: (error, { input, session }, result, context) => {
      if (!session.aborted) callbacks.onError?.(error, input, result, context);
    },
    onSettled: (data, error, { input, session }, result, context) => {
      if (!session.aborted) callbacks.onSettled?.(data, error, input, result, context);
    },
    onSuccess: (data, { input, session }, result, context) => {
      if (!session.aborted) callbacks.onSuccess?.(data, input, result, context);
    },
  };
}
