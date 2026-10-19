export const store = {
  catalog: {},
  links: {},
  labels: {},
  recording: {},
  quality: {},
  pi: {},
  views: {},
  selectedId: null,
  demo: false,
  connected: true,
};

export const displayState = { system: { stable: null, pending: null, count: 0 } };
export const history = {};
export const events = [];
const lastLabel = {};
const lastAi = {};
const bootSeen = {};
let eventRev = 0;

export function eventRevision() {
  return eventRev;
}

export function pushEvent(linkId, title, detail) {
  events.unshift({ t: Date.now(), linkId, title, detail });
  if (events.length > 200) events.pop();
  eventRev += 1;
}

export function finite(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

const METRES = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });

/* Percent is the headline (relative to this corridor when empty). Metres come
 * from the operator width and are the same numbers the router uses
 * (flow_points / 1000 == widthMeters * (1 - k)). */
export function livePercent(v) {
  const state = String(v?.state || "").toUpperCase();
  if (v?.stale === true || state === "STALE" || state === "UNKNOWN") return null;
  const raw = v?.score ?? v?.local_passability_score;
  return finite(raw) && Number(raw) >= 0 && Number(raw) <= 100 ? Number(raw) : null;
}

/* Headline percent: the hysteresis view keeps the last valid score through
 * short UNKNOWN windows (as the original wall did), so the number does not
 * blink while the badge holds its state. Nothing is shown once the link is stale. */
export function shownPercent(view) {
  if (!view || !view.hasPacket || view.stale) return null;
  return finite(view.score) ? Math.max(0, Math.min(100, Number(view.score))) : null;
}

/* Points for a percent of the operator width (1000 points == 1 m). */
export function pointsOf(percent, widthM) {
  return finite(percent) && finite(widthM) ? Number(percent) / 100 * Number(widthM) * 1000 : null;
}

export function widthPercent(v) {
  return finite(v?.w_cap) && Number(v.w_cap) >= 0 && Number(v.w_cap) <= 100.5 ? Math.min(100, Number(v.w_cap)) : null;
}

export function metres(value) {
  return METRES.format(Math.round(Number(value) * 10) / 10) + " m";
}

/* "≈ 3,2 / 4 m" from points (1000 points == 1 m). */
export function metresOf(points, widthM) {
  if (!finite(widthM)) return "";
  if (!finite(points)) return "-- / " + metres(widthM);
  return "≈ " + METRES.format(Math.round(Number(points) / 100) / 10) + " / " + metres(widthM);
}

export function packetPresent(v) {
  return !!(v && v.arrival_timestamp_us!==null && v.arrival_timestamp_us!==undefined);
}

export function unstableWindow(v) {
  if (v.display_unstable !== undefined) return v.display_unstable;
  const sampleCount = Number(v?.sample_count || 100);
  const gap = Number(v?.sequence_gap || 0);
  const gapRatio = sampleCount + gap > 0 ? gap / (sampleCount + gap) : 0;
  return (gapRatio > 0.25 || Number(v?.queue_drop_count || 0) > 5 || Number(v?.invalid_count || 0) > 10);
}

export function commitDisplayState(id, candidate) {
  if (!displayState[id]) displayState[id] = { stable: null, pending: null, count: 0, score: null };
  const s = displayState[id];
  if (!s.stable) { s.stable = candidate; return candidate; }
  if (candidate === s.stable) { s.pending = null; s.count = 0; return s.stable; }
  if (s.pending !== candidate) { s.pending = candidate; s.count = 1; }
  else { s.count++; }
  if (s.count>=3) { s.stable = candidate; s.pending = null; s.count = 0; }
  return s.stable;
}

export function passabilityFromScore(score) {
  if (score < 20) return "BLOCKED";
  if (score < 70) return "DEGRADED";
  return "PASSABLE";
}

const SEVERITY = { PASSABLE: 0, DEGRADED: 1, BLOCKED: 2 };

/* The score alone misses a firmware DEGRADED/BLOCKED that kept a high score
 * (e.g. the AI saw a person: score 85, state DEGRADED). Show the worse one;
 * commitDisplayState still requires count>=3 before the view changes. */
export function worstState(scoreState, firmwareState) {
  const firmware = SEVERITY[firmwareState];
  return firmware !== undefined && firmware > SEVERITY[scoreState] ? firmwareState : scoreState;
}

