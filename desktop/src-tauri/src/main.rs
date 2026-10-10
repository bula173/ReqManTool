//! ReqManTool Desktop App - Local Edition
//! 
//! Bundles the Python backend and serves the frontend in a native window.
//! Manages the backend lifecycle, auto-update, and data directory.

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, Result};
use dirs;
use portpicker;
use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, Manager, State, Window, WindowEvent,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    webview::Url,
    App, RunEvent,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_http::reqwest::Client;
use tauri_plugin_updater::UpdaterExt;

// State for managing the backend process
struct BackendState {
    process: Arc<Mutex<Option<std::process::Child>>>,
    port: Arc<Mutex<Option<u16>>>,
    data_dir: Arc<Mutex<Option<PathBuf>>>,
    base_url: Arc<Mutex<Option<String>>>,
}

impl Default for BackendState {
    fn default() -> Self {
        Self {
            process: Arc::new(Mutex::new(None)),
            port: Arc::new(Mutex::new(None)),
            data_dir: Arc::new(Mutex::new(None)),
            base_url: Arc::new(Mutex::new(None)),
        }
    }
}

// Configuration file structure
#[derive(Debug, Serialize, Deserialize)]
#[serde(default)]
struct DesktopConfig {
    data_dir: Option<String>,
    last_port: Option<u16>,
    window_state: Option<WindowState>,
    auto_update: bool,
    check_updates_on_start: bool,
    /// `"local"` (the bundled backend) or `"server"` (an address the window
    /// loads); `None` means the user has not decided (yet).
    mode: Option<String>,
    /// The last server address the user entered, offered again on the start screen.
    server_url: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct WindowState {
    width: f64,
    height: f64,
    x: Option<f64>,
    y: Option<f64>,
    maximized: bool,
}

impl Default for DesktopConfig {
    fn default() -> Self {
        Self {
            data_dir: None,
            last_port: None,
            window_state: None,
            auto_update: true,
            check_updates_on_start: true,
            mode: None,
            server_url: None,
        }
    }
}

/// What the frontend needs to render: which mode the app runs in, the server
/// address to offer on the start screen and, in local mode, the URL of the
/// backend the Rust side started.
#[derive(Debug, Serialize)]
struct AppState {
    mode: Option<String>,
    server_url: Option<String>,
    base_url: Option<String>,
    needs_choice: bool,
}

// Backend health check response
#[derive(Debug, Deserialize)]
struct EditionResponse {
    mode: String,
}

// Never flash a console window: the app is a GUI process, so on Windows every
// child (python, git, their probes) would otherwise get a window of its own.
fn silent(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd
}

// Git availability check
async fn check_git_available() -> bool {
    let mut command = Command::new("git");
    silent(&mut command)
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

// Find Python executable
fn find_python() -> Option<PathBuf> {
    // Try common Python commands
    for cmd in ["python3", "python", "py"] {
        let mut command = Command::new(cmd);
        if let Ok(output) = silent(&mut command).arg("--version").output() {
            if output.status.success() {
                return Some(PathBuf::from(cmd));
            }
        }
    }
    None
}

// Get the backend directory (bundled with the app)
fn get_backend_dir(app: &AppHandle) -> Result<PathBuf> {
    let resource_dir = app.path().resource_dir()?;
    Ok(resource_dir.join("backend"))
}

// Get the data directory
fn get_data_dir_from_config(config: &DesktopConfig) -> PathBuf {
    if let Some(dir) = &config.data_dir {
        PathBuf::from(dir)
    } else {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("ReqManData")
    }
}

/// The app's own directory in the user config dir: config file, backend log
/// and backend pid file all live there.
fn app_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .config_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("ReqManTool")
}

fn backend_log_path(app: &AppHandle) -> PathBuf {
    app_dir(app).join("backend.log")
}

fn backend_pid_path(app: &AppHandle) -> PathBuf {
    app_dir(app).join("backend.pid")
}

/// A field of a running process, empty when it is gone: `ps` details on Unix,
/// the image name on Windows (`tasklist`; a start time would need PowerShell).
fn process_identity(pid: u32) -> String {
    #[cfg(unix)]
    {
        process_field(pid, "lstart=")
    }
    #[cfg(windows)]
    {
        let output = silent(Command::new("tasklist"))
            .args(["/FI", &format!("PID eq {}", pid), "/FO", "CSV", "/NH"])
            .output();
        let line = output
            .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
            .unwrap_or_default();
        // `"python.exe","1234","Console","1","12,345 K"` -> `python`
        line.split(',')
            .next()
            .map(|name| name.trim_matches('"').to_lowercase())
            .and_then(|name| name.strip_suffix(".exe").map(str::to_string))
            .unwrap_or_default()
    }
    #[cfg(not(any(unix, windows)))]
    {
        String::new()
    }
}

/// A field of a process from `ps`, empty when the process is gone.
#[cfg(unix)]
fn process_field(pid: u32, field: &str) -> String {
    Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", field])
        .output()
        .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .unwrap_or_default()
}

/// Notes the backend just started, so a later launch can clean it up: the app
/// can be killed (a `tauri dev` restart, a crash, a logout) without its child
/// noticing.
fn remember_backend_pid(app: &AppHandle, pid: u32) {
    let identity = process_identity(pid);
    if identity.is_empty() {
        return;
    }
    let _ = std::fs::write(backend_pid_path(app), format!("{}\n{}\n", pid, identity));
}

/// Kills the backend a previous instance left behind, if it is still around.
/// The pid alone is not enough — it may have been reused in the meantime — so
/// the recorded identity must still match: its start time on Unix (and a
/// `reqman` command line as well), the `python` image name on Windows.
fn reap_stale_backend(app: &AppHandle) {
    let path = backend_pid_path(app);
    let Ok(content) = std::fs::read_to_string(&path) else {
        return;
    };
    let _ = std::fs::remove_file(&path);
    let mut lines = content.lines();
    let (Some(pid), Some(identity)) = (lines.next(), lines.next()) else {
        return;
    };
    let Ok(pid) = pid.trim().parse::<u32>() else {
        return;
    };
    if pid <= 1 || pid == std::process::id() {
        return;
    }
    if process_identity(pid) != identity.trim() {
        return;
    }
    #[cfg(unix)]
    if !process_field(pid, "command=").contains("reqman") {
        return;
    }
    #[cfg(unix)]
    let _ = Command::new("kill").arg("-KILL").arg(pid.to_string()).status();
    // /T: take the whole tree down, the backend has children of its own.
    #[cfg(windows)]
    let _ = silent(Command::new("taskkill"))
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .status();
}

// Start the Python backend
async fn start_backend(app: &AppHandle, state: State<'_, BackendState>, config: &DesktopConfig) -> Result<u16> {
    // Leftovers of a previous run first (the pid file records that one), then
    // anything of ours — a previous mode, an earlier start — that is in the way.
    reap_stale_backend(app);
    let _ = stop_backend(app, state.clone()).await;

    let port = portpicker::pick_unused_port()
        .context("Failed to find free port")?;

    let backend_dir = get_backend_dir(app)?;
    let data_dir = get_data_dir_from_config(config);
    let python = find_python().context("Python not found. Please install Python 3.11+")?;

    // Ensure data directory exists
    std::fs::create_dir_all(&data_dir)?;

    // On Windows, a virtualenv next to the bundled backend is preferred
    #[cfg(target_os = "windows")]
    let python = {
        let venv_python = backend_dir.join(".venv").join("Scripts").join("python.exe");
        if venv_python.exists() {
            venv_python
        } else {
            python
        }
    };

    // The backend logs to a file. A pipe that nobody reads would fill up and
    // block it once enough requests have been served, and a log is what you
    // want when it does not come up (see the timeout error below).
    let log_path = backend_log_path(app);
    if let Some(parent) = log_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .with_context(|| format!("Failed to open {}", log_path.display()))?;

    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("reqman")
        .arg("serve")
        .arg("--dir")
        .arg(&data_dir)
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        // The Vite dev server the window loads in development, and the
        // window's own origin in a bundle: `tauri://localhost` on macOS,
        // `http://tauri.localhost` on Windows and Linux. Without the right
        // origin every API call of the built app would be refused by CORS.
        .arg("--cors-origins")
        .arg("http://localhost:5173,tauri://localhost,http://tauri.localhost")
        .current_dir(&backend_dir)
        .stdout(log.try_clone().context("Failed to clone the log handle")?)
        .stderr(log);

    let child = silent(&mut cmd).spawn().context("Failed to start backend")?;
    remember_backend_pid(app, child.id());

    // Store process handle
    {
        let mut process_lock = state.process.lock().unwrap();
        *process_lock = Some(child);
    }

    // Store port and data dir
    {
        let mut port_lock = state.port.lock().unwrap();
        *port_lock = Some(port);
    }
    {
        let mut data_dir_lock = state.data_dir.lock().unwrap();
        *data_dir_lock = Some(data_dir.clone());
    }
    {
        let mut base_url_lock = state.base_url.lock().unwrap();
        *base_url_lock = Some(format!("http://127.0.0.1:{}", port));
    }

    // Wait for backend to be ready
    let base_url = format!("http://127.0.0.1:{}", port);
    let client = Client::new();

    for _ in 0..30 {
        tokio::time::sleep(Duration::from_millis(500)).await;

        if let Ok(response) = client.get(&format!("{}/edition", base_url)).send().await {
            if response.status().is_success() {
                if let Ok(edition) = response.json::<EditionResponse>().await {
                    if edition.mode == "local" {
                        return Ok(port);
                    }
                }
            }
        }
    }

    Err(anyhow::anyhow!(
        "Backend failed to start within 15s, see {}",
        log_path.display()
    ))
}

// Stop the Python backend
async fn stop_backend(app: &AppHandle, state: State<'_, BackendState>) -> Result<()> {
    let mut process_lock = state.process.lock().unwrap();
    let child = process_lock.take();
    drop(process_lock);

    if let Some(mut child) = child {
        // Try graceful shutdown first
        #[cfg(unix)]
        {
            let _ = child.kill();
        }
        #[cfg(windows)]
        {
            child.kill()?;
        }
        let _ = child.wait();
        // Only the pid file of the child we just reaped is ours to drop.
        let _ = std::fs::remove_file(backend_pid_path(app));
    }
    *state.port.lock().unwrap() = None;
    *state.base_url.lock().unwrap() = None;
    Ok(())
}

// Load config from disk
fn load_config(app: &AppHandle) -> DesktopConfig {
    let config_file = app_dir(app).join("config.json");

    if config_file.exists() {
        if let Ok(content) = std::fs::read_to_string(&config_file) {
            if let Ok(config) = serde_json::from_str::<DesktopConfig>(&content) {
                return config;
            }
        }
    }
    DesktopConfig::default()
}

// Save config to disk
fn save_config(app: &AppHandle, config: &DesktopConfig) -> Result<()> {
    let config_file = app_dir(app).join("config.json");

    std::fs::create_dir_all(config_file.parent().unwrap())?;
    std::fs::write(config_file, serde_json::to_string_pretty(config)?)?;
    Ok(())
}

/// What the frontend needs to know: the mode, the server address to offer on
/// the start screen and, in local mode, the URL of the running backend.
fn app_state(app: &AppHandle) -> AppState {
    let config = load_config(app);
    let base_url = app
        .state::<BackendState>()
        .base_url
        .lock()
        .ok()
        .and_then(|base_url| base_url.clone());
    AppState {
        needs_choice: config.mode.is_none(),
        mode: config.mode,
        server_url: config.server_url,
        base_url,
    }
}

/// The URL of the app's own UI: the Vite dev server in a dev build, the
/// embedded assets (Tauri's own protocol) in a bundle.
fn app_ui_url(app: &AppHandle) -> String {
    if cfg!(debug_assertions) {
        if let Some(dev_url) = app.config().build.dev_url.clone() {
            return dev_url.to_string();
        }
    }
    if cfg!(any(target_os = "macos", target_os = "ios")) {
        "tauri://localhost".to_string()
    } else {
        "http://tauri.localhost".to_string()
    }
}

/// Points the window at a URL: the app's own UI or a server the user picked.
fn load_url(app: &AppHandle, url: &str) {
    match url.parse::<Url>() {
        Ok(parsed) => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.navigate(parsed);
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        Err(e) => eprintln!("Not a usable URL ({}): {}", url, e),
    }
}

/// The window is created hidden (tauri.conf.json `visible: false`) so the
/// saved geometry can be restored without a flash; show it once the app is
/// ready for the user.
fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Local mode: ask where the data lives on the very first run, then start the
/// bundled backend and show the window once it answers.
fn start_local(app: &AppHandle) {
    let config = load_config(app);
    if config.data_dir.is_some() {
        run_local_backend(app.clone());
        return;
    }
    let app = app.clone();
    app.dialog().file().pick_folder(move |folder| {
        if let Some(folder) = folder {
            let mut config = load_config(&app);
            config.data_dir = Some(folder.to_string());
            let _ = save_config(&app, &config);
        }
        run_local_backend(app.clone());
    });
}

fn run_local_backend(app: AppHandle) {
    let config = load_config(&app);
    tauri::async_runtime::spawn(async move {
        let state = app.state::<BackendState>();
        match start_backend(&app, state, &config).await {
            Ok(port) => {
                let mut config = load_config(&app);
                config.last_port = Some(port);
                let _ = save_config(&app, &config);
            }
            Err(e) => eprintln!("Failed to start backend: {}", e),
        }
        show_main_window(&app);
    });
}

/// Remembers the choice from the tray / app menu and brings the window back to
/// the app's own UI — the start screen when the user asked for a server
/// address again (`mode` cleared, the last address kept on offer).
fn switch_mode(app: &AppHandle, mode: Option<&str>) {
    {
        let mut config = load_config(app);
        config.mode = mode.map(str::to_string);
        let _ = save_config(app, &config);
    }
    let app = app.clone();
    let target = mode.unwrap_or_default().to_string();
    tauri::async_runtime::spawn(async move {
        if target != "local" {
            let state = app.state::<BackendState>();
            let _ = stop_backend(&app, state).await;
        }
        load_url(&app, &app_ui_url(&app));
        if target == "local" {
            start_local(&app);
        }
    });
}

// Create system tray menu
fn create_tray_menu(app: &AppHandle) -> Result<Menu<tauri::Wry>> {
    let show = MenuItem::with_id(app, "show", "Show ReqManTool", true, None::<&str>)?;
    let local = MenuItem::with_id(app, "local", "Local instance", true, None::<&str>)?;
    let server = MenuItem::with_id(app, "server", "Server address…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &local, &server, &quit])?;
    Ok(menu)
}

// Setup function
fn setup(app: &mut App) -> Result<(), Box<dyn std::error::Error>> {
    let config = load_config(app.handle());

    // Restore window state (saved in physical pixels)
    if let Some(window_state) = &config.window_state {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.set_size(tauri::Size::Physical(tauri::PhysicalSize {
                width: window_state.width as u32,
                height: window_state.height as u32,
            }));
            if let (Some(x), Some(y)) = (window_state.x, window_state.y) {
                let _ = window.set_position(tauri::Position::Physical(tauri::PhysicalPosition {
                    x: x as i32,
                    y: y as i32,
                }));
            }
            if window_state.maximized {
                let _ = window.maximize();
            }
        }
    }

    // Create tray icon
    let tray_menu = create_tray_menu(app.handle())?;
    TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&tray_menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_main_window(app),
            "local" => switch_mode(app, Some("local")),
            // Back to the start screen; the last server address stays on offer.
            "server" => switch_mode(app, None),
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                if let Some(window) = tray.app_handle().get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        })
        .build(app)?;

    // What the window shows: a remembered server, the local instance, or the
    // question itself. A dev build skips the question (it is always local).
    match config.mode.as_deref() {
        Some("server") => {
            let url = config
                .server_url
                .clone()
                .unwrap_or_else(|| app_ui_url(app.handle()));
            load_url(app.handle(), &url);
        }
        Some("local") => start_local(app.handle()),
        _ if cfg!(debug_assertions) => start_local(app.handle()),
        _ => {
            let app_handle = app.handle().clone();
            app_handle
                .dialog()
                .message(
                    "Start a local ReqManTool instance on this machine?\n\n\
                     Pick No to connect to an existing ReqManTool server instead.",
                )
                .title("ReqManTool")
                .buttons(MessageDialogButtons::YesNo)
                .show(move |local| {
                    if local {
                        let mut config = load_config(&app_handle);
                        config.mode = Some("local".to_string());
                        let _ = save_config(&app_handle, &config);
                        start_local(&app_handle);
                    } else {
                        // No mode is remembered: the start screen in the
                        // window asks for the server address.
                        show_main_window(&app_handle);
                    }
                });
        }
    }

    // Check for updates on start
    if config.check_updates_on_start {
        let app_handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            let _ = check_for_updates(&app_handle).await;
        });
    }

    // The window stays hidden until the branch above is done with it: it is
    // shown when the backend answers (local), when the server URL is loaded
    // (server), or after the first-run question (start screen).
    Ok(())
}

