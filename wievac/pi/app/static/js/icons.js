const svg = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const icon = {
  ok: svg('<path d="M20 6 9 17l-5-5"/>'),
  warn: svg('<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 4.8 2.8 18a2 2 0 0 0 1.7 3h15a2 2 0 0 0 1.7-3L13.7 4.8a2 2 0 0 0-3.4 0z"/>'),
  bad: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 17h.01"/>'),
  stale: svg('<path d="M2 8.5a14 14 0 0 1 20 0"/><path d="M5 12a10 10 0 0 1 14 0"/><path d="M8.5 15.5a6 6 0 0 1 7 0"/><path d="M12 19h.01"/><path d="M4 4l16 16"/>'),
  idle: svg('<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2 2"/>'),
  missing: svg('<path d="M4 12h4l2-4 4 8 2-4h4"/>'),
  up: svg('<path d="M12 19V5"/><path d="m6 11 6-6 6 6"/>'),
  left: svg('<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>'),
  right: svg('<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>'),
  stop: svg('<rect x="6" y="6" width="12" height="12" rx="1"/>'),
  speaker: svg('<path d="M4 10v4h3l4 3V7L7 10H4z"/><path d="M16 9a4 4 0 0 1 0 6"/>'),
  wait: svg('<circle cx="12" cy="12" r="8"/><path d="M12 8v5"/>'),
  exit: svg('<path d="M9 6H6v12h3"/><path d="M10 12h8"/><path d="m15 9 3 3-3 3"/>'),
  clear: svg('<path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M7 7l1 12h8l1-12"/>'),
};

export function toneIcon(tone) {
  if (tone === "ok") return icon.ok;
  if (tone === "warn") return icon.warn;
  if (tone === "bad") return icon.bad;
  if (tone === "stale") return icon.stale;
  if (tone === "missing") return icon.missing;
  return icon.idle;
}
