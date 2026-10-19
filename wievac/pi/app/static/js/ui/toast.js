let timer = 0;

export function toast(message) {
  const el = document.querySelector("#toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("is-on");
  window.clearTimeout(timer);
  timer = window.setTimeout(() => el.classList.remove("is-on"), 4200);
}
