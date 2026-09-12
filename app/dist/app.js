const endpoint = "http://127.0.0.1:38427/v1/state";
let period = "today";
const byId = (id) => document.getElementById(id);
const hudRoot = document.querySelector(".hud");
const hudBody = document.querySelector(".hud-body");
const iconFace = byId("icon-face");
const normalLayoutSize = { width: 390, height: 500 };
const minWindowScale = 0.72;
const maxWindowScale = 1.5;

function updateUiScale() {
  if (hudRoot.classList.contains("icon-mode")) return;
  // 使用未变换的自然尺寸，套餐附加行出现后也要重新计算高度。
  const baseLayoutWidth = hudBody.offsetWidth;
  const baseLayoutHeight = hudBody.offsetHeight;
  if (!baseLayoutWidth || !baseLayoutHeight) return;
  const rootStyle = getComputedStyle(hudRoot);
  const contentWidth = hudRoot.clientWidth - parseFloat(rootStyle.paddingLeft) - parseFloat(rootStyle.paddingRight);
  const contentHeight = hudRoot.clientHeight - parseFloat(rootStyle.paddingTop) - parseFloat(rootStyle.paddingBottom);
  const widthScale = contentWidth / baseLayoutWidth;
  const heightScale = contentHeight / baseLayoutHeight;
  // 窗口有最小尺寸，但内容必须始终以实际可用空间为准，不能强制放大。
  const scale = Math.max(0, Math.min(maxWindowScale, widthScale, heightScale));
  // 按高度缩放时，把多出的水平空间平均分到两侧。
  const offsetX = Math.max(0, (contentWidth - baseLayoutWidth * scale) / 2);
  hudBody.style.transform = `translateX(${offsetX}px) scale(${scale})`;
}

