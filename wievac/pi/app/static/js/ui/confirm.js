export function confirmAction(message) {
  const root = document.querySelector("#confirm");
  const text = document.querySelector("#confirm-text");
  const ok = document.querySelector("#confirm-ok");
  const cancel = document.querySelector("#confirm-cancel");
  text.textContent = message;
  root.hidden = false;
  ok.focus();
  return new Promise(resolve => {
    const finish = (value) => {
      root.hidden = true;
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
  });
}
