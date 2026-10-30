import type { ActionErrorCode } from "@/lib/action-result";

export const RATE_LIMIT_MESSAGE = "Too many requests. Try again in a minute.";
export const SESSION_REFRESH_UNAVAILABLE_MESSAGE =
  "Your session could not be refreshed. Try again in a moment.";

export class ApiError extends Error {
  readonly code: ActionErrorCode | undefined;
  readonly status: number;

  constructor(status: number, message: string, code?: ActionErrorCode) {
    super(message);
    this.code = code;
    this.status = status;
    this.name = "ApiError";
  }
}
