---
name: codex-token-hud
description: 启动、检查和解释 Codex Token HUD，显示当前任务、本地日周 token、缓存输入输出命中情况和套餐剩余用量。
---

# Codex Token HUD

当用户要求查看 Codex token、缓存命中率、本地日周累计或当前套餐余量时：

1. 运行 `python "$PLUGIN_ROOT/scripts/hudctl.py" ensure` 启动本地采集器。
2. 若用户使用 `codex exec`，优先通过 `scripts/run-codex.ps1` 运行以获得精确的 `turn.completed.usage`。
3. 将 `today` 和 `week` 解释为本地采集累计，不要称为账户剩余额度；将 `plan_usage` 的 `remaining_percent` 解释为 Codex 当前套餐窗口的剩余百分比。
4. `plan_usage` 通过 Codex CLI 的 `app-server` 获取；如果 `available` 为 `false`，说明当前未登录、CLI 不兼容或 Codex 暂时没有返回套餐数据。
5. `uncached_input_tokens` 等于 input 减 cached input，不能小于零。
6. 如果没有 `cached_output_tokens` 字段，明确说明当前数据源没有提供输出缓存数据。

代码、文档和说明使用中文，技术术语保留英文。