export function isSettling(v) {
  const reason = Number(v?.reason_code ?? v?.reason);
  return v?.baseline_update_reason === "boot_unverified"
    || v?.transition_state === "BOOT_UNVERIFIED"
    || v?.boot_unverified === true
    || v?.transition_state === "BOOT_SETTLE"
    || v?.baseline_update_reason === "warming_up"
    || reason === 2;
}

export function labelFor(v) {
  if (!packetPresent(v)) return "Chưa nhận dữ liệu";
  const state = String(v.state || "UNKNOWN").toUpperCase();
  if (v.stale === true || state === "STALE") return "Mất kết nối";
  if (unstableWindow(v)) return "Không ổn định";
  if (state === "UNKNOWN") return "Chưa xác định";
  if (state === "PASSABLE") return "An toàn";
  if (state === "DEGRADED") return "Giảm lưu thông";
  if (state === "BLOCKED") return "Bị chặn";
  if (state === "CONFIRMING") return "Đang xác nhận môi trường";
  return "Chưa xác định";
}

export function toneFor(view) {
  if (view.settling) return "idle";
  if (!view.hasPacket) return "missing";
  if (view.stale || view.viewState === "STALE") return "stale";
  if (view.viewState === "PASSABLE") return "ok";
  if (view.viewState === "DEGRADED") return "warn";
  if (view.viewState === "BLOCKED" || view.viewState === "UNSTABLE") return "bad";
  return "idle";
}

export function reasonText(v, { withAi = true } = {}) {
  const code = Number(v?.reason_code ?? v?.reason);
  if (code === 6) return withAi ? "AI phát hiện bất thường (có người/vật lạ)" : "";
  if (code === 107 || v?.baseline_update_reason === "static_change" || v?.transition_state === "STATIC_CHANGE" || Number(v?.static_flag) === 2) return "Môi trường thay đổi · đang xác nhận (cần 30 giây trống, yên)";
  if (code === 108) return "Có người dừng lâu";
  if (code === 109 || String(v?.state || "").toUpperCase() === "BLOCKED") return "Bị chặn";
  // Lost packets are shown in "Chỉ số kỹ thuật", not as a blinking card line.
  if (code === 2 || isSettling(v)) return "Đang khởi động/ổn định";
  return "";
}

export function aiState(v) {
  if (Number(v?.reason_code ?? v?.reason) === 6 || v?.disagreement === true) return "alert";
  const version = String(v?.model_version || "");
  if (!version || version === "NOT_READY") return "off";
  return "ok";
}

export function aiLabel(v) {
  const state = aiState(v);
  if (state === "alert") return "AI: phát hiện bất thường (có người/vật lạ)";
  if (state === "off") return "AI: chưa sẵn sàng";
  return "AI: bình thường";
}

function ensureHistory(id) {
  if (!history[id]) history[id] = [];
  if (!displayState[id]) displayState[id] = { stable: null, pending: null, count: 0, score: null };
}

export function viewValues(id, v, record) {
  ensureHistory(id);
  const firmwareState = String(v?.state || "UNKNOWN").toUpperCase();
  const stale = firmwareState === "STALE" || v?.stale === true;
  const unstable = unstableWindow(v || {});
  const hasPacket = packetPresent(v);
  const raw = v?.score ?? v?.local_passability_score;
  const candidateScore = hasPacket && !stale && finite(raw) ? Number(raw) : null;
  let rawCandidate;
  if (stale) rawCandidate = "STALE";
  else if (unstable) rawCandidate = "UNSTABLE";
  else if (finite(candidateScore)) {
    const fromScore = passabilityFromScore(candidateScore);
    // An environment shift (reason 107) is uncertainty, not reduced flow: the
    // RX holds the last measured score while it confirms the new environment.
    const reasonCode = Number(v?.reason_code ?? v?.reason);
    const confirming = firmwareState === "DEGRADED" && reasonCode === 107;
    // A few lost packets (102/103) are a transport note, not reduced flow:
    // the window was still scored, so the score decides the colour.
    const transport = reasonCode === 102 || reasonCode === 103;
    rawCandidate = confirming ? (fromScore === "PASSABLE" ? "CONFIRMING" : fromScore)
                              : (transport ? fromScore : worstState(fromScore, firmwareState));
  }
  else rawCandidate = "UNKNOWN";
  const viewState = record ? commitDisplayState(id, rawCandidate) : (displayState[id].stable || rawCandidate);
  const label = labelFor({ ...v, state: viewState, display_unstable: viewState === "UNSTABLE" });
  if (finite(candidateScore) && viewState !== "UNSTABLE" && viewState !== "UNKNOWN" && viewState !== "STALE") displayState[id].score = candidateScore;
  const heldScore = finite(displayState[id].score) ? displayState[id].score : null;
  const score = finite(candidateScore) && viewState !== "UNSTABLE" && viewState !== "UNKNOWN" && viewState !== "STALE" ? candidateScore : heldScore;
  const livePct = livePercent(v);
  if (record && livePct !== null && viewState !== "UNSTABLE" && viewState !== "UNKNOWN" && viewState !== "STALE") {
    // History is in percent so it works before a width is entered.
    history[id].push({ v: livePct, w: widthPercent(v), max: 100, t: Date.now() });
    if (history[id].length > 600) history[id].shift();
  }
  const settling = isSettling(v) && hasPacket && !stale;
  const shown = settling ? "Đang ổn định sau khi bật" : label;
  if (record) {
    if (lastLabel[id] && lastLabel[id] !== shown) pushEvent(id, catalogName(id), shown);
    lastLabel[id] = shown;
    const ai = aiState(v);
    if (lastAi[id] && lastAi[id] !== "alert" && ai === "alert") pushEvent(id, catalogName(id), "AI phát hiện bất thường");
    lastAi[id] = ai;
  }
  return { state: firmwareState, stale, viewState, label: shown, score, hasPacket, settling };
}

