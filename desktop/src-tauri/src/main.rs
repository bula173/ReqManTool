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
    App, RunEvent,
};
use tauri_plugin_dialog::DialogExt;
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
struct DesktopConfig {
    data_dir: Option<String>,
    last_port: Option<u16>,
    window_state: Option<WindowState>,
    auto_update: bool,
    check_updates_on_start: bool,
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
        }
    }
}

// Backend health check response
#[derive(Debug, Deserialize)]
struct EditionResponse {
    mode: String,
}

// Git availability check
async fn check_git_available() -> bool {
    Command::new("git")
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
        if let Ok(output) = Command::new(cmd).arg("--version").output() {
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

// Start the Python backend
async fn start_backend(app: &AppHandle, state: State<'_, BackendState>, config: &DesktopConfig) -> Result<u16> {
    let port = portpicker::pick_unused_port()
        .context("Failed to find free port")?;
    
    let backend_dir = get_backend_dir(app)?;
    let data_dir = get_data_dir_from_config(config);
    let python = find_python().context("Python not found. Please install Python 3.11+")?;
    
    // Ensure data directory exists
    std::fs::create_dir_all(&data_dir)?;
    
    // Set up environment variables
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
        .arg("--cors-origins")
        .arg("http://localhost:5173,tauri://localhost")
        .current_dir(&backend_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    
    // On Windows, we need to ensure the venv is used
    #[cfg(target_os = "windows")]
    {
        let venv_python = backend_dir.join(".venv").join("Scripts").join("python.exe");
        if venv_python.exists() {
            cmd = Command::new(&venv_python);
            cmd.arg("-m")
                .arg("reqman")
                .arg("serve")
                .arg("--dir")
                .arg(&data_dir)
                .arg("--host")
                .arg("127.0.0.1")
                .arg("--port")
                .arg(port.to_string())
                .arg("--cors-origins")
                .arg("http://localhost:5173,tauri://localhost")
                .current_dir(&backend_dir)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
        }
    }
    
    let child = cmd.spawn().context("Failed to start backend")?;
    
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
    
    Err(anyhow::anyhow!("Backend failed to start within timeout"))
}

// Stop the Python backend
async fn stop_backend(state: State<'_, BackendState>) -> Result<()> {
    let mut process_lock = state.process.lock().unwrap();
    if let Some(mut child) = process_lock.take() {
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
    }
    Ok(())
}

// Load config from disk
fn load_config(app: &AppHandle) -> DesktopConfig {
    let config_dir = app.path().config_dir().unwrap_or_else(|_| PathBuf::from("."));
    let config_file = config_dir.join("ReqManTool").join("config.json");
    
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
    let config_dir = app.path().config_dir().unwrap_or_else(|_| PathBuf::from("."));
    let config_file = config_dir.join("ReqManTool").join("config.json");
    
    std::fs::create_dir_all(config_file.parent().unwrap())?;
    std::fs::write(config_file, serde_json::to_string_pretty(config)?)?;
    Ok(())
}

// Create system tray menu
fn create_tray_menu(app: &AppHandle) -> Result<tauri::menu::Menu<tauri::Wry>> {
    let show = MenuItem::with_id(app, "show", "Show ReqManTool", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &quit])?;
    Ok(menu)
}

// Setup function
fn setup(app: &mut App) -> Result<(), Box<dyn std::error::Error>> {
    let config = load_config(app.handle());
    
    // Restore window state
    if let Some(window_state) = config.window_state {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.set_size(tauri::Size::Logical(tauri::LogicalSize {
                width: window_state.width,
                height: window_state.height,
            }));
            if let (Some(x), Some(y)) = (window_state.x, window_state.y) {
                let _ = window.set_position(tauri::Position::Logical(tauri::LogicalPosition { x, y }));
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
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
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
    
    // First run: ask for data directory
    if config.data_dir.is_none() {
        let app_handle = app.handle().clone();
        let dialog = app_handle.dialog();
        let app_handle_clone = app_handle.clone();
        dialog.file().pick_folder(move |folder| {
            if let Some(folder) = folder {
                let mut config = load_config(&app_handle_clone);
                config.data_dir = Some(folder.to_string());
                let _ = save_config(&app_handle_clone, &config);
            }
            
            // Start backend
            let config = load_config(&app_handle_clone);
            let app_handle_for_async = app_handle_clone.clone();
            tauri::async_runtime::spawn(async move {
                let state = app_handle_for_async.state::<BackendState>();
                if let Err(e) = start_backend(&app_handle_for_async, state, &config).await {
                    eprintln!("Failed to start backend: {}", e);
                }
            });
        });
    } else {
        // Start backend with existing config
        let config = load_config(app.handle());
        let app_handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            let state = app_handle.state::<BackendState>();
            if let Err(e) = start_backend(&app_handle, state, &config).await {
                eprintln!("Failed to start backend: {}", e);
            }
        });
    }
    
    // Check for updates on start
    if config.check_updates_on_start {
        let app_handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            let _ = check_for_updates(&app_handle).await;
        });
    }

    // The window is created hidden (tauri.conf.json `visible: false`) so the
    // saved geometry can be restored without a flash; nothing else ever shows
    // it, so bring it up once setup has run. The tray icon hides/shows it
    // afterwards.
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }

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
    stop_backend(state.clone()).await.map_err(|e| e.to_string())?;
    
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
    stop_backend(state.clone()).await.map_err(|e| e.to_string())?;
    let config = load_config(&app);
    start_backend(&app, state, &config).await.map_err(|e| e.to_string())?;
    Ok(())
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
            check_updates
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
                        let _ = stop_backend(state).await;
                    });
                }
                _ => {}
            }
        });
}