// Check for updates
async fn check_for_updates(app: &AppHandle) -> Result<()> {
    if let Some(update) = app.updater()?.check().await? {
        let _ = update.download_and_install(
            |_chunk_length, _content_length| {},
            || {}
        ).await;
    }
    Ok(())
}

// Window event handler
fn on_window_event(window: &Window, event: &WindowEvent) {
    if let WindowEvent::CloseRequested { api, .. } = event {
        // Hide window instead of closing
        window.hide().unwrap();
        api.prevent_close();
    }
}

// Save window state on close
fn save_window_state(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let mut config = load_config(app);
        let size = window.inner_size().unwrap_or_default();
        let position = window.outer_position().ok();
        let maximized = window.is_maximized().unwrap_or(false);
        
        config.window_state = Some(WindowState {
            width: size.width as f64,
            height: size.height as f64,
            x: position.map(|p| p.x as f64),
            y: position.map(|p| p.y as f64),
            maximized,
        });
        let _ = save_config(app, &config);
    }
}

// Tauri commands
#[tauri::command]
async fn get_base_url(state: State<'_, BackendState>) -> Result<String, String> {
    let base_url_lock = state.base_url.lock().unwrap();
    base_url_lock.clone().ok_or_else(|| "Backend not started".to_string())
}

#[tauri::command]
async fn get_data_dir(state: State<'_, BackendState>) -> Result<String, String> {
    let data_dir_lock = state.data_dir.lock().unwrap();
    data_dir_lock
        .as_ref()
        .map(|p| p.to_string_lossy().to_string())
        .ok_or_else(|| "Data directory not set".to_string())
}

