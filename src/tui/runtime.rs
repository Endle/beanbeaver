use super::*;

use std::collections::VecDeque;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::ExitStatus;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::Value;

pub(crate) fn run_backend_interactive(args: &[&str]) -> AppResult<i32> {
    let status = process_util::run_backend_interactive(args)?;
    Ok(status.code().unwrap_or(1))
}

pub(crate) fn exit_status_code(status: ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

pub(crate) fn replace_log_lines(buffer: &Arc<Mutex<VecDeque<String>>>, lines: Vec<String>) {
    let mut guard = buffer
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.clear();
    for line in lines {
        guard.push_back(line);
    }
    if guard.is_empty() {
        guard.push_back("No logs yet.".to_string());
    }
}

pub(crate) fn push_bounded_log_line(
    buffer: &Arc<Mutex<VecDeque<String>>>,
    line: impl Into<String>,
) {
    let mut guard = buffer
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.push_back(line.into());
    while guard.len() > MAX_RUNTIME_LOG_LINES {
        guard.pop_front();
    }
}

pub(crate) fn snapshot_log_lines(buffer: &Arc<Mutex<VecDeque<String>>>) -> Vec<String> {
    let guard = buffer
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if guard.is_empty() {
        vec!["No logs yet.".to_string()]
    } else {
        guard.iter().cloned().collect()
    }
}

pub(crate) fn spawn_log_reader<R: Read + Send + 'static>(
    reader: R,
    stream_name: &'static str,
    buffer: Arc<Mutex<VecDeque<String>>>,
) {
    thread::spawn(move || {
        let reader = BufReader::new(reader);
        for line_result in reader.lines() {
            match line_result {
                Ok(line) => push_bounded_log_line(&buffer, format!("[{stream_name}] {line}")),
                Err(error) => {
                    push_bounded_log_line(
                        &buffer,
                        format!("[{stream_name}] log reader error: {error}"),
                    );
                    break;
                }
            }
        }
    });
}