export function catalogName(id) {
  return (store.catalog[id] && store.catalog[id].display_name) || id;
}

export function catalogList() {
  return Object.values(store.catalog).sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || String(a.link_id).localeCompare(String(b.link_id)));
}

export function snapshotViews(record) {
  store.views = {};
  catalogList().forEach(item => {
    store.views[item.link_id] = viewValues(item.link_id, store.links[item.link_id] || {}, record);
  });
}

export function bootProgress(id, v) {
  if (!isSettling(v)) {
    delete bootSeen[id];
    return null;
  }
  if (!bootSeen[id]) bootSeen[id] = Date.now();
  return Math.max(0, Math.min(1, (Date.now() - bootSeen[id]) / 120000));
}

export function seedDemoHistory() {
  if (!store.demo) return;
  const now = Date.now();
  Object.entries(store.links).forEach(([id, row]) => {
    if (history[id] && history[id].length) return;
    const flow = livePercent(row);
    if (flow === null) return;
    const cap = widthPercent(row);
    history[id] = [];
    for (let i = 40; i >= 0; i -= 1) {
      const wobble = Math.sin(i / 5) * 5;
      history[id].push({
        v: Math.max(0, Math.min(100, Math.round(flow + wobble))),
        w: cap === null ? null : Math.max(0, Math.min(100, Math.round(cap + wobble * 0.4))),
        max: 100,
        t: now - i * 3000,
      });
    }
  });
}

export function attentionKind(v, viewState) {
  if (!packetPresent(v)) return "missing";
  if (viewState === "STALE" || v?.stale === true) return "stale";
  if (viewState === "UNSTABLE") return "unstable";
  if (viewState === "BLOCKED") return "blocked";
  return "ok";
}

export function freshText(v, view) {
  const age = finite(v?.age_ms) ? Number(v.age_ms) / 1000 : null;
  if (!view.hasPacket) return "Chưa nhận dữ liệu";
  if (age === null) return "";
  const seconds = Math.max(0, Math.round(age));
  if (view.stale) return "Mất " + seconds + " giây";
  if (seconds < 2) return "Vừa xong";
  return seconds + " giây trước";
}

export function routeText(linkId, v, view) {
  let isRelay = v?.is_relay || (Number(v?.hop_count || 0) > 0) || v?.role === "RELAY";
  let path = v?.route_path || "";
  const nodeNum = String(linkId || "").replace("link-", "");
  if (isRelay && path) {
    const match = path.match(/➔\s*Node\s*(\d+)\s*\(Gateway\)/);
    if (match) {
      const gwView = store.views["link-" + match[1]];
      if (gwView && (gwView.stale || !gwView.hasPacket)) {
        isRelay = false;
        path = "Node " + nodeNum + " → Pi 5 (trực tiếp)";
      }
    }
  }
  if (!view.hasPacket) return "Chưa kết nối";
  const shown = String(path || (isRelay ? "Qua node trung gian" : "Trực tiếp tới Pi")).replaceAll("➔", "→");
  return (isRelay ? "Trung gian · " : "Trực tiếp · ") + shown;
}
