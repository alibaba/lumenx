// Sidecar management: start/stop/health-check the Python FastAPI backend

use std::process::{Command, Child};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use std::thread;
use tauri::Manager;

/// Start the Python backend sidecar process
/// In dev mode: runs `python -m uvicorn src.apps.comic_gen.api:app --host 0.0.0.0 --port 17177`
/// In production: runs the bundled PyInstaller binary
pub fn start_backend(app_handle: &tauri::AppHandle, running: Arc<AtomicBool>) {
    // Check if backend is already running (e.g. user started it manually)
    if is_backend_ready() {
        running.store(true, Ordering::SeqCst);
        println!("[sidecar] Backend already running on port 17177");
        return;
    }

    let child = if cfg!(debug_assertions) {
        // Dev mode: run Python directly
        start_dev_backend()
    } else {
        // Production: use bundled sidecar binary
        start_prod_backend(app_handle)
    };

    match child {
        Ok(mut process) => {
            running.store(true, Ordering::SeqCst);
            println!("[sidecar] Backend process started (pid: {})", process.id());

            // Wait for backend to be ready (poll /health every 200ms, up to 30s)
            let ready = wait_for_backend_ready(150); // 150 * 200ms = 30s
            if ready {
                println!("[sidecar] Backend is ready!");
            } else {
                eprintln!("[sidecar] Backend failed to become ready within 30s");
            }

            // Keep monitoring the process
            loop {
                if !running.load(Ordering::SeqCst) {
                    // Application is shutting down, kill the backend
                    let _ = process.kill();
                    let _ = process.wait();
                    println!("[sidecar] Backend process terminated");
                    break;
                }

                // Check if process is still alive
                match process.try_wait() {
                    Ok(Some(status)) => {
                        eprintln!("[sidecar] Backend exited with status: {:?}", status);
                        running.store(false, Ordering::SeqCst);
                        break;
                    }
                    Ok(None) => {
                        // Still running, sleep a bit
                        thread::sleep(Duration::from_secs(1));
                    }
                    Err(e) => {
                        eprintln!("[sidecar] Error checking backend status: {}", e);
                        break;
                    }
                }
            }
        }
        Err(e) => {
            eprintln!("[sidecar] Failed to start backend: {}", e);
        }
    }
}

fn start_dev_backend() -> Result<Child, std::io::Error> {
    Command::new("python")
        .args(["-m", "uvicorn", "src.apps.comic_gen.api:app", "--host", "0.0.0.0", "--port", "17177"])
        .spawn()
}

fn start_prod_backend(app_handle: &tauri::AppHandle) -> Result<Child, std::io::Error> {
    // Resolve the sidecar binary path from Tauri's resource directory
    let resource_path = app_handle
        .path()
        .resource_dir()
        .expect("failed to resolve resource dir");

    let binary_name = if cfg!(target_arch = "aarch64") {
        "lumenx-backend-aarch64-apple-darwin"
    } else {
        "lumenx-backend-x86_64-apple-darwin"
    };

    let sidecar_path = resource_path.join("binaries").join(binary_name);

    Command::new(sidecar_path)
        .arg("--port")
        .arg("17177")
        .spawn()
}

fn is_backend_ready() -> bool {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(1))
        .build();

    match client {
        Ok(c) => c.get("http://127.0.0.1:17177/health").send().is_ok(),
        Err(_) => false,
    }
}

fn wait_for_backend_ready(max_attempts: u32) -> bool {
    for _ in 0..max_attempts {
        if is_backend_ready() {
            return true;
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}
