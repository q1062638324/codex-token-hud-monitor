const endpoint = "http://127.0.0.1:38427/v1/state";
let period = "today";
const byId = (id) => document.getElementById(id);
const hudRoot = document.querySelector(".hud");
const hudBody = document.querySelector(".hud-body");
const iconFace = byId("icon-face");
let baseLayoutWidth = 0;
let baseLayoutHeight = 0;

function updateUiScale() {
  if (hudRoot.classList.contains("icon-mode")) return;
  if (!baseLayoutWidth || !baseLayoutHeight) {
    baseLayoutWidth = hudBody.getBoundingClientRect().width;
    baseLayoutHeight = hudBody.getBoundingClientRect().height;
  }
  const rootStyle = getComputedStyle(hudRoot);
  const contentWidth = hudRoot.clientWidth - parseFloat(rootStyle.paddingLeft) - parseFloat(rootStyle.paddingRight);
  const contentHeight = hudRoot.clientHeight - parseFloat(rootStyle.paddingTop) - parseFloat(rootStyle.paddingBottom);
  const widthScale = contentWidth / baseLayoutWidth;
  const heightScale = contentHeight / baseLayoutHeight;
  const scale = Math.max(0.72, Math.min(1.5, Math.min(widthScale, heightScale)));
  hudBody.style.width = `${baseLayoutWidth}px`;
  hudBody.style.height = `${baseLayoutHeight}px`;
  hudBody.style.transform = `scale(${scale})`;
}

const formatTokens = (value) => {
  const number = Number(value || 0);
  if (number >= 1000000) return `${(number / 1000000).toFixed(1)}M`;
  if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 1 : 2)}k`;
  return number.toLocaleString("zh-CN");
};
const formatExactTokens = (value) => Number(value || 0).toLocaleString("zh-CN");

function renderUsage(usage) {
  const current = usage || {};
  const total = Number(current.input_tokens || 0) + Number(current.output_tokens || 0);
  byId("current-total").textContent = formatExactTokens(total);
  byId("input").textContent = formatTokens(current.input_tokens);
  byId("cached-input").textContent = formatTokens(current.cached_input_tokens);
  byId("uncached-input").textContent = formatTokens(current.uncached_input_tokens);
  byId("input-hit-rate").textContent = `${Math.round(Number(current.input_cache_hit_rate || 0) * 100)}%`;
  byId("output").textContent = formatTokens(current.output_tokens);
  byId("uncached-output").textContent = current.output_cache_available ? formatTokens(current.uncached_output_tokens) : "—";
  byId("reasoning").textContent = formatTokens(current.reasoning_output_tokens);
}

function renderPeriod(usage) {
  const current = usage || {};
  const total = Number(current.input_tokens || 0) + Number(current.output_tokens || 0);
  byId("period-total").textContent = formatTokens(total);
  byId("period-cache").textContent = `缓存命中 ${Math.round(Number(current.input_cache_hit_rate || 0) * 100)}%`;
}

function render(state) {
  const current = state.current || {};
  renderUsage(current);
  renderPeriod(state[period]);
  byId("model").textContent = current.model || "等待 Codex";
  byId("turn").textContent = current.turn_id || "—";
  byId("message").textContent = state.message || "等待 usage 数据";
  byId("updated").textContent = state.updated_at ? state.updated_at.slice(11, 19) : "—";
  byId("connection").classList.add("ready");
  byId("connection").lastChild.textContent = " 已连接";
}

async function refresh() {
  try {
    const invoke = window.__TAURI__?.core?.invoke;
    if (invoke) {
      render(JSON.parse(await invoke("read_state")));
    } else {
      const response = await fetch(`${endpoint}?t=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error("state unavailable");
      render(await response.json());
    }
  } catch {
    byId("connection").classList.remove("ready");
    byId("connection").lastChild.textContent = " 等待数据";
  }
}

document.querySelectorAll(".period-tab").forEach((button) => {
  button.addEventListener("click", () => {
    period = button.dataset.period;
    document.querySelectorAll(".period-tab").forEach((item) => item.classList.toggle("active", item === button));
    refresh();
  });
});

let normalWindowSize = { width: window.outerWidth, height: window.outerHeight };
const invokeNative = () => window.__TAURI__?.core?.invoke;

