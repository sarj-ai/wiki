export type ActionErrorCode = "document_transform_source_changed";

export type ActionResult<T> =
  | { code?: ActionErrorCode; error: string; ok: false; status: number }
  | { data: T; ok: true; replayed?: boolean };