#[tauri::command]
async fn set_data_dir(app: AppHandle, state: State<'_, BackendState>, new_dir: String) -> Result<(), String> {
    let new_path = PathBuf::from(new_dir);
    std::fs::create_dir_all(&new_path).map_err(|e| e.to_string())?;
    
    // Stop current backend
    stop_backend(&app, state.clone()).await.map_err(|e| e.to_string())?;
    
    // Update config
    let mut config = load_config(&app);
    config.data_dir = Some(new_path.to_string_lossy().to_string());
    save_config(&app, &config).map_err(|e| e.to_string())?;
    
    // Start new backend
    let config = load_config(&app);
    start_backend(&app, state, &config).await.map_err(|e| e.to_string())?;
    
    Ok(())
}

#[tauri::command]
async fn check_git() -> Result<bool, String> {
    Ok(check_git_available().await)
}

#[tauri::command]
async fn restart_backend(app: AppHandle, state: State<'_, BackendState>) -> Result<(), String> {
    stop_backend(&app, state.clone()).await.map_err(|e| e.to_string())?;
    let config = load_config(&app);
    start_backend(&app, state, &config).await.map_err(|e| e.to_string())?;
    Ok(())
}

/// The startup state the window is rendered from: the mode, the server address
/// to offer on the start screen and, in local mode, the running backend's URL.
#[tauri::command]
async fn get_app_state(app: AppHandle) -> Result<AppState, String> {
    Ok(app_state(&app))
}

