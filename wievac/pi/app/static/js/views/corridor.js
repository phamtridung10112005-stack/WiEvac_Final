import { errorText, mutate } from "../api.js?v=20261018l";
import { icon } from "../icons.js?v=20261018l";
import { aiLabel, catalogName, finite, freshText, history, metresOf, pointsOf, shownPercent, pushEvent, reasonText, routeText, store, toneFor, widthPercent } from "../state.js?v=20261018l";
import { drawSeries, seriesValueAt } from "../ui/sparkline.js?v=20261018l";
import { toast } from "../ui/toast.js?v=20261018l";
import { parseWidth } from "../ui/width.js?v=20261018l";

let bound = false;

function pct(value) {
  return finite(value) ? (Number(value) * 100).toFixed(1) + "%" : "--";
}

function paintGuidance(el, sync, id) {
  if (!el) return;
  const state = sync && sync.state || "";
  const key = [state, sync && sync.map_width_m, (sync && sync.edges || []).join(","), sync && sync.multi_link_width_conflict].join("|");
  if (el.dataset.syncKey === key) return;
  el.dataset.syncKey = key;
  el.className = "sync" + (state === "MISMATCH" ? " is-warn" : "");
  el.replaceChildren();
  const text = document.createElement("span");
  if (state === "SYNCED") {
    const edges = (sync.edges || []).filter(Boolean).join(", ");
    text.textContent = edges ? "Điều hướng: đã đồng bộ (cạnh " + edges + ")" : "Điều hướng: đã đồng bộ";
  } else if (state === "MISMATCH") {
    text.textContent = "Bản đồ điều hướng đang dùng " + (finite(sync.map_width_m) ? sync.map_width_m + " m" : "--");
  } else if (state === "NOT_MAPPED") {
    text.textContent = "Link chưa gán vào cạnh nào trên bản đồ điều hướng (csiLinkId) — số liệu chỉ hiển thị, chưa ảnh hưởng chỉ đường";
  } else if (state === "MAP_UNAVAILABLE") {
    text.textContent = "Không đọc được bản đồ điều hướng";
  }
  if (text.textContent) el.appendChild(text);
  if (sync && sync.multi_link_width_conflict) {
    const warn = document.createElement("span");
    warn.textContent = " Cạnh gán nhiều link có độ rộng khác nhau — điều hướng dùng độ rộng nhỏ hơn.";
    el.appendChild(warn);
  }
  if (state === "MISMATCH") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.textContent = "Đồng bộ lại";
    button.addEventListener("click", () => {
      mutate("POST", "/api/v5/nodes/" + encodeURIComponent(id) + "/sync-width", {})
        .then(() => toast("Đã gửi đồng bộ độ rộng"))
        .catch(err => toast(errorText(err.message)));
    });
    el.appendChild(button);
  }
}

function paintLabels(id) {
  const current = (store.labels[id] || {}).occupancy || "";
  document.querySelectorAll("#view-corridor [data-occupancy]").forEach(button => {
    button.classList.toggle("is-on", button.dataset.occupancy === current);
  });
  const caption = document.querySelector("[data-label-current]");
  const name = current === "EMPTY" ? "Trống" : current === "HUMAN_PRESENT" ? "Có người" : current === "UNKNOWN" ? "Không rõ" : "Chưa gắn nhãn";
  if (caption) caption.textContent = "Nhãn: " + name;
}

