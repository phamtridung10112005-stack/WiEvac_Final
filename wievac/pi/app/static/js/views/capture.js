import { errorText, mutate } from "../api.js?v=20261018l";
import { finite, store } from "../state.js?v=20261018l";
import { toast } from "../ui/toast.js?v=20261018l";

function pad(n) { return String(n).padStart(2, "0"); }

function toLocalInput(date) {
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) + "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

function occupancyCaption(code) {
  return code === "EMPTY" ? "Hành lang trống" : code === "HUMAN_PRESENT" ? "Có người" : code === "UNKNOWN" ? "Không rõ" : "Chưa gắn nhãn";
}

function renderSchedules(items) {
  const host = document.querySelector("#schedule-list");
  if (!host) return;
  host.replaceChildren();
  (items || []).filter(item => item.phase !== "done").forEach(item => {
    const row = document.createElement("div");
    row.className = "schedule-row";
    const start = new Date(item.start_at);
    const end = new Date(item.end_at);
    const phase = item.phase === "active" ? "Đang thu" : "Chờ tới giờ";
    const text = document.createElement("span");
    text.textContent = phase + " · " + occupancyCaption(item.occupancy) + " · " + start.toLocaleString("vi-VN") + " → " + end.toLocaleString("vi-VN");
    const del = document.createElement("button");
    del.className = "btn";
    del.type = "button";
    del.textContent = "Xóa lịch";
    del.addEventListener("click", () => {
      mutate("POST", "/api/v5/recorder", { action: "unschedule", id: item.id })
        .then(() => toast("Đã xóa lịch"))
        .catch(err => toast(errorText(err.message)));
    });
    row.append(text, del);
    host.appendChild(row);
  });
}

export function renderCapture() {
  const rec = store.recording || {};
  const line = document.querySelector("#recording");
  const button = document.querySelector("#record-toggle");
  const active = rec.state === "RECORDING";
  if (button) button.textContent = active ? "Dừng ghi" : "Bắt đầu ghi";
  if (!line) return;
  if (active) {
    const files = rec.files ? Object.keys(rec.files).join(", ") : "";
    const remain = finite(rec.remaining_s)
      ? (Math.floor(Number(rec.remaining_s) / 3600) + " giờ " + Math.floor((Number(rec.remaining_s) % 3600) / 60) + " phút")
      : "đến khi bấm dừng";
    line.textContent = "Đang ghi " + (finite(rec.count) ? rec.count : 0) + " bản" + (rec.schedule_id ? " · theo lịch" : "") + " · còn " + remain + (files ? " · " + files : "");
  } else {
    line.textContent = "Chưa ghi — đặt lịch hoặc bấm Bắt đầu ghi";
  }
  renderSchedules(rec.schedules || []);
}

export function initCapture() {
  const start = new Date();
  start.setDate(start.getDate() + 1);
  start.setHours(1, 0, 0, 0);
  const end = new Date(start);
  end.setHours(5, 0, 0, 0);
  const startEl = document.querySelector("#capture-start");
  const endEl = document.querySelector("#capture-end");
  if (startEl && !startEl.value) startEl.value = toLocalInput(start);
  if (endEl && !endEl.value) endEl.value = toLocalInput(end);
  document.querySelector("#record-toggle").addEventListener("click", () => {
    const hours = Number(document.querySelector("#record-hours").value);
    const occupancy = document.querySelector("#capture-occupancy").value || "EMPTY";
    const active = (store.recording || {}).state === "RECORDING";
    const req = active
      ? mutate("POST", "/api/v5/recorder", { action: "stop" })
      : mutate("POST", "/api/v5/recorder", { action: "start", duration_hours: Number.isFinite(hours) ? hours : 8, occupancy });
    req.then(() => toast(active ? "Đã dừng ghi" : "Đã bắt đầu ghi")).catch(err => toast(errorText(err.message)));
  });
  document.querySelector("#capture-schedule").addEventListener("click", () => {
    const startValue = document.querySelector("#capture-start").value;
    const endValue = document.querySelector("#capture-end").value;
    if (!startValue || !endValue) { toast("Chọn ngày giờ bắt đầu và kết thúc"); return; }
    mutate("POST", "/api/v5/recorder", {
      action: "schedule",
      start_at: new Date(startValue).toISOString(),
      end_at: new Date(endValue).toISOString(),
      occupancy: document.querySelector("#capture-occupancy").value || "EMPTY",
    }).then(() => toast("Đã đặt lịch thu")).catch(err => toast(errorText(err.message)));
  });
}
