import { mutate, errorText } from "../api.js?v=20261018l";
import { icon } from "../icons.js?v=20261018l";
import { pushEvent } from "../state.js?v=20261018l";
import { confirmAction } from "../ui/confirm.js?v=20261018l";
import { toast } from "../ui/toast.js?v=20261018l";

const commands = [
  { id: 11, label: "Đi thẳng", icon: icon.up },
  { id: 12, label: "Rẽ trái", icon: icon.left },
  { id: 13, label: "Rẽ phải", icon: icon.right },
  { id: 10, label: "Bắt đầu sơ tán", icon: icon.exit },
  { id: 16, label: "Lối thoát", icon: icon.exit },
  { id: 17, label: "Dừng lại", icon: icon.stop, danger: true },
  { id: 18, label: "Chờ", icon: icon.wait },
  { id: 19, label: "Xóa điều hướng", icon: icon.clear },
];

const choice = { target: 1, command: 11, priority: 1 };

function selected(group, id) {
  document.querySelectorAll('[data-choice="' + group + '"]').forEach(button => {
    button.classList.toggle("is-on", Number(button.dataset.value) === id);
  });
}

function paintResult(ok, text) {
  const el = document.querySelector("#guidance-result");
  const badge = document.querySelector("#guidance-badge");
  if (badge) {
    badge.textContent = ok ? "Đã gửi" : "Chưa gửi";
    badge.className = "badge " + (ok ? "is-ok" : "is-idle");
  }
  if (el) el.textContent = text;
}

async function send(target, command, priority) {
  const dangerous = command === 17 || priority === 4 || (target === 0 && command === 19);
  if (dangerous) {
    const ok = await confirmAction("Gửi lệnh này tới mạng sơ tán? Thao tác không tự hủy.");
    if (!ok) return;
  }
  try {
    const data = await mutate("POST", "/api/v5/guidance", { target, command, priority });
    const res = data.result || {};
    const who = res.target_node_id === 0 ? "toàn mạng" : "node " + (res.target_node_id ?? target);
    paintResult(true, "Đã gửi lệnh " + (res.command ?? command) + " tới " + who + (res.gateway_ip ? " qua " + res.gateway_ip + ":8889" : "") + (res.command_id ? " · mã " + res.command_id : ""));
    pushEvent("", "Điều hướng", "Đã gửi lệnh " + command + " tới " + who);
    toast("Đã gửi lệnh điều hướng");
  } catch (err) {
    paintResult(false, errorText(err.message));
    toast(errorText(err.message));
  }
}

export function initGuidance() {
  const targets = document.querySelector("#guidance-targets");
  const cmds = document.querySelector("#guidance-commands");
  const prios = document.querySelector("#guidance-priority");
  [{ id: 1, label: "Node 1" }, { id: 2, label: "Node 2" }, { id: 3, label: "Node 3" }, { id: 0, label: "Toàn mạng" }].forEach(item => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice";
    button.dataset.choice = "target";
    button.dataset.value = String(item.id);
    button.textContent = item.label;
    button.addEventListener("click", () => { choice.target = item.id; selected("target", item.id); });
    targets.appendChild(button);
  });
  commands.forEach(item => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice" + (item.danger ? " is-danger" : "");
    button.dataset.choice = "command";
    button.dataset.value = String(item.id);
    button.innerHTML = item.icon + "<span></span>";
    button.querySelector("span").textContent = item.label;
    button.addEventListener("click", () => { choice.command = item.id; selected("command", item.id); });
    cmds.appendChild(button);
  });
  [{ id: 1, label: "Bình thường" }, { id: 2, label: "Sơ tán" }, { id: 3, label: "Nguy hiểm" }, { id: 4, label: "Khẩn cấp" }].forEach(item => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice" + (item.id === 4 ? " is-danger" : "");
    button.dataset.choice = "priority";
    button.dataset.value = String(item.id);
    button.textContent = item.label;
    button.addEventListener("click", () => { choice.priority = item.id; selected("priority", item.id); });
    prios.appendChild(button);
  });
  selected("target", choice.target);
  selected("command", choice.command);
  selected("priority", choice.priority);
  document.querySelector("#guidance-send").addEventListener("click", () => send(choice.target, choice.command, choice.priority));
  document.querySelector("#guidance-stop").addEventListener("click", () => send(0, 19, 4));
}

export function renderGuidance() {}