async function minimizeToIcon(event) {
  event.stopPropagation();
  const invoke = invokeNative();
  if (!invoke) return;
  normalWindowSize = { width: window.outerWidth, height: window.outerHeight };
  hudRoot.classList.add("icon-mode");
  await invoke("minimize_to_icon");
}

async function minimizeToTaskbar(event) {
  event.stopPropagation();
  const invoke = invokeNative();
  if (!invoke) return;
  await invoke("minimize_to_taskbar");
}

async function restoreFromIcon(event) {
  event.stopPropagation();
  const invoke = invokeNative();
  if (!invoke) return;
  await invoke("restore_window", normalWindowSize);
  hudRoot.classList.remove("icon-mode");
  updateUiScale();
}

byId("minimize").addEventListener("click", minimizeToIcon);
byId("taskbar").addEventListener("click", minimizeToTaskbar);
let iconPointer = null;
let iconDragBusy = false;

async function startNativeDragging() {
  const currentWindow = window.__TAURI__?.window?.getCurrentWindow?.();
  if (!currentWindow) {
    iconDragBusy = false;
    return;
  }
  iconDragBusy = true;
  try {
    await currentWindow.startDragging();
  } catch {
    iconDragBusy = false;
  }
}

iconFace.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  event.preventDefault();
  iconFace.setPointerCapture(event.pointerId);
  iconPointer = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, dragged: false };
});

iconFace.addEventListener("pointermove", (event) => {
  if (!iconPointer || event.pointerId !== iconPointer.pointerId || iconPointer.dragged) return;
  const distance = Math.hypot(event.clientX - iconPointer.startX, event.clientY - iconPointer.startY);
  if (distance < 5) return;
  iconPointer.dragged = true;
  startNativeDragging();
});

iconFace.addEventListener("pointerup", (event) => {
  if (!iconPointer || event.pointerId !== iconPointer.pointerId) return;
  const dragged = iconPointer.dragged || iconDragBusy;
  iconFace.releasePointerCapture(event.pointerId);
  iconPointer = null;
  iconDragBusy = false;
  if (!dragged) restoreFromIcon(event);
});

iconFace.addEventListener("pointercancel", () => {
  iconPointer = null;
  iconDragBusy = false;
});
byId("exit").addEventListener("click", async (event) => {
  event.stopPropagation();
  const invoke = invokeNative();
  if (invoke) await invoke("close_window");
});

document.querySelector("[data-tauri-drag-region]").addEventListener("mousedown", async (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (event.button !== 0 || target?.closest("button")) return;
  event.preventDefault();
  try {
    const currentWindow = window.__TAURI__?.window?.getCurrentWindow?.();
    if (!currentWindow) return;
    await currentWindow.startDragging();
  } catch {
    return;
  }
});

const resizeHandle = byId("resize-handle");
let resizeState = null;
let resizeBusy = false;

resizeHandle.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  event.stopPropagation();
  resizeHandle.setPointerCapture(event.pointerId);
  resizeState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    width: window.outerWidth,
    height: window.outerHeight,
  };
});

resizeHandle.addEventListener("pointermove", async (event) => {
  if (!resizeState || resizeBusy || event.pointerId !== resizeState.pointerId) return;
  const scaleX = (resizeState.width + event.clientX - resizeState.startX) / resizeState.width;
  const scaleY = (resizeState.height + event.clientY - resizeState.startY) / resizeState.height;
  const scale = Math.max(0.72, Math.min(1.5, Math.min(scaleX, scaleY)));
  const width = Math.round(resizeState.width * scale);
  const height = Math.round(resizeState.height * scale);
  const invoke = invokeNative();
  if (!invoke) return;
  resizeBusy = true;
  try {
    await invoke("resize_window", { width, height });
  } finally {
    resizeBusy = false;
  }
});

resizeHandle.addEventListener("pointerup", (event) => {
  if (resizeState?.pointerId === event.pointerId) {
    resizeHandle.releasePointerCapture(event.pointerId);
    resizeState = null;
  }
});

refresh();
setInterval(refresh, 1000);
window.addEventListener("resize", updateUiScale);
updateUiScale();
