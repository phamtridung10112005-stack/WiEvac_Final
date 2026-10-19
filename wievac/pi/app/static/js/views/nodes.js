import { errorText, mutate } from "../api.js?v=20261018l";
import { attentionKind, catalogList, finite, freshText, store, metres, shownPercent } from "../state.js?v=20261018l";
import { confirmAction } from "../ui/confirm.js?v=20261018l";
import { toast } from "../ui/toast.js?v=20261018l";
import { parseWidth } from "../ui/width.js?v=20261018l";

let editingId = null;
let filter = "all";

function matches(item) {
  const q = (document.querySelector("#node-search").value || "").trim().toLowerCase();
  const hay = [item.display_name, item.link_id, item.rx_id, item.corridor_id, item.node_id].join(" ").toLowerCase();
  if (q && !hay.includes(q)) return false;
  if (filter === "all") return true;
  const view = store.views[item.link_id] || {};
  return attentionKind(store.links[item.link_id] || {}, view.viewState) === filter;
}

export function renderNodes() {
  const body = document.querySelector("#node-table");
  if (!body) return;
  body.replaceChildren();
  catalogList().filter(matches).forEach(item => {
    const v = store.links[item.link_id] || {};
    const view = store.views[item.link_id] || { label: "Chưa xác định", hasPacket: false, stale: false, viewState: "UNKNOWN" };
    const tr = document.createElement("tr");
    const cells = [
      item.display_name || item.link_id,
      (item.rx_id || "") + " / " + item.link_id,
      item.corridor_id || "",
      finite(v.width_m) ? metres(v.width_m) : "Chưa nhập độ rộng",
      view.label,
      shownPercent(view) === null ? "--" : Math.round(shownPercent(view)) + "%",
      freshText(v, view),
      item.enabled === false ? "Tắt" : "Bật",
    ];
    cells.forEach(text => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    });
    const actions = document.createElement("td");
    actions.className = "row-actions";
    [["Sửa", "edit"], [item.enabled === false ? "Bật" : "Tắt", "toggle"], ["Xóa", "delete"]].forEach(([label, act]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn" + (act === "delete" ? " is-danger" : "");
      button.textContent = label;
      button.addEventListener("click", () => onAction(act, item));
      actions.appendChild(button);
    });
    tr.appendChild(actions);
    body.appendChild(tr);
  });
}

async function onAction(act, item) {
  if (act === "edit") { openForm(item); return; }
  if (act === "toggle") {
    mutate("PATCH", "/api/v5/nodes/" + encodeURIComponent(item.link_id), { enabled: item.enabled === false })
      .catch(err => toast(errorText(err.message)));
    return;
  }
  if (act === "delete") {
    const ok = await confirmAction("Xóa node " + (item.display_name || item.link_id) + "?");
    if (!ok) return;
    mutate("DELETE", "/api/v5/nodes/" + encodeURIComponent(item.link_id))
      .then(() => toast("Đã xóa node"))
      .catch(err => toast(errorText(err.message)));
  }
}

function openForm(item) {
  editingId = item ? item.link_id : null;
  const form = document.querySelector("#node-form");
  document.querySelector("#form-title").textContent = item ? "Sửa node" : "Thêm node";
  form.display_name.value = item?.display_name || "";
  form.link_id.value = item?.link_id || "";
  form.rx_id.value = item?.rx_id || "";
  form.node_id.value = item?.node_id || "";
  form.corridor_id.value = item?.corridor_id || "";
  form.nominal_width_m.value = item && item.nominal_width_m != null ? String(item.nominal_width_m) : "";
  form.link_id.disabled = !!item;
  form.rx_id.disabled = !!item;
  form.node_id.disabled = !!item;
  document.querySelector("#node-modal").hidden = false;
}

function closeForm() {
  document.querySelector("#node-modal").hidden = true;
  editingId = null;
}

export function initNodes() {
  document.querySelector("#node-search").addEventListener("input", renderNodes);
  document.querySelector("#node-filter").addEventListener("change", event => {
    filter = event.target.value;
    renderNodes();
  });
  document.querySelector("#node-add").addEventListener("click", () => openForm(null));
  document.querySelector("#form-cancel").addEventListener("click", closeForm);
  document.querySelector("#node-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    const widthParsed = parseWidth(form.nominal_width_m.value, true);
    if (widthParsed.error) { toast(widthParsed.error); return; }
    const payload = {
      display_name: form.display_name.value.trim(),
      link_id: form.link_id.value.trim(),
      rx_id: form.rx_id.value.trim(),
      node_id: form.node_id.value.trim() || undefined,
      corridor_id: form.corridor_id.value.trim() || undefined,
    };
    if (widthParsed.value !== null) payload.nominal_width_m = widthParsed.value;
    const req = editingId
      ? mutate("PATCH", "/api/v5/nodes/" + encodeURIComponent(editingId), {
        display_name: payload.display_name,
        corridor_id: payload.corridor_id,
        nominal_width_m: widthParsed.value,
      })
      : mutate("POST", "/api/v5/nodes", payload);
    req.then(() => { closeForm(); toast("Đã lưu node"); }).catch(err => toast(errorText(err.message)));
  });
}