const formatTokens = (value) => {
  const number = Number(value || 0);
  if (number >= 1000000) return `${(number / 1000000).toFixed(1)}M`;
  if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 1 : 2)}k`;
  return number.toLocaleString("zh-CN");
};
const formatExactTokens = (value) => Number(value || 0).toLocaleString("zh-CN");
const formatUsd = (usage) => {
  if (!usage || usage.cost_available !== true || !Number.isFinite(Number(usage.cost_usd))) return "—";
  const value = Number(usage.cost_usd);
  return `$${value < 0.01 ? value.toFixed(4) : value.toFixed(2)}`;
};
const formatEstimatedCost = (usage) => `${usage?.cost_approximate ? "≈ API 估算" : "API 估算"} ${formatUsd(usage)}`;
const planNames = {
  free: "免费",
  go: "Go",
  plus: "Plus",
  pro: "Pro",
  prolite: "Pro Lite",
  team: "Team",
  business: "Business",
  enterprise: "Enterprise",
  edu: "教育",
  unknown: "未知套餐",
};

function formatWindowLabel(minutes) {
  const value = Number(minutes);
  if (!Number.isFinite(value) || value <= 0) return "套餐窗口";
  if (value >= 43200) return `约 ${Math.round(value / 43200)} 个月`;
  if (value >= 10080) return "每周";
  if (value >= 1440) return "每日";
  if (value >= 60) return `每 ${Math.round(value / 60)} 小时`;
  return `每 ${Math.round(value)} 分钟`;
}

function formatResetTime(timestamp) {
  const seconds = Number(timestamp);
  if (!Number.isFinite(seconds) || seconds <= 0) return "重置时间 —";
  const resetAt = new Date(seconds * 1000);
  if (Number.isNaN(resetAt.getTime())) return "重置时间 —";
  const date = resetAt.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  const time = resetAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return `重置 ${date} ${time}`;
}

function renderQuota(planUsage) {
  const quota = planUsage || {};
  const available = quota.available === true;
  const planType = quota.plan_type || "unknown";
  const planLabel = available ? (planNames[planType] || planType) : "—";
  const primary = quota.primary || null;
  const secondary = quota.secondary || null;
  const primaryRemaining = primary && Number.isFinite(Number(primary.remaining_percent))
    ? Math.max(0, Math.min(100, Number(primary.remaining_percent)))
    : null;
  const secondaryRemaining = secondary && Number.isFinite(Number(secondary.remaining_percent))
    ? Math.max(0, Math.min(100, Number(secondary.remaining_percent)))
    : null;

  byId("plan-type").textContent = planLabel;
  byId("quota-primary-label").textContent = primary ? `${formatWindowLabel(primary.window_minutes)}剩余` : (available ? "套餐窗口" : "等待数据");
  byId("quota-primary").textContent = primaryRemaining === null ? "—" : `${Math.round(primaryRemaining)}%`;
  byId("quota-primary-reset").textContent = formatResetTime(primary?.resets_at);

  const secondaryVisible = secondaryRemaining !== null;
  byId("quota-secondary").classList.toggle("is-hidden", !secondaryVisible);
  byId("quota-secondary-reset").classList.toggle("is-hidden", !secondaryVisible);
  if (secondaryVisible) {
    byId("quota-secondary-label").textContent = `${formatWindowLabel(secondary.window_minutes)}剩余`;
    byId("quota-secondary-value").textContent = `${Math.round(secondaryRemaining)}%`;
    byId("quota-secondary-reset").textContent = formatResetTime(secondary.resets_at);
  }

  const credits = quota.credits || null;
  const creditText = credits?.unlimited
    ? "额外额度不限"
    : credits?.has_credits
      ? `额外额度 ${credits.balance || "可用"}`
      : "";
  byId("quota-credits").textContent = creditText;
  byId("quota-credits").classList.toggle("is-hidden", !creditText);
  byId("quota-status").textContent = available
    ? (quota.stale ? "套餐数据暂时未刷新" : "来自 Codex 登录状态")
    : (quota.message || "等待套餐数据");
}

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
  byId("current-cost").textContent = formatEstimatedCost(current);
}

function renderPeriod(usage) {
  const current = usage || {};
  const total = Number(current.input_tokens || 0) + Number(current.output_tokens || 0);
  byId("period-total").textContent = formatTokens(total);
  byId("period-cache").textContent = `缓存命中 ${Math.round(Number(current.input_cache_hit_rate || 0) * 100)}%`;
  byId("period-cost").textContent = formatEstimatedCost(current);
}

function render(state) {
  const current = state.current || {};
  renderUsage(current);
  renderPeriod(state[period]);
  renderQuota(state.plan_usage);
  byId("model").textContent = current.model || "等待 Codex";
  byId("turn").textContent = current.turn_id || "—";
  byId("message").textContent = state.message || "等待 usage 数据";
  byId("updated").textContent = state.updated_at ? state.updated_at.slice(11, 19) : "—";
  byId("connection").classList.add("ready");
  byId("connection").lastChild.textContent = " 已连接";
  updateUiScale();
}

let lastCollectorCheck = 0;
let lastCollectorReady = false;
async function ensureCollector(invoke) {
  const now = Date.now();
  if (now - lastCollectorCheck < 5000) return lastCollectorReady;
  lastCollectorCheck = now;
  try {
    await invoke("ensure_collector");
    lastCollectorReady = true;
  } catch {
    lastCollectorReady = false;
  }
  return lastCollectorReady;
}

async function refresh() {
  try {
    const invoke = window.__TAURI__?.core?.invoke;
    if (invoke) {
      const collectorReady = await ensureCollector(invoke);
      const state = JSON.parse(await invoke("read_state"));
      render(state);
      byId("connection").classList.toggle("ready", collectorReady);
      byId("connection").lastChild.textContent = collectorReady ? " 已连接" : " 等待采集器";
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

let normalWindowSize = { width: window.innerWidth, height: window.innerHeight };
let windowTransitionBusy = false;
const invokeNative = () => window.__TAURI__?.core?.invoke;

async function minimizeToIcon(event) {
  event.stopPropagation();
  const invoke = invokeNative();
  if (!invoke || windowTransitionBusy) return;
  windowTransitionBusy = true;
  normalWindowSize = { width: window.innerWidth, height: window.innerHeight };
  hudRoot.classList.add("icon-mode");
  try {
    await invoke("minimize_to_icon");
  } catch {
    hudRoot.classList.remove("icon-mode");
    updateUiScale();
  } finally {
    windowTransitionBusy = false;
  }
}

async function minimizeToTray(event) {
  event.stopPropagation();
  const invoke = invokeNative();
  if (!invoke) return;
  await invoke("minimize_to_tray");
}

async function restoreFromIcon(event) {
  event.stopPropagation();
  const invoke = invokeNative();
  if (!invoke || windowTransitionBusy) return;
  windowTransitionBusy = true;
  try {
    await invoke("restore_window", normalWindowSize);
    hudRoot.classList.remove("icon-mode");
    updateUiScale();
  } catch {
    // 原生窗口恢复失败时保留图标，允许再次点击重试。
  } finally {
    windowTransitionBusy = false;
  }
}

byId("minimize").addEventListener("click", minimizeToIcon);
byId("tray").addEventListener("click", minimizeToTray);
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
  if (event.button !== 0 || windowTransitionBusy) return;
  event.preventDefault();
  event.stopPropagation();
  resizeHandle.setPointerCapture(event.pointerId);
  resizeState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    width: window.innerWidth,
    height: window.innerHeight,
  };
});

resizeHandle.addEventListener("pointermove", async (event) => {
  if (!resizeState || resizeBusy || event.pointerId !== resizeState.pointerId) return;
  const scaleX = (resizeState.width + event.clientX - resizeState.startX) / normalLayoutSize.width;
  const scaleY = (resizeState.height + event.clientY - resizeState.startY) / normalLayoutSize.height;
  // 相对固定基准限制尺寸，避免多次拖动突破上下限。
  const scale = Math.max(minWindowScale, Math.min(maxWindowScale, scaleX, scaleY));
  const width = Math.round(normalLayoutSize.width * scale);
  const height = Math.round(normalLayoutSize.height * scale);
  const invoke = invokeNative();
  if (!invoke) return;
  resizeBusy = true;
  try {
    await invoke("resize_window", { width, height });
  } catch {
    resizeState = null;
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
resizeHandle.addEventListener("pointercancel", () => { resizeState = null; });
resizeHandle.addEventListener("lostpointercapture", () => { resizeState = null; });

refresh();
setInterval(refresh, 1000);
window.addEventListener("resize", updateUiScale);
updateUiScale();
