use super::{OcrContainerRuntime, FAVA_HOST, FAVA_PORT, OCR_CONTAINER_NAME, OCR_IMAGE, SERVE_HOST};
use std::collections::VecDeque;
use std::ffi::OsStr;
use std::io::{self, Write};
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::sync::{Arc, Mutex};

pub(super) struct SpawnedProcess {
    pub child: Child,
    pub command_line: String,
}

pub(super) fn backend_serve_command_line() -> String {
    let base = backend_command();
    render_command_line(&base, &["serve", "--host", SERVE_HOST, "--port", "8080"])
}

pub(super) fn fava_command_line(ledger_path: &str) -> String {
    fava_command(ledger_path).join(" ")
}

pub(super) fn spawn_backend_serve(
    log_lines: &Arc<Mutex<VecDeque<String>>>,
) -> io::Result<SpawnedProcess> {
    let base = backend_command();
    let extra = ["serve", "--host", SERVE_HOST, "--port", "8080"];
    let command_line = render_command_line(&base, &extra);
    let child = spawn_logged_full_command(&base, &extra, log_lines)?;
    Ok(SpawnedProcess {
        child,
        command_line,
    })
}

pub(super) fn spawn_fava(
    ledger_path: &str,
    log_lines: &Arc<Mutex<VecDeque<String>>>,
) -> io::Result<SpawnedProcess> {
    let command = fava_command(ledger_path);
    let command_line = command.join(" ");
    let child = spawn_logged_full_command(&command, &[], log_lines)?;
    Ok(SpawnedProcess {
        child,
        command_line,
    })
}

pub(super) fn run_backend_capture(
    args: &[&str],
    stdin_input: Option<&str>,
) -> io::Result<(Output, String)> {
    let base = backend_command();
    let command_line = render_command_line(&base, args);
    let output = run_capture_full_command(&base, args, stdin_input)?;
    Ok((output, command_line))
}

pub(super) fn run_backend_interactive(args: &[&str]) -> io::Result<ExitStatus> {
    let base = backend_command();
    run_interactive_full_command(&base, args)
}

pub(super) fn view_csv_file(path: &str) -> io::Result<ExitStatus> {
    run_program_status("less", ["-N", "-S", path])
}

pub(super) fn find_original_image(receipt_dir: &Path) -> io::Result<std::path::PathBuf> {
    let source_dir = receipt_dir.join("source");
    let entries = std::fs::read_dir(&source_dir).map_err(|error| {
        io::Error::new(error.kind(), format!("{}: {}", source_dir.display(), error))
    })?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.file_stem().and_then(|stem| stem.to_str()) == Some("original") {
            return Ok(path);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!("no original.* file under {}", source_dir.display()),
    ))
}

