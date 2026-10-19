export function parseWidth(raw, allowEmpty) {
  const text = String(raw ?? "").trim().replace(",", ".");
  if (!text) return allowEmpty ? { value: null } : { error: "Nhập số lớn hơn 0" };
  if (!/^\d+(\.\d+)?$/.test(text)) return { error: "Nhập số lớn hơn 0" };
  const number = Number(text);
  if (!Number.isFinite(number) || number <= 0) return { error: "Nhập số lớn hơn 0" };
  return { value: number };
}
