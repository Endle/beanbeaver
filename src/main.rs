use std::env;
use std::error::Error;
use std::io::{self, Stdout, Write};
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Text};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Tabs, Wrap};
use ratatui::Terminal;
use serde::Deserialize;
use serde_json::Value;

type AppResult<T> = Result<T, Box<dyn Error>>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Queue {
    Scanned,
    Approved,
}

impl Queue {
    fn title(self) -> &'static str {
        match self {
            Queue::Scanned => "Scanned",
            Queue::Approved => "Approved",
        }
    }

    fn api_list_command(self) -> &'static str {
        match self {
            Queue::Scanned => "list-scanned",
            Queue::Approved => "list-approved",
        }
    }

    fn tab_index(self) -> usize {
        match self {
            Queue::Scanned => 0,
            Queue::Approved => 1,
        }
    }

    fn from_tab(index: usize) -> Self {
        if index == 0 {
            Queue::Scanned
        } else {
            Queue::Approved
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PaneFocus {
    List,
    Detail,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RightPane {
    Details,
    StatusLog,
}

#[derive(Clone, Debug, Deserialize)]
struct ReceiptsResponse {
    receipts: Vec<ReceiptSummary>,
}

#[derive(Clone, Debug, Deserialize)]
struct ReceiptSummary {
    path: String,
    receipt_dir: String,
    stage_file: String,
    merchant: Option<String>,
    date: Option<String>,
    total: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct ShowReceiptResponse {
    path: String,
    summary: ReceiptSummary,
    document: Value,
}

#[derive(Clone, Debug, Deserialize)]
struct ApproveReceiptResponse {
    status: String,
    source_path: String,
    approved_path: String,
}

#[derive(Clone, Debug, Deserialize)]
struct ReEditApprovedResponse {
    status: String,
    #[serde(rename = "source_path")]
    _source_path: String,
    updated_path: Option<String>,
    normalize_error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct ConfigResponse {
    config_path: String,
    project_root: String,
    resolved_project_root: String,
    resolved_main_beancount_path: String,
    scanned_dir: String,
    approved_dir: String,
}

#[derive(Clone, Debug, Deserialize)]
struct MatchCandidateSummary {
    file_path: String,
    line_number: i32,
    confidence: f64,
    display: String,
    payee: Option<String>,
    narration: Option<String>,
    date: String,
    amount: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct MatchCandidatesResponse {
    #[serde(rename = "path")]
    _path: String,
    ledger_path: String,
    errors: Vec<String>,
    warning: Option<String>,
    candidates: Vec<MatchCandidateSummary>,
}

#[derive(Clone, Debug, Deserialize)]
struct ApplyMatchResponse {
    status: String,
    message: Option<String>,
    #[serde(rename = "matched_receipt_path")]
    _matched_receipt_path: Option<String>,
    #[serde(rename = "enriched_path")]
    _enriched_path: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum EditMode {
    ApproveScanned,
    UpdateApproved,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum EditField {
    Merchant,
    Date,
    Total,
}

impl EditField {
    fn label(self) -> &'static str {
        match self {
            EditField::Merchant => "Merchant",
            EditField::Date => "Date",
            EditField::Total => "Total",
        }
    }

    fn next(self) -> Self {
        match self {
            EditField::Merchant => EditField::Date,
            EditField::Date => EditField::Total,
            EditField::Total => EditField::Merchant,
        }
    }

    fn previous(self) -> Self {
        match self {
            EditField::Merchant => EditField::Total,
            EditField::Date => EditField::Merchant,
            EditField::Total => EditField::Date,
        }
    }
}

struct EditState {
    merchant: String,
    date: String,
    total: String,
    active_field: EditField,
}

struct ConfigState {
    project_root: String,
}

struct MatchState {
    candidates: Vec<MatchCandidateSummary>,
    state: ListState,
    ledger_path: String,
    warning: Option<String>,
}

impl ConfigState {
    fn from_response(config: &ConfigResponse) -> Self {
        Self {
            project_root: if config.project_root.is_empty() {
                config.resolved_project_root.clone()
            } else {
                config.project_root.clone()
            },
        }
    }
}

impl MatchState {
    fn new(response: MatchCandidatesResponse) -> Self {
        let mut state = ListState::default();
        if !response.candidates.is_empty() {
            state.select(Some(0));
        }
        Self {
            candidates: response.candidates,
            state,
            ledger_path: response.ledger_path,
            warning: response.warning,
        }
    }

    fn selected(&self) -> Option<&MatchCandidateSummary> {
        self.state
            .selected()
            .and_then(|index| self.candidates.get(index))
    }

    fn move_selection(&mut self, delta: isize) {
        let len = self.candidates.len();
        if len == 0 {
            self.state.select(None);
            return;
        }
        let current = self.state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.state.select(Some(next));
    }
}

impl EditState {
    fn from_summary(summary: &ReceiptSummary) -> Self {
        Self {
            merchant: summary.merchant.clone().unwrap_or_default(),
            date: summary.date.clone().unwrap_or_default(),
            total: summary.total.clone().unwrap_or_default(),
            active_field: EditField::Merchant,
        }
    }

    fn active_value_mut(&mut self) -> &mut String {
        match self.active_field {
            EditField::Merchant => &mut self.merchant,
            EditField::Date => &mut self.date,
            EditField::Total => &mut self.total,
        }
    }

    fn review_payload(&self) -> Value {
        serde_json::json!({
            "review": {
                "merchant": self.merchant,
                "date": self.date,
                "total": self.total,
            }
        })
    }
}

struct App {
    active_queue: Queue,
    focus: PaneFocus,
    right_pane: RightPane,
    scanned: Vec<ReceiptSummary>,
    approved: Vec<ReceiptSummary>,
    scanned_state: ListState,
    approved_state: ListState,
    detail_lines: Vec<String>,
    status_log_lines: Vec<String>,
    detail_path: Option<String>,
    detail_scroll_y: u16,
    detail_scroll_x: u16,
    status: String,
    edit_state: Option<EditState>,
    edit_mode: Option<EditMode>,
    config: ConfigResponse,
    config_state: Option<ConfigState>,
    match_state: Option<MatchState>,
    should_quit: bool,
}

impl App {
    fn new() -> Self {
        let mut scanned_state = ListState::default();
        scanned_state.select(Some(0));
        let mut approved_state = ListState::default();
        approved_state.select(Some(0));
        Self {
            active_queue: Queue::Scanned,
            focus: PaneFocus::List,
            right_pane: RightPane::Details,
            scanned: Vec::new(),
            approved: Vec::new(),
            scanned_state,
            approved_state,
            detail_lines: vec!["Loading receipts...".to_string()],
            status_log_lines: Vec::new(),
            detail_path: None,
            detail_scroll_y: 0,
            detail_scroll_x: 0,
            status:
                "q quit | Tab switch queues | h/l pane focus | s toggle details/status | j/k move or scroll | e edit | m TUI match | M CLI match | arrows pan right pane | r reload | a approve | c config"
                    .to_string(),
            edit_state: None,
            edit_mode: None,
            config: ConfigResponse {
                config_path: String::new(),
                project_root: String::new(),
                resolved_project_root: String::new(),
                resolved_main_beancount_path: String::new(),
                scanned_dir: String::new(),
                approved_dir: String::new(),
            },
            config_state: None,
            match_state: None,
            should_quit: false,
        }
        .with_initial_status()
    }

    fn with_initial_status(mut self) -> Self {
        self.push_status_log(self.status.clone());
        self
    }

    fn receipts(&self, queue: Queue) -> &[ReceiptSummary] {
        match queue {
            Queue::Scanned => &self.scanned,
            Queue::Approved => &self.approved,
        }
    }

    fn list_state_mut(&mut self, queue: Queue) -> &mut ListState {
        match queue {
            Queue::Scanned => &mut self.scanned_state,
            Queue::Approved => &mut self.approved_state,
        }
    }

    fn selected_index(&self, queue: Queue) -> Option<usize> {
        match queue {
            Queue::Scanned => self.scanned_state.selected(),
            Queue::Approved => self.approved_state.selected(),
        }
    }

    fn selected_receipt(&self) -> Option<&ReceiptSummary> {
        let receipts = self.receipts(self.active_queue);
        self.selected_index(self.active_queue)
            .and_then(|index| receipts.get(index))
    }

    fn sync_selection(&mut self, queue: Queue) {
        let len = self.receipts(queue).len();
        let state = self.list_state_mut(queue);
        match len {
            0 => state.select(None),
            _ => {
                let current = state.selected().unwrap_or(0);
                state.select(Some(current.min(len - 1)));
            }
        }
    }

    fn move_selection(&mut self, delta: isize) {
        let len = self.receipts(self.active_queue).len();
        if len == 0 {
            return;
        }
        let current = self.selected_index(self.active_queue).unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.list_state_mut(self.active_queue).select(Some(next));
    }

    fn switch_queue(&mut self) {
        self.active_queue = match self.active_queue {
            Queue::Scanned => Queue::Approved,
            Queue::Approved => Queue::Scanned,
        };
        self.sync_selection(self.active_queue);
        self.focus = PaneFocus::List;
    }

    fn set_status(&mut self, message: impl Into<String>) {
        let message = message.into();
        self.status = message.clone();
        self.push_status_log(message);
    }

    fn set_error(&mut self, message: impl Into<String>) {
        self.right_pane = RightPane::StatusLog;
        self.set_status(message);
        self.scroll_detail_to_bottom();
    }

    fn push_status_log(&mut self, message: String) {
        if !self.status_log_lines.is_empty() {
            self.status_log_lines.push(String::new());
        }
        self.status_log_lines
            .extend(message.lines().map(ToOwned::to_owned));
        if self.status_log_lines.is_empty() {
            self.status_log_lines.push(String::new());
        }
    }

    fn toggle_right_pane(&mut self) {
        self.right_pane = match self.right_pane {
            RightPane::Details => RightPane::StatusLog,
            RightPane::StatusLog => RightPane::Details,
        };
        self.detail_scroll_y = 0;
        self.detail_scroll_x = 0;
        self.set_status(match self.right_pane {
            RightPane::Details => "Switched right pane to receipt details",
            RightPane::StatusLog => "Switched right pane to status log",
        });
    }

    fn right_pane_lines(&self) -> &[String] {
        match self.right_pane {
            RightPane::Details => &self.detail_lines,
            RightPane::StatusLog => &self.status_log_lines,
        }
    }

    fn right_pane_title(&self) -> String {
        match self.right_pane {
            RightPane::Details => match &self.detail_path {
                Some(path) => format!("Details: {path}"),
                None => "Details".to_string(),
            },
            RightPane::StatusLog => "Status Log".to_string(),
        }
    }

    fn refresh(&mut self) -> AppResult<()> {
        self.config = backend_get_config()?;
        self.reload_receipts()?;
        self.load_detail()?;
        self.set_status(format!(
            "Loaded {} scanned / {} approved receipt(s)",
            self.scanned.len(),
            self.approved.len()
        ));
        Ok(())
    }

    fn reload_receipts(&mut self) -> AppResult<()> {
        self.scanned = backend_list_receipts(Queue::Scanned)?;
        self.approved = backend_list_receipts(Queue::Approved)?;
        self.sync_selection(Queue::Scanned);
        self.sync_selection(Queue::Approved);
        Ok(())
    }

    fn load_detail(&mut self) -> AppResult<()> {
        let Some(mut path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.detail_lines = vec!["No receipt selected.".to_string()];
            self.detail_path = None;
            self.detail_scroll_y = 0;
            self.detail_scroll_x = 0;
            return Ok(());
        };
        if !Path::new(&path).exists() {
            self.reload_receipts()?;
            let Some(reloaded_path) = self.selected_receipt().map(|receipt| receipt.path.clone())
            else {
                self.detail_lines = vec!["No receipt selected.".to_string()];
                self.detail_path = None;
                self.detail_scroll_y = 0;
                self.detail_scroll_x = 0;
                return Ok(());
            };
            path = reloaded_path;
            self.set_status("Selected receipt changed on disk; reloaded receipt list");
        }
        let detail = backend_show_receipt(&path)?;
        self.detail_path = Some(detail.path.clone());
        self.detail_lines = render_detail_lines(&detail);
        self.detail_scroll_y = 0;
        self.detail_scroll_x = 0;
        Ok(())
    }

    fn scroll_detail_vertical(&mut self, delta: i32) {
        if delta >= 0 {
            self.detail_scroll_y = self.detail_scroll_y.saturating_add(delta as u16);
        } else {
            self.detail_scroll_y = self.detail_scroll_y.saturating_sub((-delta) as u16);
        }
    }

    fn scroll_detail_horizontal(&mut self, delta: i32) {
        if delta >= 0 {
            self.detail_scroll_x = self.detail_scroll_x.saturating_add(delta as u16);
        } else {
            self.detail_scroll_x = self.detail_scroll_x.saturating_sub((-delta) as u16);
        }
    }

    fn scroll_detail_to_top(&mut self) {
        self.detail_scroll_y = 0;
    }

    fn scroll_detail_to_bottom(&mut self) {
        self.detail_scroll_y = self.right_pane_lines().len().saturating_sub(1) as u16;
    }

    fn focus_list(&mut self) {
        self.focus = PaneFocus::List;
    }

    fn focus_detail(&mut self) {
        self.focus = PaneFocus::Detail;
    }

    fn approve_selected_scanned(&mut self) -> AppResult<()> {
        if self.active_queue != Queue::Scanned {
            self.set_status("Approve is only available in the Scanned queue");
            return Ok(());
        }
        let Some(path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.set_status("No scanned receipt selected");
            return Ok(());
        };
        let result = backend_approve_scanned(&path)?;
        self.refresh()?;
        self.set_status(format!(
            "Approved {} -> {}",
            result.source_path, result.approved_path
        ));
        Ok(())
    }

    fn begin_edit_selected(&mut self) {
        let Some(receipt) = self.selected_receipt() else {
            self.set_status("No receipt selected");
            return;
        };
        self.edit_state = Some(EditState::from_summary(receipt));
        self.edit_mode = Some(if self.active_queue == Queue::Scanned {
            EditMode::ApproveScanned
        } else {
            EditMode::UpdateApproved
        });
        self.set_status(
            "Edit review fields, Tab/Shift-Tab or Up/Down to move, Enter to save, Esc to cancel",
        );
    }

    fn apply_edit_changes(&mut self) -> AppResult<()> {
        let Some(path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.set_status("No receipt selected");
            return Ok(());
        };
        let Some(edit_mode) = self.edit_mode else {
            self.set_status("Missing edit mode");
            return Ok(());
        };
        let payload = {
            let edit_state = self
                .edit_state
                .as_ref()
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Missing edit state"))?;
            serde_json::to_string(&edit_state.review_payload())?
        };
        match edit_mode {
            EditMode::ApproveScanned => {
                let result = backend_approve_scanned_with_review(&path, &payload)?;
                self.edit_state = None;
                self.edit_mode = None;
                self.refresh()?;
                self.set_status(format!(
                    "Approved {} -> {}",
                    result.source_path, result.approved_path
                ));
            }
            EditMode::UpdateApproved => {
                let result = backend_re_edit_approved_with_review(&path, &payload)?;
                self.edit_state = None;
                self.edit_mode = None;
                self.refresh()?;
                self.set_status(match result.updated_path {
                    Some(updated_path) => format!("Updated approved receipt: {updated_path}"),
                    None => result.normalize_error.unwrap_or_else(|| {
                        format!("Approved receipt update failed: {}", result.status)
                    }),
                });
            }
        }
        Ok(())
    }

    fn can_match_selected_approved(&mut self) -> AppResult<bool> {
        if self.active_queue != Queue::Approved {
            self.set_status("Match is only available in the Approved queue");
            return Ok(false);
        }
        let Some(path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.set_status("No approved receipt selected");
            return Ok(false);
        };
        if !Path::new(&path).exists() {
            self.reload_receipts()?;
            self.load_detail()?;
            self.set_status("Selected approved receipt changed on disk; reloaded receipt list");
            return Ok(false);
        }
        self.set_status("Launching bb match...");
        Ok(true)
    }

    fn begin_match_selected_approved(&mut self) -> AppResult<()> {
        if !self.can_match_selected_approved()? {
            return Ok(());
        }
        let Some(path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.set_status("No approved receipt selected");
            return Ok(());
        };
        let response = backend_match_candidates(&path)?;
        if !response.errors.is_empty() {
            self.set_error(response.errors.join(" | "));
            return Ok(());
        }
        if response.candidates.is_empty() {
            self.set_status(
                response
                    .warning
                    .clone()
                    .unwrap_or_else(|| "No ledger matches found".to_string()),
            );
            return Ok(());
        }
        self.match_state = Some(MatchState::new(response));
        self.set_status("Select a candidate match, Enter to apply, Esc to cancel");
        Ok(())
    }

    fn apply_selected_match(&mut self) -> AppResult<()> {
        let Some(path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.set_status("No approved receipt selected");
            return Ok(());
        };
        let Some(match_state) = self.match_state.as_ref() else {
            self.set_status("Missing match state");
            return Ok(());
        };
        let Some(candidate) = match_state.selected() else {
            self.set_status("No match candidate selected");
            return Ok(());
        };
        let response = backend_apply_match(&path, &candidate.file_path, candidate.line_number)?;
        self.match_state = None;
        self.refresh()?;
        self.set_status(
            response
                .message
                .unwrap_or_else(|| "Match applied".to_string()),
        );
        Ok(())
    }

    fn begin_config_edit(&mut self) {
        self.config_state = Some(ConfigState::from_response(&self.config));
        self.set_status("Edit project root, Enter to save, Esc to cancel, Backspace delete");
    }

    fn apply_config(&mut self) -> AppResult<()> {
        let Some(config_state) = self.config_state.as_ref() else {
            self.set_status("Missing config state");
            return Ok(());
        };
        let config = backend_set_config(&config_state.project_root)?;
        self.config = config;
        self.config_state = None;
        self.set_status(format!(
            "Configured project root -> {}",
            self.config.resolved_project_root
        ));
        Ok(())
    }
}

fn backend_command() -> Vec<String> {
    if let Ok(raw) = env::var("BEANBEAVER_TUI_BACKEND") {
        let parts: Vec<String> = raw.split_whitespace().map(ToOwned::to_owned).collect();
        if !parts.is_empty() {
            return parts;
        }
    }
    let pixi_bb = Path::new(".pixi")
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

fn run_backend(args: &[&str]) -> AppResult<String> {
    run_backend_with_input(args, None)
}

fn run_backend_with_input(args: &[&str], stdin_input: Option<&str>) -> AppResult<String> {
    let backend = backend_command();
    let (program, program_args) = backend
        .split_first()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Empty backend command"))?;
    let mut command = Command::new(program);
    command.args(program_args).args(args);
    let rendered_command = backend
        .iter()
        .map(String::as_str)
        .chain(args.iter().copied())
        .collect::<Vec<_>>()
        .join(" ");
    if stdin_input.is_some() {
        command.stdin(std::process::Stdio::piped());
    }
    let mut child = command
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()?;
    if let Some(input) = stdin_input {
        use std::io::Write;

        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(input.as_bytes())?;
        }
    }
    let output = child.wait_with_output()?;

    if output.status.success() {
        Ok(String::from_utf8(output.stdout)?)
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        Err(format!(
            "backend command failed: {}\nstdout:\n{}\nstderr:\n{}",
            rendered_command,
            stdout.trim(),
            stderr.trim()
        )
        .into())
    }
}

fn backend_list_receipts(queue: Queue) -> AppResult<Vec<ReceiptSummary>> {
    let stdout = run_backend(&["api", queue.api_list_command()])?;
    let response: ReceiptsResponse = serde_json::from_str(&stdout)?;
    Ok(response.receipts)
}

fn backend_show_receipt(path: &str) -> AppResult<ShowReceiptResponse> {
    let stdout = run_backend(&["api", "show-receipt", path])?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_approve_scanned(path: &str) -> AppResult<ApproveReceiptResponse> {
    let stdout = run_backend(&["api", "approve-scanned", path])?;
    let response: ApproveReceiptResponse = serde_json::from_str(&stdout)?;
    if response.status != "approved" {
        return Err(format!("unexpected approve status: {}", response.status).into());
    }
    Ok(response)
}

fn backend_approve_scanned_with_review(
    path: &str,
    payload: &str,
) -> AppResult<ApproveReceiptResponse> {
    let stdout =
        run_backend_with_input(&["api", "approve-scanned-with-review", path], Some(payload))?;
    let response: ApproveReceiptResponse = serde_json::from_str(&stdout)?;
    if response.status != "approved" {
        return Err(format!("unexpected approve status: {}", response.status).into());
    }
    Ok(response)
}

fn backend_re_edit_approved_with_review(
    path: &str,
    payload: &str,
) -> AppResult<ReEditApprovedResponse> {
    let stdout = run_backend_with_input(
        &["api", "re-edit-approved-with-review", path],
        Some(payload),
    )?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_get_config() -> AppResult<ConfigResponse> {
    let stdout = run_backend(&["api", "get-config"])?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_set_config(project_root: &str) -> AppResult<ConfigResponse> {
    let payload = serde_json::json!({
        "project_root": project_root,
    });
    let stdout = run_backend_with_input(
        &["api", "set-config"],
        Some(&serde_json::to_string(&payload)?),
    )?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_match_candidates(path: &str) -> AppResult<MatchCandidatesResponse> {
    let stdout = run_backend(&["api", "match-candidates", path])?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_apply_match(
    path: &str,
    file_path: &str,
    line_number: i32,
) -> AppResult<ApplyMatchResponse> {
    let payload = serde_json::json!({
        "file_path": file_path,
        "line_number": line_number,
    });
    let stdout = run_backend_with_input(
        &["api", "apply-match", path],
        Some(&serde_json::to_string(&payload)?),
    )?;
    let response: ApplyMatchResponse = serde_json::from_str(&stdout)?;
    match response.status.as_str() {
        "applied" | "already_applied" => Ok(response),
        _ => Err(response
            .message
            .clone()
            .unwrap_or_else(|| format!("Match failed: {}", response.status))
            .into()),
    }
}

fn run_backend_interactive(args: &[&str]) -> AppResult<i32> {
    let backend = backend_command();
    let (program, program_args) = backend
        .split_first()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Empty backend command"))?;
    let status = Command::new(program)
        .args(program_args)
        .args(args)
        .stdin(std::process::Stdio::inherit())
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit())
        .status()?;
    Ok(status.code().unwrap_or(1))
}

fn render_detail_lines(detail: &ShowReceiptResponse) -> Vec<String> {
    let mut lines = vec![
        format!(
            "Merchant: {}",
            detail.summary.merchant.as_deref().unwrap_or("UNKNOWN")
        ),
        format!(
            "Date: {}",
            detail.summary.date.as_deref().unwrap_or("UNKNOWN")
        ),
        format!(
            "Total: {}",
            detail.summary.total.as_deref().unwrap_or("UNKNOWN")
        ),
        format!("Receipt Dir: {}", detail.summary.receipt_dir),
        format!("Stage File: {}", detail.summary.stage_file),
        String::new(),
        "Stage JSON".to_string(),
    ];

    match serde_json::to_string_pretty(&detail.document) {
        Ok(json) => lines.extend(json.lines().map(ToOwned::to_owned)),
        Err(error) => lines.push(format!("Failed to render JSON: {error}")),
    }
    lines
}

fn render_app(frame: &mut ratatui::Frame<'_>, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .split(frame.area());

    let tabs = Tabs::new(["Scanned", "Approved"])
        .block(Block::default().borders(Borders::ALL).title("Queues"))
        .select(app.active_queue.tab_index())
        .highlight_style(
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        );
    frame.render_widget(tabs, chunks[0]);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
        .split(chunks[1]);

    let items: Vec<ListItem> = app
        .receipts(app.active_queue)
        .iter()
        .map(|receipt| {
            let line = format!(
                "{}  {}  {}",
                receipt.date.as_deref().unwrap_or("UNKNOWN"),
                receipt.total.as_deref().unwrap_or("UNKNOWN"),
                receipt.merchant.as_deref().unwrap_or("UNKNOWN"),
            );
            ListItem::new(Line::from(line))
        })
        .collect();
    let list_title = format!(
        "{} ({})",
        app.active_queue.title(),
        app.receipts(app.active_queue).len()
    );
    let list = List::new(items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(list_title)
                .border_style(if app.focus == PaneFocus::List {
                    Style::default().fg(Color::Yellow)
                } else {
                    Style::default()
                }),
        )
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    match app.active_queue {
        Queue::Scanned => frame.render_stateful_widget(list, body[0], &mut app.scanned_state),
        Queue::Approved => frame.render_stateful_widget(list, body[0], &mut app.approved_state),
    }

    let detail_title = app.right_pane_title();
    let detail = Paragraph::new(Text::from(
        app.right_pane_lines()
            .iter()
            .cloned()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(detail_title)
            .border_style(if app.focus == PaneFocus::Detail {
                Style::default().fg(Color::Yellow)
            } else {
                Style::default()
            }),
    )
    .scroll((app.detail_scroll_y, app.detail_scroll_x))
    .wrap(Wrap { trim: false });
    frame.render_widget(detail, body[1]);

    let footer = Paragraph::new(app.status.clone())
        .block(Block::default().borders(Borders::ALL).title("Status"))
        .wrap(Wrap { trim: true });
    frame.render_widget(footer, chunks[2]);

    if let Some(edit_state) = &app.edit_state {
        render_edit_modal(frame, edit_state);
    }
    if let Some(config_state) = &app.config_state {
        render_config_modal(frame, &app.config, config_state);
    }
    if let Some(match_state) = &mut app.match_state {
        render_match_modal(frame, match_state);
    }
}

fn render_edit_modal(frame: &mut ratatui::Frame<'_>, edit_state: &EditState) {
    let popup = centered_rect(70, 14, frame.area());
    frame.render_widget(
        Block::default().style(Style::default().bg(Color::Black)),
        popup,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(1),
        ])
        .split(popup);

    let block = Block::default()
        .borders(Borders::ALL)
        .title("Review And Approve");
    frame.render_widget(block, popup);

    for (row, field, value) in [
        (rows[0], EditField::Merchant, edit_state.merchant.as_str()),
        (rows[1], EditField::Date, edit_state.date.as_str()),
        (rows[2], EditField::Total, edit_state.total.as_str()),
    ] {
        let style = if edit_state.active_field == field {
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default()
        };
        let paragraph = Paragraph::new(format!("{}: {}", field.label(), value))
            .block(Block::default().borders(Borders::BOTTOM))
            .style(style);
        frame.render_widget(paragraph, row);
    }

    let help = Paragraph::new("Enter save | Esc cancel | Backspace delete | Tab move")
        .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[3]);
}

fn render_config_modal(
    frame: &mut ratatui::Frame<'_>,
    config: &ConfigResponse,
    config_state: &ConfigState,
) {
    let popup = centered_rect(72, 18, frame.area());
    frame.render_widget(Clear, popup);

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(6),
            Constraint::Length(2),
            Constraint::Min(1),
        ])
        .split(popup);

    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title("Ledger Configuration")
            .style(Style::default().bg(Color::Black)),
        popup,
    );

    let intro = Paragraph::new("Set the BeanBeaver project root used for receipts and matching.")
        .style(Style::default().fg(Color::Gray))
        .wrap(Wrap { trim: true });
    frame.render_widget(intro, rows[0]);

    let input_value = if config_state.project_root.is_empty() {
        "<auto-detect from cwd>".to_string()
    } else {
        config_state.project_root.clone()
    };
    let input = Paragraph::new(input_value)
        .block(Block::default().borders(Borders::ALL).title("Project Root"))
        .style(
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(input, rows[1]);

    let resolved = Paragraph::new(config.resolved_project_root.clone())
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Resolved Project Root"),
        )
        .wrap(Wrap { trim: true });
    frame.render_widget(resolved, rows[2]);

    let saved_in = Paragraph::new(format!(
        "main.beancount: {}\nscanned: {}\napproved: {}\nconfig: {}",
        config.resolved_main_beancount_path,
        config.scanned_dir,
        config.approved_dir,
        config.config_path
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Derived Paths"),
    )
    .style(Style::default().fg(Color::Gray))
    .wrap(Wrap { trim: true });
    frame.render_widget(saved_in, rows[3]);

    let help =
        Paragraph::new("Enter save  |  Esc cancel  |  Backspace delete").wrap(Wrap { trim: true });
    frame.render_widget(help, rows[4]);
}

fn render_match_modal(frame: &mut ratatui::Frame<'_>, match_state: &mut MatchState) {
    let popup = centered_rect(84, 18, frame.area());
    frame.render_widget(Clear, popup);

    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title("Match Approved Receipt")
            .style(Style::default().bg(Color::Black)),
        popup,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Min(8),
            Constraint::Length(2),
            Constraint::Length(2),
        ])
        .split(popup);

    let intro = Paragraph::new(format!("Ledger: {}", match_state.ledger_path))
        .style(Style::default().fg(Color::Gray))
        .wrap(Wrap { trim: true });
    frame.render_widget(intro, rows[0]);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
        .split(rows[1]);

    let items: Vec<ListItem> = match_state
        .candidates
        .iter()
        .map(|candidate| {
            let amount = candidate.amount.as_deref().unwrap_or("UNKNOWN");
            let line = format!(
                "{}  {}  {:.0}%",
                candidate.date,
                amount,
                candidate.confidence * 100.0
            );
            ListItem::new(Line::from(line))
        })
        .collect();
    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title("Candidates"))
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(list, body[0], &mut match_state.state);

    let detail_text = match match_state.selected() {
        Some(candidate) => format!(
            "{}\n\nFile: {}:{}\nPayee: {}\nNarration: {}",
            candidate.display,
            candidate.file_path,
            candidate.line_number,
            candidate.payee.as_deref().unwrap_or("UNKNOWN"),
            candidate.narration.as_deref().unwrap_or(""),
        ),
        None => "No candidate selected.".to_string(),
    };
    let detail = Paragraph::new(detail_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Selected Candidate"),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(detail, body[1]);

    let warning = Paragraph::new(
        match_state
            .warning
            .clone()
            .unwrap_or_else(|| "Enter apply  |  Esc cancel  |  j/k move".to_string()),
    )
    .style(Style::default().fg(Color::Gray))
    .wrap(Wrap { trim: true });
    frame.render_widget(warning, rows[2]);

    let help =
        Paragraph::new("Enter apply selected match  |  Esc cancel").wrap(Wrap { trim: true });
    frame.render_widget(help, rows[3]);
}

fn centered_rect(
    width_percent: u16,
    height: u16,
    area: ratatui::layout::Rect,
) -> ratatui::layout::Rect {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Min(1),
            Constraint::Length(height),
            Constraint::Min(1),
        ])
        .split(area);
    let horizontal = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - width_percent) / 2),
            Constraint::Percentage(width_percent),
            Constraint::Percentage((100 - width_percent) / 2),
        ])
        .split(vertical[1]);
    horizontal[1]
}