pub(crate) fn check_local_health(
    port: u16,
    path: &str,
    success_codes: &[u16],
) -> Result<String, String> {
    let mut stream = TcpStream::connect((SERVE_HEALTH_HOST, port))
        .map_err(|error| format!("Health probe failed: {error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_millis(500)))
        .map_err(|error| format!("Failed to set read timeout: {error}"))?;
    stream
        .set_write_timeout(Some(Duration::from_millis(500)))
        .map_err(|error| format!("Failed to set write timeout: {error}"))?;
    stream
        .write_all(
            format!(
                "GET {path} HTTP/1.1\r\nHost: {SERVE_HEALTH_HOST}:{port}\r\nConnection: close\r\n\r\n"
            )
            .as_bytes(),
        )
        .map_err(|error| format!("Health probe request failed: {error}"))?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("Health probe read failed: {error}"))?;

    let status_line = response.lines().next().unwrap_or("No HTTP response");
    let status_ok = success_codes
        .iter()
        .any(|code| status_line.contains(&code.to_string()));
    if status_ok {
        Ok(format!(
            "Healthy: http://{SERVE_HEALTH_HOST}:{port}{path} returned {status_line}"
        ))
    } else {
        Err(format!("Health probe returned {status_line}"))
    }
}

pub(crate) fn select_ocr_runtime(podman_on_path: bool) -> OcrContainerRuntime {
    if podman_on_path {
        OcrContainerRuntime::Podman
    } else {
        OcrContainerRuntime::Docker
    }
}

pub(crate) fn preferred_ocr_runtime() -> OcrContainerRuntime {
    select_ocr_runtime(process_util::command_on_path(
        OcrContainerRuntime::Podman.command(),
    ))
}

pub(crate) fn render_ocr_runtime_command(runtime: OcrContainerRuntime, args: &[&str]) -> String {
    std::iter::once(runtime.command())
        .chain(args.iter().copied())
        .collect::<Vec<_>>()
        .join(" ")
}

pub(crate) fn error_mentions_missing_container(stdout: &str, stderr: &str) -> bool {
    let combined = format!("{stdout}\n{stderr}").to_ascii_lowercase();
    ["no such", "not found", "does not exist", "no container"]
        .iter()
        .any(|needle| combined.contains(needle))
}

pub(crate) fn ensure_ocr_runtime_success(
    runtime: OcrContainerRuntime,
    output: std::process::Output,
    rendered_command: &str,
) -> AppResult<()> {
    if output.status.success() {
        return Ok(());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!(
        "{} command failed: {rendered_command}\nstdout:\n{}\nstderr:\n{}",
        runtime.command(),
        stdout.trim(),
        stderr.trim()
    )
    .into())
}

pub(crate) fn ocr_container_state(runtime: OcrContainerRuntime) -> AppResult<OcrContainerState> {
    let inspect_output = process_util::ocr_inspect_running(runtime)?;
    if !inspect_output.status.success() {
        let stdout = String::from_utf8_lossy(&inspect_output.stdout);
        let stderr = String::from_utf8_lossy(&inspect_output.stderr);
        if error_mentions_missing_container(stdout.trim(), stderr.trim()) {
            return Ok(OcrContainerState::Missing);
        }
        return Err(format!(
            "{} command failed: {}\nstdout:\n{}\nstderr:\n{}",
            runtime.command(),
            render_ocr_runtime_command(
                runtime,
                &[
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    OCR_CONTAINER_NAME
                ],
            ),
            stdout.trim(),
            stderr.trim()
        )
        .into());
    }

    let running = String::from_utf8_lossy(&inspect_output.stdout)
        .trim()
        .eq_ignore_ascii_case("true");
    if running {
        Ok(OcrContainerState::Running)
    } else {
        Ok(OcrContainerState::Stopped)
    }
}

pub(crate) fn query_ocr_page_state() -> OcrPageState {
    let runtime = preferred_ocr_runtime();
    let inspect_output = match process_util::ocr_inspect(runtime) {
        Ok(output) => output,
        Err(error) => {
            return OcrPageState {
                runtime,
                summary_lines: vec![
                    format!("{} unavailable", runtime.display_name()),
                    format!("Error: {error}"),
                    format!("Container: {OCR_CONTAINER_NAME}"),
                ],
                log_lines: vec![format!(
                    "Install Podman or Docker, or ensure `{}` is available on PATH.",
                    runtime.command()
                )],
            };
        }
    };

    if !inspect_output.status.success() {
        let stderr = String::from_utf8_lossy(&inspect_output.stderr)
            .trim()
            .to_string();
        let stdout = String::from_utf8_lossy(&inspect_output.stdout)
            .trim()
            .to_string();
        if error_mentions_missing_container(&stdout, &stderr) {
            return OcrPageState {
                runtime,
                summary_lines: vec![
                    format!("{} available", runtime.display_name()),
                    format!("Container `{OCR_CONTAINER_NAME}` not found."),
                    "Press `s` to create and start it.".to_string(),
                    "Manual command:".to_string(),
                    runtime.suggested_run_command().to_string(),
                ],
                log_lines: vec!["No logs because the container does not exist.".to_string()],
            };
        }
        return OcrPageState {
            runtime,
            summary_lines: vec![
                format!("{} inspect failed", runtime.display_name()),
                format!(
                    "Command: {}",
                    render_ocr_runtime_command(runtime, &["inspect", OCR_CONTAINER_NAME])
                ),
                format!(
                    "stdout: {}",
                    if stdout.is_empty() {
                        "<empty>"
                    } else {
                        &stdout
                    }
                ),
                format!(
                    "stderr: {}",
                    if stderr.is_empty() {
                        "<empty>"
                    } else {
                        &stderr
                    }
                ),
            ],
            log_lines: vec!["Unable to inspect container logs.".to_string()],
        };
    }

    let summary_lines = match ocr_summary_lines(&String::from_utf8_lossy(&inspect_output.stdout)) {
        Ok(lines) => lines,
        Err(error) => vec![
            format!("Failed to parse `{}` inspect output", runtime.command()),
            format!("Error: {error}"),
        ],
    };

    let logs_output = match process_util::ocr_logs_tail(runtime) {
        Ok(output) => output,
        Err(error) => {
            return OcrPageState {
                runtime,
                summary_lines,
                log_lines: vec![format!("Failed to fetch container logs: {error}")],
            };
        }
    };

    let log_lines = if logs_output.status.success() {
        let stdout = String::from_utf8_lossy(&logs_output.stdout);
        let mut lines: Vec<String> = stdout.lines().map(ToOwned::to_owned).collect();
        if lines.is_empty() {
            lines.push("No logs emitted yet.".to_string());
        }
        lines
    } else {
        let stderr = String::from_utf8_lossy(&logs_output.stderr)
            .trim()
            .to_string();
        vec![format!(
            "Failed to fetch `{}` logs: {}",
            runtime.command(),
            if stderr.is_empty() {
                "<empty>"
            } else {
                &stderr
            }
        )]
    };

    OcrPageState {
        runtime,
        summary_lines,
        log_lines,
    }
}

pub(crate) fn ocr_summary_lines(raw_json: &str) -> AppResult<Vec<String>> {
    let payload: Value = serde_json::from_str(raw_json)?;
    let entry = payload
        .as_array()
        .and_then(|items| items.first())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Missing inspect payload"))?;

    let name = entry
        .get("Name")
        .and_then(Value::as_str)
        .unwrap_or(OCR_CONTAINER_NAME)
        .trim_start_matches('/');
    let status = entry
        .pointer("/State/Status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let running = entry
        .pointer("/State/Running")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let exit_code = entry
        .pointer("/State/ExitCode")
        .and_then(Value::as_i64)
        .map(|value| value.to_string())
        .unwrap_or_else(|| "unknown".to_string());
    let image = entry
        .get("ImageName")
        .and_then(Value::as_str)
        .or_else(|| entry.pointer("/Config/Image").and_then(Value::as_str))
        .or_else(|| entry.get("Image").and_then(Value::as_str))
        .unwrap_or("unknown");
    let created = entry
        .get("Created")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let started_at = entry
        .pointer("/State/StartedAt")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let finished_at = entry
        .pointer("/State/FinishedAt")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let command = {
        let mut parts = Vec::new();
        if let Some(path) = entry.get("Path").and_then(Value::as_str) {
            parts.push(path.to_string());
        }
        if let Some(args) = entry.get("Args").and_then(Value::as_array) {
            parts.extend(args.iter().filter_map(Value::as_str).map(ToOwned::to_owned));
        }
        if parts.is_empty() {
            "unknown".to_string()
        } else {
            parts.join(" ")
        }
    };
    let ports = entry
        .pointer("/NetworkSettings/Ports")
        .map(format_container_ports)
        .unwrap_or_else(|| "unknown".to_string());

    Ok(vec![
        format!("Container: {name}"),
        format!(
            "Status: {} ({})",
            status,
            if running { "running" } else { "not running" }
        ),
        format!("Exit code: {exit_code}"),
        format!("Image: {image}"),
        format!("Ports: {ports}"),
        format!("Command: {command}"),
        format!("Created: {created}"),
        format!("Started: {started_at}"),
        format!("Finished: {finished_at}"),
    ])
}

pub(crate) fn format_container_ports(ports_value: &Value) -> String {
    let Some(ports) = ports_value.as_object() else {
        return "unknown".to_string();
    };
    if ports.is_empty() {
        return "none".to_string();
    }

    let mut rendered = Vec::new();
    for (container_port, bindings_value) in ports {
        match bindings_value {
            Value::Null => rendered.push(format!("{container_port} (not published)")),
            Value::Array(bindings) if bindings.is_empty() => {
                rendered.push(format!("{container_port} (not published)"));
            }
            Value::Array(bindings) => {
                for binding in bindings {
                    let host_ip = binding
                        .get("HostIp")
                        .and_then(Value::as_str)
                        .unwrap_or("0.0.0.0");
                    let host_port = binding
                        .get("HostPort")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown");
                    rendered.push(format!("{host_ip}:{host_port} -> {container_port}"));
                }
            }
            _ => rendered.push(format!("{container_port} (unknown binding)")),
        }
    }
    rendered.join(", ")
}
