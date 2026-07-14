#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;

#[tauri::command]
fn read_state() -> Result<String, String> {
    let local_app_data = std::env::var_os("LOCALAPPDATA")
        .ok_or_else(|| "找不到 LOCALAPPDATA".to_string())?;
    let path = std::path::PathBuf::from(local_app_data)
        .join("CodexTokenHUD")
        .join("state.json");
    std::fs::read_to_string(path).map_err(|error| error.to_string())
}

#[tauri::command]
fn resize_window(window: tauri::WebviewWindow, width: f64, height: f64) -> Result<(), String> {
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|error| error.to_string())
}

fn clamp_window_position(
    monitor: &tauri::Monitor,
    width: i32,
    height: i32,
    desired_x: i32,
    desired_y: i32,
) -> (i32, i32) {
    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let margin = 12;
    let min_x = monitor_position.x + margin;
    let min_y = monitor_position.y + margin;
    let max_x = (monitor_position.x + monitor_size.width as i32 - width - margin).max(min_x);
    let max_y = (monitor_position.y + monitor_size.height as i32 - height - margin).max(min_y);
    (desired_x.clamp(min_x, max_x), desired_y.clamp(min_y, max_y))
}

// 缩小到图标，并把图标放在原窗口的右下角锚点。
#[tauri::command]
fn minimize_to_icon(window: tauri::WebviewWindow) -> Result<(), String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let current_size = window.outer_size().map_err(|error| error.to_string())?;
    let monitor = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "找不到当前显示器".to_string())?;
    let scale_factor = monitor.scale_factor();
    let icon_width = (48.0 * scale_factor).round() as i32;
    let icon_height = (48.0 * scale_factor).round() as i32;
    let (x, y) = clamp_window_position(
        &monitor,
        icon_width,
        icon_height,
        position.x + current_size.width as i32 - icon_width,
        position.y + current_size.height as i32 - icon_height,
    );
    window
        .set_size(tauri::LogicalSize::new(48.0, 48.0))
        .map_err(|error| error.to_string())?;
    window
        .set_position(tauri::PhysicalPosition::new(x, y))
        .map_err(|error| error.to_string())
}

// 从图标位置向左上恢复，并确保完整窗口仍在当前显示器内。
#[tauri::command]
fn restore_window(window: tauri::WebviewWindow, width: f64, height: f64) -> Result<(), String> {
    let icon_position = window.outer_position().map_err(|error| error.to_string())?;
    let icon_size = window.outer_size().map_err(|error| error.to_string())?;
    let monitor = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "找不到当前显示器".to_string())?;
    let scale_factor = monitor.scale_factor();
    let physical_width = (width * scale_factor).round() as i32;
    let physical_height = (height * scale_factor).round() as i32;
    let (x, y) = clamp_window_position(
        &monitor,
        physical_width,
        physical_height,
        icon_position.x + icon_size.width as i32 - physical_width,
        icon_position.y + icon_size.height as i32 - physical_height,
    );
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|error| error.to_string())?;
    window
        .set_position(tauri::PhysicalPosition::new(x, y))
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn close_window(window: tauri::WebviewWindow) -> Result<(), String> {
    window.close().map_err(|error| error.to_string())
}

// 在用户桌面创建指向当前 HUD 可执行文件的启动快捷方式。
fn create_desktop_shortcut() -> Result<(), String> {
    let target = std::env::current_exe().map_err(|error| error.to_string())?;
    let working_directory = target
        .parent()
        .ok_or_else(|| "找不到 HUD 工作目录".to_string())?;
    let quote = |value: &std::path::Path| value.to_string_lossy().replace('\'', "''");
    let target = quote(&target);
    let working_directory = quote(working_directory);
    let script = format!(
        "$desktop = [Environment]::GetFolderPath('Desktop'); $shortcut = Join-Path $desktop 'Codex Token HUD.lnk'; $shell = New-Object -ComObject WScript.Shell; $link = $shell.CreateShortcut($shortcut); $link.TargetPath = '{}'; $link.WorkingDirectory = '{}'; $link.Description = 'Codex Token HUD'; $link.IconLocation = '{}'; $link.Save()",
        target, working_directory, target
    );
    let status = std::process::Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            &script,
        ])
        .status()
        .map_err(|error| error.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("创建桌面快捷方式失败：{status}"))
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            read_state,
            resize_window,
            minimize_to_icon,
            restore_window,
            close_window
        ])
        .setup(|app| {
            let _ = create_desktop_shortcut();
            if let Some(window) = app.get_webview_window("main") {
                if let Some(monitor) = window.current_monitor()? {
                    let monitor_position = monitor.position();
                    let monitor_size = monitor.size();
                    let window_size = window.outer_size()?;
                    let x = monitor_position.x + monitor_size.width as i32 - window_size.width as i32 - 28;
                    let y = monitor_position.y + monitor_size.height as i32 - window_size.height as i32 - 28;
                    window.set_position(tauri::PhysicalPosition::new(x, y))?;
                }
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("启动 Codex Token HUD 失败");
}
