import { fetchDemo, fetchOverview } from "./api.js?v=20261018l";
import { pushEvent, seedDemoHistory, snapshotViews, store } from "./state.js?v=20261018l";
import { initCapture, renderCapture } from "./views/capture.js?v=20261018l";
import { initCorridor, renderCorridor } from "./views/corridor.js?v=20261018l";
import { initGuidance } from "./views/guidance.js?v=20261018l";
import { initNodes, renderNodes } from "./views/nodes.js?v=20261018l";
import { renderOverview, renderSystem } from "./views/overview.js?v=20261018l";

store.demo = new URLSearchParams(location.search).get("demo") === "1";
let demoEvents = false;

function openCorridor(id, focusWidth) {
  store.selectedId = id;
  if (location.hash !== "#/hanh-lang/" + encodeURIComponent(id)) {
    location.hash = "#/hanh-lang/" + encodeURIComponent(id);
  }
  if (focusWidth) renderCorridor(true);
}

function currentRoute() {
  const raw = (location.hash || "#/tong-quan").replace(/^#\/?/, "");
  const [name, id] = raw.split("/");
  const known = ["tong-quan", "hanh-lang", "dieu-huong", "thu-du-lieu", "quan-ly"];
  return { name: known.includes(name) ? name : "tong-quan", id: id ? decodeURIComponent(id) : "" };
}

function applyRoute() {
  const route = currentRoute();
  if (route.id) store.selectedId = route.id;
  const present = document.documentElement.dataset.present === "1";
  const name = present ? "tong-quan" : route.name;
  document.querySelectorAll("[data-view]").forEach(section => {
    section.hidden = section.dataset.view !== name;
  });
  document.querySelectorAll("[data-route]").forEach(link => {
    link.classList.toggle("is-active", link.dataset.route === route.name && !present);
    if (present && link.dataset.route === "tong-quan") link.classList.add("is-active");
  });
  if (name === "tong-quan") renderOverview(openCorridor);
  if (name === "hanh-lang") renderCorridor(false);
  if (name === "thu-du-lieu") renderCapture();
  if (name === "quan-ly") renderNodes();
  renderSystem();
}

function seedDemoEvents() {
  if (!store.demo || demoEvents) return;
  demoEvents = true;
  pushEvent("link-3", "Cầu thang C", "Mất kết nối");
  pushEvent("link-2", "Hành lang B", "Giảm lưu thông");
}

async function refresh() {
  try {
    const response = store.demo ? await fetchDemo() : await fetchOverview();
    if (!response.ok) throw Error("overview");
    const data = await response.json();
    store.links = data.links || {};
    store.catalog = data.catalog || {};
    if (!Object.keys(store.catalog).length) {
      store.catalog = Object.fromEntries(Object.keys(store.links).map(id => [id, { link_id: id, display_name: id, enabled: true }]));
    }
    store.labels = data.active_labels || {};
    store.recording = data.recording || {};
    store.quality = data.quality || {};
    store.pi = data.pi_ingest || (data.quality && data.quality.pi_ingest) || {};
    store.connected = true;
    if (store.demo) seedDemoHistory();
    snapshotViews(true);
    seedDemoEvents();
    document.body.classList.remove("is-offline");
    const banner = document.querySelector("#connection");
    banner.hidden = true;
    const updated = document.querySelector("#updated");
    if (updated) updated.textContent = new Date().toLocaleTimeString("vi-VN");
    const clock = document.querySelector("#clock");
    if (clock) clock.textContent = new Date().toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    applyRoute();
  } catch (err) {
    store.connected = false;
    document.body.classList.add("is-offline");
    const banner = document.querySelector("#connection");
    banner.hidden = false;
    banner.textContent = "Mất kết nối tới Pi. Đang thử lại.";
    renderSystem();
  }
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("wievac-theme", theme); } catch (err) { /* private mode */ }
}

function setPresent(on) {
  if (on) {
    document.documentElement.dataset.present = "1";
    document.documentElement.dataset.theme = "dark";
    if (!location.hash.startsWith("#/tong-quan")) location.hash = "#/tong-quan";
  } else {
    delete document.documentElement.dataset.present;
    try {
      const saved = localStorage.getItem("wievac-theme");
      if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
    } catch (err) { /* private mode */ }
  }
  applyRoute();
}

if (store.demo) document.querySelector("#demo-banner").hidden = false;
initCorridor();
initGuidance();
initCapture();
initNodes();
document.querySelector("#theme-toggle").addEventListener("click", () => {
  if (document.documentElement.dataset.present === "1") return;
  setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
});
document.querySelector("#present-toggle").addEventListener("click", () => setPresent(document.documentElement.dataset.present !== "1"));
document.querySelector("#present-exit").addEventListener("click", () => setPresent(false));
window.addEventListener("hashchange", applyRoute);
window.addEventListener("keydown", event => {
  const typing = event.target.closest("input, textarea, select");
  if ((event.key === "f" || event.key === "F") && !typing) {
    event.preventDefault();
    setPresent(document.documentElement.dataset.present !== "1");
  }
  if (event.key === "Escape") {
    setPresent(false);
    const modal = document.querySelector("#node-modal");
    if (modal && !modal.hidden) document.querySelector("#form-cancel").click();
    const confirmBox = document.querySelector("#confirm");
    if (confirmBox && !confirmBox.hidden) document.querySelector("#confirm-cancel").click();
  }
});
window.addEventListener("resize", applyRoute);
refresh();
setInterval(refresh,1000);