/// The start screen's choice. `local` starts the bundled backend (the app UI
/// is already showing there); `server` stores the address and moves the window
/// to it — that server serves its own UI, so no local backend runs in that mode.
#[tauri::command]
async fn set_app_mode(
    app: AppHandle,
    state: State<'_, BackendState>,
    mode: String,
    server_url: Option<String>,
) -> Result<AppState, String> {
    let server_url = server_url
        .map(|url| url.trim().to_string())
        .filter(|url| !url.is_empty());

    match mode.as_str() {
        "local" => {
            {
                let mut config = load_config(&app);
                config.mode = Some("local".to_string());
                save_config(&app, &config).map_err(|e| e.to_string())?;
            }
            let config = load_config(&app);
            let port = start_backend(&app, state, &config)
                .await
                .map_err(|e| e.to_string())?;
            let mut config = load_config(&app);
            config.last_port = Some(port);
            let _ = save_config(&app, &config);
        }
        "server" => {
            let url = server_url.ok_or_else(|| "A server address is required".to_string())?;
            {
                let mut config = load_config(&app);
                config.mode = Some("server".to_string());
                config.server_url = Some(url.clone());
                save_config(&app, &config).map_err(|e| e.to_string())?;
            }
            stop_backend(&app, state).await.map_err(|e| e.to_string())?;
            load_url(&app, &url);
        }
        other => return Err(format!("Unknown mode: {}", other)),
    }

    Ok(app_state(&app))
}