fn setup_terminal() -> AppResult<Terminal<CrosstermBackend<Stdout>>> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    Ok(Terminal::new(backend)?)
}

fn restore_terminal(mut terminal: Terminal<CrosstermBackend<Stdout>>) -> AppResult<()> {
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    Ok(())
}

fn suspend_terminal(terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> AppResult<()> {
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    Ok(())
}

fn resume_terminal(terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> AppResult<()> {
    enable_raw_mode()?;
    execute!(terminal.backend_mut(), EnterAlternateScreen)?;
    terminal.hide_cursor()?;
    terminal.clear()?;
    Ok(())
}

fn run_app(terminal: &mut Terminal<CrosstermBackend<Stdout>>, app: &mut App) -> AppResult<()> {
    loop {
        terminal.draw(|frame| render_app(frame, app))?;
        if app.should_quit {
            return Ok(());
        }

        if !event::poll(Duration::from_millis(200))? {
            continue;
        }

        let Event::Key(key) = event::read()? else {
            continue;
        };
        if key.kind != KeyEventKind::Press {
            continue;
        }

        if app.edit_state.is_some() {
            match key.code {
                KeyCode::Esc => {
                    app.edit_state = None;
                    app.edit_mode = None;
                    app.set_status("Review cancelled");
                }
                KeyCode::Enter => {
                    if let Err(error) = app.apply_edit_changes() {
                        app.set_error(error.to_string());
                    }
                }
                KeyCode::Tab | KeyCode::Down => {
                    if let Some(edit_state) = app.edit_state.as_mut() {
                        edit_state.active_field = edit_state.active_field.next();
                    }
                }
                KeyCode::BackTab | KeyCode::Up => {
                    if let Some(edit_state) = app.edit_state.as_mut() {
                        edit_state.active_field = edit_state.active_field.previous();
                    }
                }
                KeyCode::Backspace => {
                    if let Some(edit_state) = app.edit_state.as_mut() {
                        edit_state.active_value_mut().pop();
                    }
                }
                KeyCode::Char(ch) => {
                    if let Some(edit_state) = app.edit_state.as_mut() {
                        edit_state.active_value_mut().push(ch);
                    }
                }
                _ => {}
            }
            continue;
        }

        if app.config_state.is_some() {
            match key.code {
                KeyCode::Esc => {
                    app.config_state = None;
                    app.set_status("Configuration cancelled");
                }
                KeyCode::Enter => {
                    if let Err(error) = app.apply_config() {
                        app.set_error(error.to_string());
                    }
                }
                KeyCode::Backspace => {
                    if let Some(config_state) = app.config_state.as_mut() {
                        config_state.project_root.pop();
                    }
                }
                KeyCode::Char(ch) => {
                    if let Some(config_state) = app.config_state.as_mut() {
                        config_state.project_root.push(ch);
                    }
                }
                _ => {}
            }
            continue;
        }

        if app.match_state.is_some() {
            match key.code {
                KeyCode::Esc => {
                    app.match_state = None;
                    app.set_status("Match cancelled");
                }
                KeyCode::Enter => {
                    if let Err(error) = app.apply_selected_match() {
                        app.set_error(error.to_string());
                    }
                }
                KeyCode::Down | KeyCode::Char('j') => {
                    if let Some(match_state) = app.match_state.as_mut() {
                        match_state.move_selection(1);
                    }
                }
                KeyCode::Up | KeyCode::Char('k') => {
                    if let Some(match_state) = app.match_state.as_mut() {
                        match_state.move_selection(-1);
                    }
                }
                _ => {}
            }
            continue;
        }

        match (key.code, key.modifiers) {
            (KeyCode::Char('q'), _) => app.should_quit = true,
            (KeyCode::Tab, _) => {
                app.switch_queue();
                if let Err(error) = app.load_detail() {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('s'), KeyModifiers::NONE) => {
                app.toggle_right_pane();
            }
            (KeyCode::Char('l'), KeyModifiers::NONE) => {
                app.focus_detail();
            }
            (KeyCode::Char('h'), KeyModifiers::NONE) => {
                app.focus_list();
            }
            (KeyCode::Down, _) | (KeyCode::Char('j'), KeyModifiers::NONE) => {
                if app.focus == PaneFocus::List {
                    app.move_selection(1);
                    if let Err(error) = app.load_detail() {
                        app.set_error(error.to_string());
                    }
                } else {
                    app.scroll_detail_vertical(1);
                }
            }
            (KeyCode::Up, _) | (KeyCode::Char('k'), KeyModifiers::NONE) => {
                if app.focus == PaneFocus::List {
                    app.move_selection(-1);
                    if let Err(error) = app.load_detail() {
                        app.set_error(error.to_string());
                    }
                } else {
                    app.scroll_detail_vertical(-1);
                }
            }
            (KeyCode::PageDown, _)
            | (KeyCode::Char('d'), KeyModifiers::CONTROL)
            | (KeyCode::Char('f'), KeyModifiers::CONTROL) => {
                app.scroll_detail_vertical(12);
            }
            (KeyCode::PageUp, _) | (KeyCode::Char('u'), KeyModifiers::CONTROL) => {
                app.scroll_detail_vertical(-12);
            }
            (KeyCode::Char('g'), KeyModifiers::NONE) => {
                app.scroll_detail_to_top();
            }
            (KeyCode::Char('G'), KeyModifiers::SHIFT) => {
                app.scroll_detail_to_bottom();
            }
            (KeyCode::Right, _) => {
                app.scroll_detail_horizontal(4);
            }
            (KeyCode::Left, _) => {
                app.scroll_detail_horizontal(-4);
            }
            (KeyCode::Char('r'), _) => {
                if let Err(error) = app.refresh() {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('a'), _) => {
                if let Err(error) = app.approve_selected_scanned() {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('e'), _) => app.begin_edit_selected(),
            (KeyCode::Char('m'), KeyModifiers::NONE) => {
                if let Err(error) = app.begin_match_selected_approved() {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('M'), KeyModifiers::SHIFT) => {
                match app.can_match_selected_approved() {
                    Ok(true) => {}
                    Ok(false) => continue,
                    Err(error) => {
                        app.set_error(error.to_string());
                        continue;
                    }
                }
                suspend_terminal(terminal)?;
                let match_result = run_backend_interactive(&["match"]);
                println!();
                match match_result {
                    Ok(exit_code) => {
                        println!("`bb match` exited with code {exit_code}.");
                    }
                    Err(error) => {
                        println!("Failed to run `bb match`: {error}");
                    }
                }
                print!("Press Enter to return to bb-tui...");
                io::stdout().flush()?;
                let mut input = String::new();
                io::stdin().read_line(&mut input)?;
                resume_terminal(terminal)?;
                if let Err(error) = app.refresh() {
                    app.set_error(error.to_string());
                    continue;
                }
            }
            (KeyCode::Char('c'), _) => app.begin_config_edit(),
            (KeyCode::Char('1'), _) | (KeyCode::Char('2'), _) => {
                let next = if key.code == KeyCode::Char('1') { 0 } else { 1 };
                app.active_queue = Queue::from_tab(next);
                if let Err(error) = app.load_detail() {
                    app.set_error(error.to_string());
                }
            }
            _ => {}
        }
    }
}

fn main() -> AppResult<()> {
    let mut terminal = setup_terminal()?;
    let result = (|| -> AppResult<()> {
        let mut app = App::new();
        app.refresh()?;
        run_app(&mut terminal, &mut app)
    })();
    restore_terminal(terminal)?;
    result
}
