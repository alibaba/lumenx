// LumenX Studio - Tauri 2.0 Main Entry
// Implements: transparent titlebar, Traffic Light, vibrancy, sidecar management, API proxy

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

mod sidecar;
mod menu;

#[derive(Debug, Serialize, Deserialize)]
struct ApiProxyRequest {
    method: String,
    path: String,
    body: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ApiProxyResponse {
    status: u16,
    body: String,
}

/// IPC command: proxy API requests to the Python backend
#[tauri::command]
async fn api_proxy(method: String, path: String, body: Option<String>) -> Result<ApiProxyResponse, String> {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:17177{}", path);

    let request = match method.to_uppercase().as_str() {
        "GET" => client.get(&url),
        "POST" => {
            let mut req = client.post(&url);
            if let Some(ref b) = body {
                req = req.header("Content-Type", "application/json").body(b.clone());
            }
            req
        }
        "PUT" => {
            let mut req = client.put(&url);
            if let Some(ref b) = body {
                req = req.header("Content-Type", "application/json").body(b.clone());
            }
            req
        }
        "DELETE" => client.delete(&url),
        "PATCH" => {
            let mut req = client.patch(&url);
            if let Some(ref b) = body {
                req = req.header("Content-Type", "application/json").body(b.clone());
            }
            req
        }
        _ => return Err(format!("Unsupported HTTP method: {}", method)),
    };

    let response = request.send().await.map_err(|e| e.to_string())?;
    let status = response.status().as_u16();
    let body = response.text().await.map_err(|e| e.to_string())?;

    Ok(ApiProxyResponse { status, body })
}

/// IPC command: check if the Python backend is ready
#[tauri::command]
async fn check_backend_health() -> Result<bool, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .map_err(|e| e.to_string())?;

    match client.get("http://127.0.0.1:17177/health").send().await {
        Ok(resp) => Ok(resp.status().is_success()),
        Err(_) => Ok(false),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend_running = Arc::new(AtomicBool::new(false));
    let backend_running_clone = backend_running.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_dialog::init())
        .on_menu_event(|app, event| {
            let id = event.id().0.as_str();
            // Use webview.eval() for reliable JS execution (bypasses event system)
            if let Some(window) = app.get_webview_window("main") {
                let js = match id {
                    "preferences" => Some("window.location.hash = '#/settings';".to_string()),
                    "new_project" => Some("window.location.hash='#/new-project';".to_string()),
                    "open_project" => Some("window.location.hash = '#/';".to_string()),
                    "zoom_in" => Some("(function(){var s=parseFloat(getComputedStyle(document.documentElement).fontSize);document.documentElement.style.fontSize=(s+1)+'px';})()".to_string()),
                    "zoom_out" => Some("(function(){var s=parseFloat(getComputedStyle(document.documentElement).fontSize);document.documentElement.style.fontSize=Math.max(10,s-1)+'px';})()".to_string()),
                    "zoom_reset" => Some("document.documentElement.style.fontSize='81.25%';".to_string()),
                    _ => None,
                };
                if let Some(code) = js {
                    let _ = window.eval(&code);
                }
            }
            // External links: open in default browser
            match id {
                "docs" => { let _ = open::that("https://github.com/alibaba/lumenx/wiki"); }
                "release_notes" => { let _ = open::that("https://github.com/alibaba/lumenx/releases"); }
                "report_issue" => { let _ = open::that("https://github.com/alibaba/lumenx/issues/new"); }
                _ => {}
            }
        })
        .setup(move |app| {
            // Set up native macOS menu bar
            let native_menu = menu::build_menu(app.handle())?;
            app.set_menu(native_menu)?;

            let _window = app.get_webview_window("main").unwrap();

            // macOS titlebar styling: using decorations:false for custom titlebar.
            // The drag region is handled via CSS data-tauri-drag-region in the frontend.
            // No manual cocoa manipulation needed for basic setup.

            // Start Python sidecar in background
            let app_handle = app.handle().clone();
            let running = backend_running_clone.clone();
            std::thread::spawn(move || {
                sidecar::start_backend(&app_handle, running);
            });

            Ok(())
        })
        .on_window_event(move |_window, event| {
            // Clean up sidecar on window close
            if let tauri::WindowEvent::Destroyed = event {
                backend_running.store(false, Ordering::SeqCst);
            }
        })
        .invoke_handler(tauri::generate_handler![
            api_proxy,
            check_backend_health,
        ])
        .run(tauri::generate_context!())
        .expect("error while running LumenX Studio");
}
