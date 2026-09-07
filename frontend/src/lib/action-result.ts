export type ActionErrorCode = "document_transform_source_changed";

export type ActionResult<T> =
  | { data: T; ok: true; replayed?: boolean }
  | { code?: ActionErrorCode; error: string; ok: false; status: number };