pub(super) fn xdg_open_detached(path: &Path) -> io::Result<()> {
    Command::new("xdg-open")
        .arg(path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
}

pub(super) fn trash_file(path: &str) -> io::Result<Output> {
    run_program_output("trash", [path])
}

pub(super) fn command_on_path(program: &str) -> bool {
    let Some(paths) = std::env::var_os("PATH") else {
        return false;
    };

    std::env::split_paths(&paths).any(|directory| Path::new(&directory).join(program).is_file())
}

pub(super) fn ocr_start(runtime: OcrContainerRuntime) -> io::Result<Output> {
    run_program_output(runtime.command(), ["start", OCR_CONTAINER_NAME])
}

pub(super) fn ocr_stop(runtime: OcrContainerRuntime) -> io::Result<Output> {
    run_program_output(runtime.command(), ["stop", OCR_CONTAINER_NAME])
}

pub(super) fn ocr_restart(runtime: OcrContainerRuntime) -> io::Result<Output> {
    run_program_output(runtime.command(), ["restart", OCR_CONTAINER_NAME])
}

pub(super) fn ocr_create_and_start(runtime: OcrContainerRuntime) -> io::Result<Output> {
    match runtime {
        OcrContainerRuntime::Podman => run_program_output(
            runtime.command(),
            [
                "run",
                "-d",
                "--replace",
                "--name",
                OCR_CONTAINER_NAME,
                "--network=slirp4netns",
                "-p",
                "8001:8000",
                OCR_IMAGE,
            ],
        ),
        OcrContainerRuntime::Docker => run_program_output(
            runtime.command(),
            [
                "run",
                "-d",
                "--name",
                OCR_CONTAINER_NAME,
                "-p",
                "8001:8000",
                OCR_IMAGE,
            ],
        ),
    }
}

pub(super) fn ocr_inspect_running(runtime: OcrContainerRuntime) -> io::Result<Output> {
    run_program_output(
        runtime.command(),
        [
            "inspect",
            "--format",
            "{{.State.Running}}",
            OCR_CONTAINER_NAME,
        ],
    )
}

pub(super) fn ocr_inspect(runtime: OcrContainerRuntime) -> io::Result<Output> {
    run_program_output(runtime.command(), ["inspect", OCR_CONTAINER_NAME])
}

pub(super) fn ocr_logs_tail(runtime: OcrContainerRuntime) -> io::Result<Output> {
    run_program_output(
        runtime.command(),
        ["logs", "--tail", "80", OCR_CONTAINER_NAME],
    )
}

fn backend_command() -> Vec<String> {
    if let Ok(raw) = std::env::var("BEANBEAVER_TUI_BACKEND") {
        let parts: Vec<String> = raw.split_whitespace().map(ToOwned::to_owned).collect();
        if !parts.is_empty() {
            return parts;
        }
    }
    let pixi_bb = std::path::Path::new(".pixi")
        .join("envs")
        .join("default")
        .join("bin")
        .join("bb");
    if pixi_bb.exists() {
        return vec![pixi_bb.to_string_lossy().into_owned()];
    }
    vec![
        "python".to_string(),
        "-m".to_string(),
        "beanbeaver.cli.main".to_string(),
    ]
}

fn fava_command(ledger_path: &str) -> Vec<String> {
    let pixi_fava = std::path::Path::new(".pixi")
        .join("envs")
        .join("default")
        .join("bin")
        .join("fava");
    if pixi_fava.exists() {
        return vec![
            pixi_fava.to_string_lossy().into_owned(),
            ledger_path.to_string(),
            "--host".to_string(),
            FAVA_HOST.to_string(),
            "--port".to_string(),
            FAVA_PORT.to_string(),
        ];
    }
    vec![
        "fava".to_string(),
        ledger_path.to_string(),
        "--host".to_string(),
        FAVA_HOST.to_string(),
        "--port".to_string(),
        FAVA_PORT.to_string(),
    ]
}

fn render_command_line(base: &[String], extra: &[&str]) -> String {
    base.iter()
        .map(String::as_str)
        .chain(extra.iter().copied())
        .collect::<Vec<_>>()
        .join(" ")
}

fn spawn_logged_full_command(
    command: &[String],
    extra_args: &[&str],
    log_lines: &Arc<Mutex<VecDeque<String>>>,
) -> io::Result<Child> {
    let (program, program_args) = split_command(command)?;
    spawn_logged_command(
        program,
        program_args
            .iter()
            .map(String::as_str)
            .chain(extra_args.iter().copied()),
        log_lines,
    )
}

fn spawn_logged_command<I, S>(
    program: &str,
    args: I,
    log_lines: &Arc<Mutex<VecDeque<String>>>,
) -> io::Result<Child>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut child = Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    if let Some(stdout) = child.stdout.take() {
        super::spawn_log_reader(stdout, "stdout", Arc::clone(log_lines));
    }
    if let Some(stderr) = child.stderr.take() {
        super::spawn_log_reader(stderr, "stderr", Arc::clone(log_lines));
    }

    Ok(child)
}

fn run_program_output<I, S>(program: &str, args: I) -> io::Result<Output>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new(program).args(args).output()
}

fn run_program_status<I, S>(program: &str, args: I) -> io::Result<ExitStatus>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new(program)
        .args(args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
}

fn run_capture_full_command(
    command: &[String],
    extra_args: &[&str],
    stdin_input: Option<&str>,
) -> io::Result<Output> {
    let (program, program_args) = split_command(command)?;
    let mut command = Command::new(program);
    command.args(program_args).args(extra_args.iter().copied());
    if stdin_input.is_some() {
        command.stdin(Stdio::piped());
    } else {
        command.stdin(Stdio::null());
    }
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    if let Some(input) = stdin_input {
        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(input.as_bytes())?;
        }
    }

    child.wait_with_output()
}

fn run_interactive_full_command(command: &[String], extra_args: &[&str]) -> io::Result<ExitStatus> {
    let (program, program_args) = split_command(command)?;
    Command::new(program)
        .args(program_args)
        .args(extra_args.iter().copied())
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
}

fn split_command(command: &[String]) -> io::Result<(&str, &[String])> {
    command
        .split_first()
        .map(|(program, args)| (program.as_str(), args))
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Empty command"))
}
