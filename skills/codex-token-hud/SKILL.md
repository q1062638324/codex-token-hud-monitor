---
name: codex-token-hud
description: 启动、检查和解释 Codex Token HUD，显示当前任务与本地日周 token、缓存输入输出命中情况。
---

# Codex Token HUD

当用户要求查看 Codex token、缓存命中率或本地日周累计时：

1. 运行 `python "$PLUGIN_ROOT/scripts/hudctl.py" ensure` 启动本地采集器。
2. 若用户使用 `codex exec`，优先通过 `scripts/run-codex.ps1` 运行以获得精确的 `turn.completed.usage`。
3. 将 `http://127.0.0.1:38427/v1/state` 返回的数值解释为本地采集累计，不要称为账户剩余额度。
4. `uncached_input_tokens` 等于 input 减 cached input，不能小于零。
5. 如果没有 `cached_output_tokens` 字段，明确说明当前数据源没有提供输出缓存数据。

代码、文档和说明使用中文，技术术语保留英文。
