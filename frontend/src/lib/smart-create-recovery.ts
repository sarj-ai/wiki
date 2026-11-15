import * as z from "zod";

const ReceiptSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("unconfirmed"), key: z.uuid() }).strict(),
  z
    .object({ kind: z.literal("admitted"), key: z.uuid(), taskId: z.uuid() })
    .strict(),
]);

export type SmartCreateReceipt = z.infer<typeof ReceiptSchema>;

export function readSmartCreateReceipt(
  owner: string,
  scope: string,
): SmartCreateReceipt | null {
  const raw = window.sessionStorage.getItem(storageKey(owner, scope));
  return raw === null ? null : ReceiptSchema.parse(JSON.parse(raw));
}

export function saveSmartCreateReceipt(
  owner: string,
  scope: string,
  receipt: SmartCreateReceipt,
): void {
  window.sessionStorage.setItem(
    storageKey(owner, scope),
    JSON.stringify(receipt),
  );
}

export function clearSmartCreateReceipt(
  owner: string,
  scope: string,
  key: string,
): void {
  if (readSmartCreateReceipt(owner, scope)?.key === key)
    window.sessionStorage.removeItem(storageKey(owner, scope));
}

function storageKey(owner: string, scope: string): string {
  return `sarj-wiki:smart-create:${encodeURIComponent(owner)}:${encodeURIComponent(scope)}`;
}
