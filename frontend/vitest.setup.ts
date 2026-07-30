import "@testing-library/jest-dom/vitest";

import { vi } from "vitest";

import { cookieState } from "@/test-stubs/cookie-state";

vi.mock("next/headers", () => ({
  cookies: async () =>
    Promise.resolve({
      get: (name: string) =>
        name === "access_token" && cookieState.token !== undefined
          ? { name, value: cookieState.token }
          : undefined,
    }),
}));
