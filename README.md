# Codex Token HUD Monitor

Codex 的 Windows 透明桌面 HUD，显示当前任务和本地采集的每日、每周 token 使用量。

![Codex Token HUD Monitor](assets/hud-screenshot.png)

## 项目优势

- **实时可见**：直接读取 Codex Desktop session 的 usage 事件，当前任务、输入、输出和 reasoning 一目了然。
- **缓存透明**：拆分显示 cached input、uncached input 和 input cache hit rate，方便判断上下文复用效果。
- **日周统计**：按本机时区累计今日与本周 token，支持跨任务查看本机使用趋势。
- **桌面友好**：透明置顶、右下角启动、顶部拖动、等比例缩放、普通缩小与系统托盘收纳，并提供退出按钮。
- **本地优先**：采集器只监听 `127.0.0.1`，不上传 prompt、session 内容或 token 数据。
- **开箱下载**：GitHub Release 同时提供 Windows 安装包和 Codex 插件 ZIP。

## 下载与安装

项目通过 GitHub Private Repository 发布，只有仓库所有者和被邀请的协作者可以下载 Release。

每个 Release 提供两类文件：

- `*.msi` 或 `*-setup.exe`：Windows 桌面 HUD 安装包。
- `codex-token-hud-monitor-plugin-*.zip`：完整 Codex 插件包。

安装步骤：

1. 下载并运行 Windows 安装包。
2. 下载插件压缩包，将其中的插件目录安装到 Codex 的个人插件目录。
3. 重启 Codex Desktop，`SessionStart` hook 会自动启动本地采集器。
4. HUD 会在桌面右下角启动，可拖动、缩放、缩小为桌面图标。

仓库打 tag 后，GitHub Actions 会自动构建 Windows 安装包和插件压缩包，并创建 Draft Release。

## 已实现

- 当前任务的 input、cached input、uncached input、output、reasoning output。
- 输入缓存命中率和输出缓存字段（数据源提供时显示）。
- 本地每日、每周累计，按本机时区切分。
- `codex exec --json` 的 `turn.completed.usage` 采集。
- Codex 桌面端 session 的 `last_token_usage` 和 `total_token_usage` 采集。
- OTLP JSON 与 OTLP HTTP Protobuf 的基础采集入口。
- Tauri 透明、置顶、无边框窗口。
- 启动时定位到主显示器右下角，拖动顶部标题栏即可移动。
- 拖动右下角缩放手柄可等比例调整窗口大小。

## 目录

- `app/`：Tauri HUD 桌面应用。
- `scripts/hudctl.py`：本地状态服务与 usage 采集器。
- `hudctl.py` 会读取 Codex 的 `state_5.sqlite` 找到 session JSONL，并跟踪其中的 `token_count` 事件。
- `scripts/run-codex.ps1`：通过 `codex exec --json` 运行并转发 usage。
- `hooks/hooks.json`：Codex 会话启动时启动本地采集服务。

## 开发运行

在插件目录执行：

```powershell
python .\scripts\hudctl.py ensure
cargo run --manifest-path .\app\src-tauri\Cargo.toml
```

若只想测试采集器：

```powershell
Get-Content .\tests\sample-turn.jsonl | python .\scripts\hudctl.py ingest
```

## Codex JSONL 包装运行

```powershell
.\scripts\run-codex.ps1 -Prompt "总结当前仓库"
```

该脚本会把 Codex 输出原样写到 stdout，并把 `turn.completed` 事件发送到本地 HUD。

## OTel 接入

Codex 当前版本的 OTel 配置字段和传输格式可能随版本变化，建议先按当前版本的官方配置启用本地 OTLP exporter，再将 endpoint 指向：

```text
http://127.0.0.1:38427/v1/ingest
```

采集器只接受 localhost，不会把 prompt 或 token 发到外部服务。

## 说明

每日和每周是本机采集累计，不等同于账户后台额度。账户级剩余额度需要 Codex 当前版本公开 usage 数据后才能安全接入。
