import { store } from "./state.js?v=20261018l";
import { toast } from "./ui/toast.js?v=20261018l";

export function fetchOverview() {
  return fetch('/api/v5/overview', { cache: "no-store" });
}

export function fetchDemo() {
  return fetch("/static/demo/overview.json", { cache: "no-store" });
}

export async function mutate(method, path, body) {
  if (store.demo) {
    toast("Đây là dữ liệu mẫu, không ghi lên Pi.");
    throw Error("demo_readonly");
  }
  const options = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) options.body = JSON.stringify(body);
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.error || "request_failed");
  return data;
}

export function errorText(code) {
  if (code === "invalid_width") return "Độ rộng phải từ 0,1 đến 100 m";
  if (code === "demo_readonly") return "Đây là dữ liệu mẫu, không ghi lên Pi.";
  return code || "Không thực hiện được";
}