export function renderCorridor(focusWidth) {
  const id = store.selectedId;
  const viewRoot = document.querySelector("#view-corridor");
  const empty = document.querySelector("#corridor-empty");
  const body = document.querySelector("#corridor-body");
  if (!id || !store.catalog[id]) {
    if (empty) empty.hidden = false;
    if (body) body.hidden = true;
    return;
  }
  if (empty) empty.hidden = true;
  if (body) body.hidden = false;
  const item = store.catalog[id];
  const v = store.links[id] || {};
  const view = store.views[id] || { label: "Chưa xác định", hasPacket: false, stale: false, viewState: "UNKNOWN" };
  const tone = toneFor(view);
  document.querySelector("#corridor-title").textContent = item.display_name || id;
  document.querySelector("#corridor-sub").textContent = (item.rx_id || "") + " · " + id;
  const badge = document.querySelector("#corridor-badge");
  badge.className = "badge is-" + tone;
  badge.querySelector("[data-state-icon]").innerHTML = tone === "ok" ? icon.ok : tone === "warn" ? icon.warn : tone === "bad" ? icon.bad : tone === "stale" ? icon.stale : icon.idle;
  document.querySelector("#corridor-badge-label").textContent = view.label;
  const widthM = finite(v.width_m) ? Number(v.width_m) : null;
  const flowPct = shownPercent(view);
  const capPct = widthPercent(v);
  document.querySelector("#corridor-flow").textContent = flowPct === null ? "--" : String(Math.round(flowPct));
  document.querySelector("#corridor-max").textContent = widthM === null ? "Chưa nhập độ rộng" : metresOf(pointsOf(flowPct, widthM), widthM) + " còn đi được";
  {
    const reasonEl = document.querySelector("#corridor-reason");
    reasonEl.textContent = reasonText(v, { withAi: false });
    reasonEl.hidden = !reasonEl.textContent;
  }
  document.querySelector("#corridor-ai").textContent = aiLabel(v) + (v.model_version ? " · " + v.model_version : "");
  document.querySelector("#corridor-fresh").textContent = freshText(v, view);
  document.querySelector("#corridor-route").textContent = routeText(id, v, view);
  const capLine = document.querySelector("#corridor-cap");
  if (capLine) {
    const capText = capPct === null ? "--" : Math.round(capPct) + "%";
    capLine.textContent = "Độ rộng còn lại " + capText + (widthM === null || capPct === null ? "" : " · " + metresOf(v.width_points, widthM).replace("≈ ", ""));
  }
  const points = history[id] || [];
  drawSeries(document.querySelector("#chart-flow"), points.slice(-600), "v");
  drawSeries(document.querySelector("#chart-width"), points.slice(-600), "w");
  const input = document.querySelector("[data-width-input]");
  if (input && document.activeElement !== input) input.value = widthM === null ? "" : String(widthM);
  const row = document.querySelector("[data-width-row]");
  if (row) row.classList.toggle("is-missing", widthM === null);
  paintGuidance(document.querySelector("[data-guidance]"), v.guidance_sync, id);
  paintLabels(id);
  const q = store.quality || {};
  const set = (sel, text) => {
    const el = document.querySelector(sel);
    if (el) el.textContent = text;
  };
  set("[data-stability]", finite(v.quality) ? Number(v.quality).toFixed(1) : "--");
  set("[data-udp-loss]", finite(v.udp_loss_ratio) ? pct(v.udp_loss_ratio) : (v.udp_loss_status ? "CHƯA ĐO" : "--"));
  set("[data-gap]", finite(v.csi_gap_ratio) ? pct(v.csi_gap_ratio) : "--");
  set("[data-gap-mean]", finite(q.csi_gap_mean) ? pct(q.csi_gap_mean) : "--");
  set("[data-queue-drop]", finite(v.queue_drop_ratio) ? pct(v.queue_drop_ratio) : "--");
  set("[data-invalid]", finite(v.invalid_csi_ratio) ? pct(v.invalid_csi_ratio) : "--");
  set("[data-jitter]", finite(v.jitter_ms) ? Number(v.jitter_ms).toFixed(1) + " ms" : "--");
  set("[data-sequence]", finite(v.sequence_gap) ? String(v.sequence_gap) : "--");
  set("[data-queue-count]", finite(v.queue_drop_count) ? String(v.queue_drop_count) : "--");
  set("[data-invalid-count]", finite(v.invalid_count) ? String(v.invalid_count) : "--");
  set("[data-boot]", v.boot_id != null ? String(v.boot_id) : "--");
  const pi = store.pi || {};
  set("[data-pi]", [pi.received, pi.accepted, pi.rejected].every(finite) ? [pi.received, pi.accepted, pi.rejected].join("/") : "--");
  if (focusWidth && input) input.focus();
}

function bindCorridor() {
  if (bound) return;
  bound = true;
  document.querySelector("#width-save").addEventListener("click", () => {
    const id = store.selectedId;
    const input = document.querySelector("[data-width-input]");
    const error = document.querySelector("[data-width-error]");
    const parsed = parseWidth(input.value, false);
    if (parsed.error) { error.textContent = parsed.error; return; }
    error.textContent = "";
    mutate("PATCH", "/api/v5/nodes/" + encodeURIComponent(id), { nominal_width_m: parsed.value })
      .then(() => {
        pushEvent(id, catalogName(id), "Đã lưu độ rộng " + parsed.value + " m");
        toast("Đã lưu độ rộng");
      })
      .catch(err => { error.textContent = errorText(err.message); });
  });
  document.querySelector("#width-clear").addEventListener("click", () => {
    const id = store.selectedId;
    const error = document.querySelector("[data-width-error]");
    error.textContent = "";
    mutate("PATCH", "/api/v5/nodes/" + encodeURIComponent(id), { nominal_width_m: null })
      .then(() => toast("Đã xóa độ rộng"))
      .catch(err => { error.textContent = errorText(err.message); });
  });
  document.querySelector("#corridor-labels").addEventListener("click", event => {
    const button = event.target.closest("[data-occupancy]");
    if (!button || !store.selectedId) return;
    mutate("POST", "/api/v5/labels", { link_id: store.selectedId, occupancy: button.dataset.occupancy })
      .then(data => {
        store.labels = data.active_labels || store.labels;
        paintLabels(store.selectedId);
      })
      .catch(err => toast(errorText(err.message)));
  });
  const chart = document.querySelector("#chart-flow");
  const tip = document.querySelector("#chart-tip");
  if (chart && tip) {
    chart.addEventListener("mousemove", event => {
      const rect = chart.getBoundingClientRect();
      const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0;
      const point = seriesValueAt(history[store.selectedId] || [], "v", ratio);
      if (!point) { tip.hidden = true; return; }
      tip.hidden = false;
      tip.textContent = "Lưu thông " + Math.round(point.v) + "%";
      tip.style.setProperty("--x", (event.clientX - rect.left) + "px");
      tip.style.setProperty("--y", (event.clientY - rect.top) + "px");
    });
    chart.addEventListener("mouseleave", () => { tip.hidden = true; });
  }
}

export function initCorridor() {
  bindCorridor();
}
