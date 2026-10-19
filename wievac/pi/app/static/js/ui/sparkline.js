function cssColor(canvas, name, fallback) {
  const value = getComputedStyle(canvas).getPropertyValue(name).trim();
  return value || fallback;
}

export function drawSeries(canvas, points, key, accent) {
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, rect.width);
  const h = Math.max(1, rect.height);
  const width = Math.round(w * dpr);
  const height = Math.round(h * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const rows = (points || []).filter(point => Number.isFinite(Number(point[key])));
  ctx.strokeStyle = cssColor(canvas, "--line", "#2c3848");
  ctx.lineWidth = 1;
  [0.2, 0.7].forEach(mark => {
    const y = h - mark * (h - 8) - 4;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  });
  if (!rows.length) return;
  ctx.strokeStyle = accent || cssColor(canvas, "--chart", "#1f9d62");
  ctx.lineWidth = 1.75;
  ctx.beginPath();
  rows.forEach((point, index) => {
    const scale = Number.isFinite(Number(point.max)) && Number(point.max) > 0 ? Number(point.max) : 1;
    const x = rows.length === 1 ? w / 2 : index * (w - 8) / Math.max(1, rows.length - 1) + 4;
    const y = h - 4 - Math.max(0, Math.min(1, Number(point[key]) / scale)) * (h - 8);
    if (index) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  });
  ctx.stroke();
}

export function seriesValueAt(points, key, ratio) {
  const rows = (points || []).filter(point => Number.isFinite(Number(point[key])));
  if (!rows.length) return null;
  const index = Math.max(0, Math.min(rows.length - 1, Math.round(ratio * (rows.length - 1))));
  return rows[index];
}
