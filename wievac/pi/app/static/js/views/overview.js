import { fillCard, ensureCards } from "../ui/card.js?v=20261018l";
import { catalogList, catalogName, eventRevision, events, finite, metresOf, pointsOf, shownPercent, store } from "../state.js?v=20261018l";

let paintedEvents = -1;

function rank(item) {
  const view = store.views[item.link_id];
  const row = store.links[item.link_id] || {};
  if (!view) return 6;
  if (view.stale || view.viewState === "STALE") return 0;
  if (!view.hasPacket) return 1;
  if (view.viewState === "BLOCKED") return 2;
  if (view.viewState === "UNSTABLE") return 3;
  if (view.viewState === "DEGRADED") return 4;
  if (!finite(row.width_m)) return 5;
  return 6;
}

function wallList() {
  return catalogList()
    .filter(item => item.enabled !== false)
    .sort((a, b) => rank(a) - rank(b) || (a.sort_order || 0) - (b.sort_order || 0) || String(a.link_id).localeCompare(String(b.link_id)));
}

function renderStats(list) {
  const tally = { ok: 0, warn: 0, bad: 0, stale: 0 };
  let waiting = 0;
  list.forEach(item => {
    const view = store.views[item.link_id] || {};
    // A node that never sent anything is not a lost connection.
    if (!view.hasPacket) waiting += 1;
    else if (view.stale) tally.stale += 1;
    else if (view.viewState === "BLOCKED") tally.bad += 1;
    else if (view.viewState === "DEGRADED") tally.warn += 1;
    else if (view.viewState === "PASSABLE" && !view.settling) tally.ok += 1;
  });
  Object.entries(tally).forEach(([key, count]) => {
    const el = document.querySelector('[data-count="' + key + '"]');
    if (el) el.textContent = String(count);
    const box = document.querySelector('[data-stat="' + key + '"]');
    if (box) box.classList.toggle("is-zero", count === 0);
  });
  let flow = 0;
  let max = 0;
  let missing = 0;
  list.forEach(item => {
    const row = store.links[item.link_id] || {};
    if (!finite(row.width_m)) {
      missing += 1;
      return;
    }
    if (finite(row.max_points)) max += Number(row.max_points);
    const held = pointsOf(shownPercent(store.views[item.link_id]), row.width_m);
    if (held !== null) flow += held;
  });
  const flowEl = document.querySelector("[data-flow-sum]");
  const maxEl = document.querySelector("[data-max-sum]");
  if (flowEl) flowEl.textContent = max > 0 ? Math.round(flow / max * 100) + "%" : "--";
  if (maxEl) maxEl.textContent = max > 0 ? metresOf(flow, max / 1000) : "";
  const bar = document.querySelector("[data-capacity-bar]");
  if (bar) bar.style.setProperty("--w", (max > 0 ? Math.max(0, Math.min(100, flow / max * 100)) : 0) + "%");
  const note = document.querySelector("[data-missing-width]");
  if (note) {
    const parts = [missing ? missing + " hành lang chưa nhập độ rộng" : "Đã có độ rộng cho mọi hành lang đang bật"];
    if (waiting) parts.push(waiting + " node chưa gửi dữ liệu");
    note.textContent = parts.join(" · ");
  }
}

function renderEvents() {
  const rev = eventRevision();
  if (rev === paintedEvents) return;
  paintedEvents = rev;
  const host = document.querySelector("#events");
  if (!host) return;
  host.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Chưa có sự kiện";
    host.appendChild(empty);
    return;
  }
  events.slice(0, 12).forEach(item => {
    const row = document.createElement("article");
    row.className = "event";
    const time = document.createElement("time");
    time.textContent = new Date(item.t).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const text = document.createElement("p");
    text.textContent = (item.title || catalogName(item.linkId)) + " · " + item.detail;
    row.append(time, text);
    host.appendChild(row);
  });
}

export function renderOverview(onOpen) {
  const list = wallList();
  renderStats(list);
  const host = document.querySelector("#cards");
  ensureCards(host, list, onOpen);
  list.forEach(item => {
    const card = host.querySelector('[data-link-id="' + CSS.escape(item.link_id) + '"]');
    if (!card) return;
    fillCard(card, item, store.links[item.link_id] || {}, store.views[item.link_id]);
  });
  renderEvents();
}

export function renderSystem() {
  const el = document.querySelector("#system");
  const dot = document.querySelector("#system-dot");
  if (!store.connected) {
    if (el) el.textContent = "Mất kết nối";
    if (dot) dot.className = "dot is-stale";
    return;
  }
  const live = wallList().map(item => store.views[item.link_id]).filter(Boolean);
  const reporting = live.filter(view => view.hasPacket);
  const hard = reporting.some(view => view.viewState === "BLOCKED" || view.stale);
  const soft = reporting.some(view => view.viewState === "DEGRADED" || view.viewState === "UNSTABLE");
  if (el) el.textContent = !reporting.length ? "Đang chờ dữ liệu" : (hard ? "Có cảnh báo" : (soft ? "Giảm lưu thông" : "Hoạt động bình thường"));
  if (dot) dot.className = "dot " + (!reporting.length ? "is-idle" : (hard ? "is-bad" : (soft ? "is-warn" : "is-ok")));
}
