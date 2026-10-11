import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { captureMutationSession, retireMutationSession } from "@/lib/mutation-session";

import { useSessionMutation } from "./use-session-mutation";

interface Input {
  value: number;
}

interface Context {
  previous: number;
}

describe("owned mutation public result and callbacks", () => {
  let root: Root;
  let client: QueryClient;
  let mutation: ReturnType<typeof useSessionMutation<number, Input, Context>>;
  const work = vi.fn<(input: Input) => Promise<number>>();
  const success = vi.fn<(data: number, input: Input, result: Context) => void>();
  const settled = vi.fn();

  function Probe() {
    mutation = useSessionMutation({
      mutationFn: async (input: Input) => await work(input),
      onMutate: ({ value }) => ({ previous: value }),
      onSettled: settled,
      onSuccess: success,
    });
    return null;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    client = new QueryClient({ defaultOptions: { mutations: { gcTime: Infinity, retry: false } } });
    work.mockReset();
    success.mockClear();
    settled.mockClear();
    root = createRoot(document.createElement("div"));
  });

  function render() {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Probe />
        </QueryClientProvider>,
      ),
    );
  }

  afterEach(async () => {
    flushSync(() => root.unmount());
    await vi.advanceTimersByTimeAsync(0);
    client.clear();
    vi.useRealTimers();
  });

  it("preserves variables, data, context and per-call callbacks in a standalone client", async () => {
    render();
    work.mockResolvedValue(12);
    const input = { value: 6 };
    const onSuccess = vi.fn();
    const onSettled = vi.fn();
    await expect(mutation.mutateAsync(input, { onSettled, onSuccess })).resolves.toBe(12);
    await vi.advanceTimersByTimeAsync(0);

    expect(mutation.variables).toBe(input);
    expect(mutation.data).toBe(12);
    expect(success).toHaveBeenCalledWith(
      12,
      input,
      { previous: 6 },
      expect.objectContaining({ client }),
    );
    expect(onSuccess).toHaveBeenCalledWith(
      12,
      input,
      { previous: 6 },
      expect.objectContaining({ client }),
    );
    expect(onSettled).toHaveBeenCalledWith(
      12,
      null,
      input,
      { previous: 6 },
      expect.objectContaining({ client }),
    );
    expect(captureMutationSession(client).aborted).toBe(false);
  });

  it("allows admitted work to finish while retiring its result and callbacks", async () => {
    render();
    const pending = Promise.withResolvers<number>();
    const completed = vi.fn();
    work.mockImplementation(async () => {
      const result = await pending.promise;
      completed();
      return result;
    });
    const onSuccess = vi.fn();
    const onSettled = vi.fn();
    const operation = mutation
      .mutateAsync({ value: 4 }, { onSettled, onSuccess })
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(work).toHaveBeenCalledOnce();
    retireMutationSession(client);
    pending.resolve(8);

    await expect(operation).resolves.toMatchObject({ name: "AbortError" });
    expect(completed).toHaveBeenCalledOnce();
    expect(success).not.toHaveBeenCalled();
    expect(settled).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("converts a retired asynchronous admission error to quiet cancellation", async () => {
    render();
    const admission = Promise.withResolvers<undefined>();
    client.getMutationCache().config.onMutate = async () => await admission.promise;
    const operation = mutation.mutateAsync({ value: 4 }).catch((error: unknown) => error);
    retireMutationSession(client);
    admission.reject(new Error("Old admission failed"));

    await expect(operation).resolves.toMatchObject({ name: "AbortError" });
    expect(work).not.toHaveBeenCalled();
    expect(settled).not.toHaveBeenCalled();
  });
});