#[tauri::command]
async fn check_updates(app: AppHandle) -> Result<bool, String> {
    match app.updater() {
        Ok(updater) => {
            match updater.check().await {
                Ok(Some(_)) => Ok(true),
                Ok(None) => Ok(false),
                Err(e) => Err(e.to_string()),
            }
        }
        Err(e) => Err(e.to_string()),
    }
}

fn main() {
    // `--version` for CI and packagers: answer and exit before the GUI starts.
    // The write result is ignored — a GUI process on Windows has no stdout.
    if std::env::args().any(|arg| arg == "--version" || arg == "-V") {
        let version = format!("{} {}\n", env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION"));
        let _ = std::io::Write::write_all(&mut std::io::stdout(), version.as_bytes());
        return;
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_http::init())
        .manage(BackendState::default())
        .setup(|app| Ok(setup(app)?))
        .on_window_event(on_window_event)
        .invoke_handler(tauri::generate_handler![
            get_base_url,
            get_data_dir,
            set_data_dir,
            check_git,
            restart_backend,
            check_updates,
            get_app_state,
            set_app_mode
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            match event {
                RunEvent::ExitRequested { api, .. } => {
                    save_window_state(app_handle);
                    api.prevent_exit();
                }
                RunEvent::Exit => {
                    save_window_state(app_handle);
                    // Stop backend
                    let state = app_handle.state::<BackendState>();
                    tauri::async_runtime::block_on(async {
                        let _ = stop_backend(app_handle, state).await;
                    });
                }
                _ => {}
            }
        });
}