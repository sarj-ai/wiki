export class ServerApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Server API error: ${status}`);
    this.status = status;
    this.name = "ServerApiError";
  }
}
