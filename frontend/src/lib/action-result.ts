export type ActionResult<T> = { data: T; ok: true } | { error: string; ok: false; status: number };
