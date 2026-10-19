import { toneIcon } from "../icons.js?v=20261018l";
import { bootProgress, finite, freshText, history, metresOf, pointsOf, shownPercent, reasonText, aiLabel, toneFor, widthPercent } from "../state.js?v=20261018l";
import { drawSeries } from "./sparkline.js?v=20261018l";

const GAUGE = 2 * Math.PI * 52;

function setBar(el, ratio) {
  if (!el) return;
  const pct = ratio === null ? 0 : Math.max(0, Math.min(100, ratio * 100));
  el.style.setProperty("--w", pct + "%");
}

export function fillCard(card, item, v, view) {
  const tone = toneFor(view);
  card.classList.remove("is-ok", "is-warn", "is-bad", "is-stale", "is-idle", "is-missing");
  card.classList.add("is-" + tone);
  card.querySelector("[data-title]").textContent = item.display_name || item.link_id;
  card.querySelector("[data-sub]").textContent = "";
  const badge = card.querySelector("[data-state]");
  badge.className = "badge is-" + tone;
  card.querySelector("[data-state-icon]").innerHTML = toneIcon(tone);
  card.querySelector("[data-state-label]").textContent = view.label;
  card.querySelector("[data-state-text]").textContent = view.label;
  const widthM = finite(v?.width_m) ? Number(v.width_m) : null;
  const flowPct = shownPercent(view);
  const capPct = widthPercent(v);
  const num = card.querySelector("[data-score-num]");
  const unit = card.querySelector("[data-score-unit]");
  const maxEl = card.querySelector("[data-score-max]");
  num.textContent = flowPct === null ? "--" : String(Math.round(flowPct));
  unit.textContent = "%";
  maxEl.textContent = widthM === null ? "Chưa nhập độ rộng" : metresOf(pointsOf(flowPct, widthM), widthM);
  maxEl.classList.toggle("is-missing", widthM === null);
  const flowRatio = flowPct === null ? null : flowPct / 100;
  const gauge = card.querySelector("[data-gauge]");
  if (gauge) {
    gauge.style.setProperty("--offset", String(flowRatio === null ? GAUGE : GAUGE * (1 - Math.max(0, Math.min(1, flowRatio)))));
    // A round cap on a zero-length arc still draws a dot.
    gauge.classList.toggle("is-empty", flowRatio === null || flowRatio <= 0);
  }
  const capRatio = capPct === null ? null : capPct / 100;
  const capValue = card.querySelector("[data-cap-value]");
  if (capValue) {
    const capText = capPct === null ? "--" : Math.round(capPct) + "%";
    capValue.textContent = widthM === null || capPct === null ? capText : capText + " · " + metresOf(v?.width_points, widthM).replace("≈ ", "");
  }
  setBar(card.querySelector("[data-cap-bar]"), capRatio);
  const drop = card.querySelector("[data-cap-drop]");
  if (drop) {
    const lost = capPct === null ? 0 : Math.round(100 - capPct);
    drop.textContent = lost > 5 ? "-" + lost + "% do vật cản tĩnh" : "";
  }
  const narrow = card.querySelector("[data-narrow]");
  if (narrow) narrow.classList.toggle("hidden", !(capRatio !== null && capRatio < 0.5));
  const reason = card.querySelector("[data-reason]");
  if (reason) reason.textContent = reasonText(v, { withAi: false });
  const ai = card.querySelector("[data-ai]");
  if (ai) ai.textContent = aiLabel(v);
  const fresh = card.querySelector("[data-fresh]");
  if (fresh) fresh.textContent = freshText(v, view);
  const cta = card.querySelector("[data-width-cta]");
  if (cta) cta.classList.toggle("hidden", widthM !== null);
  const settle = card.querySelector("[data-settle]");
  const progress = bootProgress(item.link_id, v);
  if (settle) {
    settle.classList.toggle("hidden", progress === null);
    const bar = settle.querySelector("[data-settle-bar]");
    if (bar && progress !== null) bar.style.setProperty("--w", Math.round(progress * 100) + "%");
  }
  const points = (history[item.link_id] || []).slice(-120);
  drawSeries(card.querySelector("[data-chart]"), points, "v");
}

export function ensureCards(host, list, onOpen) {
  const keep = new Set(list.map(item => item.link_id));
  [...host.querySelectorAll(".card")].forEach(card => {
    if (!keep.has(card.dataset.linkId)) card.remove();
  });
  list.forEach(item => {
    let card = host.querySelector('[data-link-id="' + CSS.escape(item.link_id) + '"]');
    if (!card) {
      card = document.querySelector("#card-template").content.firstElementChild.cloneNode(true);
      card.dataset.linkId = item.link_id;
      card.addEventListener("click", event => {
        if (event.target.closest("button")) return;
        onOpen(item.link_id);
      });
      const cta = card.querySelector("[data-width-cta]");
      if (cta) cta.addEventListener("click", () => onOpen(item.link_id, true));
      host.appendChild(card);
    }
  });
  list.forEach(item => {
    const card = host.querySelector('[data-link-id="' + CSS.escape(item.link_id) + '"]');
    if (card) host.appendChild(card);
  });
}
