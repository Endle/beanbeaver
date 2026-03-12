use std::collections::VecDeque;
use std::error::Error;
use std::io::{self, BufRead, BufReader, Read, Stdout, Write};
use std::net::TcpStream;
use std::path::Path;
use std::process::{Child, ExitStatus};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crossterm::event::{self, Event, KeyCode, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Tabs, Wrap};
use ratatui::Terminal;
use serde::Deserialize;
use serde_json::Value;

type AppResult<T> = Result<T, Box<dyn Error>>;

mod process_util {
    use super::{FAVA_HOST, FAVA_PORT, OCR_CONTAINER_NAME, SERVE_HOST};
    use std::collections::VecDeque;
    use std::ffi::OsStr;
    use std::io::{self, Write};
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

    pub(super) fn trash_file(path: &str) -> io::Result<Output> {
        run_program_output("trash", [path])
    }

    pub(super) fn podman_start_ocr() -> io::Result<Output> {
        run_program_output("podman", ["start", OCR_CONTAINER_NAME])
    }

    pub(super) fn podman_stop_ocr() -> io::Result<Output> {
        run_program_output("podman", ["stop", OCR_CONTAINER_NAME])
    }

    pub(super) fn podman_restart_ocr() -> io::Result<Output> {
        run_program_output("podman", ["restart", OCR_CONTAINER_NAME])
    }

    pub(super) fn podman_container_exists() -> io::Result<Output> {
        run_program_output("podman", ["container", "exists", OCR_CONTAINER_NAME])
    }

    pub(super) fn podman_inspect_running() -> io::Result<Output> {
        run_program_output(
            "podman",
            [
                "inspect",
                "--format",
                "{{.State.Running}}",
                OCR_CONTAINER_NAME,
            ],
        )
    }

    pub(super) fn podman_inspect_ocr() -> io::Result<Output> {
        run_program_output("podman", ["inspect", OCR_CONTAINER_NAME])
    }

    pub(super) fn podman_logs_ocr_tail() -> io::Result<Output> {
        run_program_output("podman", ["logs", "--tail", "80", OCR_CONTAINER_NAME])
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

    fn run_interactive_full_command(
        command: &[String],
        extra_args: &[&str],
    ) -> io::Result<ExitStatus> {
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
}

const SERVE_HOST: &str = "0.0.0.0";
const SERVE_HEALTH_HOST: &str = "127.0.0.1";
const SERVE_PORT: u16 = 8080;
const FAVA_HOST: &str = "127.0.0.1";
const FAVA_PORT: u16 = 5000;
const OCR_CONTAINER_NAME: &str = "beanbeaver-ocr";
const MAX_RUNTIME_LOG_LINES: usize = 400;
const RECEIPTS_REFRESH_INTERVAL: Duration = Duration::from_secs(3);
const SERVE_REFRESH_INTERVAL: Duration = Duration::from_secs(1);
const FAVA_REFRESH_INTERVAL: Duration = Duration::from_secs(1);
const OCR_REFRESH_INTERVAL: Duration = Duration::from_secs(2);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Page {
    Receipts,
    Serve,
    Fava,
    Ocr,
    Imports,
}

impl Page {
    fn tab_index(self) -> usize {
        match self {
            Page::Receipts => 0,
            Page::Serve => 1,
            Page::Fava => 2,
            Page::Ocr => 3,
            Page::Imports => 4,
        }
    }
}

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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ImportPaneFocus {
    Routes,
    Accounts,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OcrContainerState {
    Missing,
    Running,
    Stopped,
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
struct CategoryOption {
    key: String,
    account: String,
}

impl CategoryOption {
    fn display_label(&self) -> String {
        if self.key.is_empty() {
            "<empty>".to_string()
        } else if self.account.is_empty() || self.account == self.key {
            self.key.clone()
        } else {
            format!("{}  ->  {}", self.key, self.account)
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
struct CategoryListResponse {
    categories: Vec<CategoryOption>,
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

#[derive(Clone, Debug, Deserialize)]
struct ImportRouteOption {
    csv_file: String,
    source_path: String,
    import_type: String,
    importer_id: String,
    rule_id: String,
    stage: u32,
}

impl ImportRouteOption {
    fn display_label(&self) -> String {
        format!(
            "{}  {}  {}",
            self.import_type_label(),
            self.importer_id,
            self.csv_file
        )
    }

    fn import_type_label(&self) -> &'static str {
        match self.import_type.as_str() {
            "cc" => "Credit Card",
            "chequing" => "Chequing",
            _ => "Unknown",
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
struct RefreshImportPageResponse {
    planner_status: String,
    has_uncommitted_changes: bool,
    #[serde(default)]
    routes: Vec<ImportRouteOption>,
    selected_source_path: Option<String>,
    account_resolution: Option<ResolveImportAccountsResponse>,
    planner_error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct ResolveImportAccountsResponse {
    status: String,
    #[serde(rename = "import_type")]
    _import_type: String,
    #[serde(rename = "csv_file")]
    _csv_file: String,
    #[serde(rename = "importer_id")]
    _importer_id: String,
    account_label: Option<String>,
    account_options: Option<Vec<String>>,
    as_of: Option<String>,
    error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct ApplyImportResponse {
    status: String,
    import_type: String,
    result_file_path: Option<String>,
    result_file_name: Option<String>,
    account: Option<String>,
    start_date: Option<String>,
    end_date: Option<String>,
    error: Option<String>,
    #[serde(default)]
    warnings: Vec<String>,
    #[serde(default)]
    validation_errors: Vec<String>,
    summary: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReviewPane {
    Items,
    Fields,
    Preview,
}

impl ReviewPane {
    fn next(self) -> Self {
        match self {
            ReviewPane::Items => ReviewPane::Fields,
            ReviewPane::Fields => ReviewPane::Preview,
            ReviewPane::Preview => ReviewPane::Items,
        }
    }

    fn previous(self) -> Self {
        match self {
            ReviewPane::Items => ReviewPane::Preview,
            ReviewPane::Fields => ReviewPane::Items,
            ReviewPane::Preview => ReviewPane::Fields,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReviewTab {
    Effective,
    Diff,
    Raw,
}

impl ReviewTab {
    fn next(self) -> Self {
        match self {
            ReviewTab::Effective => ReviewTab::Diff,
            ReviewTab::Diff => ReviewTab::Raw,
            ReviewTab::Raw => ReviewTab::Effective,
        }
    }

    fn title(self) -> &'static str {
        match self {
            ReviewTab::Effective => "Effective Preview",
            ReviewTab::Diff => "Unsaved Diff",
            ReviewTab::Raw => "Raw Stage JSON",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReceiptReviewField {
    Merchant,
    Date,
    Subtotal,
    Tax,
    Total,
    Notes,
}

impl ReceiptReviewField {
    fn label(self) -> &'static str {
        match self {
            ReceiptReviewField::Merchant => "Merchant",
            ReceiptReviewField::Date => "Date",
            ReceiptReviewField::Subtotal => "Subtotal",
            ReceiptReviewField::Tax => "Tax",
            ReceiptReviewField::Total => "Total",
            ReceiptReviewField::Notes => "Notes",
        }
    }

    fn key(self) -> &'static str {
        match self {
            ReceiptReviewField::Merchant => "merchant",
            ReceiptReviewField::Date => "date",
            ReceiptReviewField::Subtotal => "subtotal",
            ReceiptReviewField::Tax => "tax",
            ReceiptReviewField::Total => "total",
            ReceiptReviewField::Notes => "notes",
        }
    }
}

#[derive(Clone, Debug)]
struct ReceiptFieldState {
    field: ReceiptReviewField,
    original: String,
    value: String,
}

#[derive(Clone, Debug)]
struct ReviewItemState {
    id: String,
    is_new: bool,
    original_description: String,
    description: String,
    original_price: String,
    price: String,
    quantity: String,
    original_category: String,
    category: String,
    original_notes: String,
    notes: String,
    original_removed: bool,
    removed: bool,
}

impl ReviewItemState {
    fn from_document(item: &Value) -> Option<Self> {
        let item_id = item.get("id")?;
        let id = json_value_to_text(Some(item_id));
        if id.is_empty() {
            return None;
        }
        Some(Self {
            id,
            is_new: false,
            original_description: effective_item_text(item, "description"),
            description: effective_item_text(item, "description"),
            original_price: effective_item_text(item, "price"),
            price: effective_item_text(item, "price"),
            quantity: effective_item_text(item, "quantity"),
            original_category: effective_item_category_text(item),
            category: effective_item_category_text(item),
            original_notes: effective_item_text(item, "notes"),
            notes: effective_item_text(item, "notes"),
            original_removed: effective_item_removed(item),
            removed: effective_item_removed(item),
        })
    }

    fn new_added(id: String) -> Self {
        Self {
            id,
            is_new: true,
            original_description: String::new(),
            description: String::new(),
            original_price: String::new(),
            price: String::new(),
            quantity: "1".to_string(),
            original_category: String::new(),
            category: String::new(),
            original_notes: String::new(),
            notes: String::new(),
            original_removed: false,
            removed: false,
        }
    }

    fn has_meaningful_content(&self) -> bool {
        !self.description.trim().is_empty()
            || !self.price.trim().is_empty()
            || !self.category.trim().is_empty()
            || !self.notes.trim().is_empty()
    }
}

#[derive(Clone, Debug)]
enum ReviewEditTarget {
    ReceiptField(usize),
    ItemDescription(usize),
    ItemPrice(usize),
    ItemNotes(usize),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ItemEditorField {
    Description,
    Price,
    Category,
    Notes,
    Removed,
}

impl ItemEditorField {
    fn all() -> [Self; 5] {
        [
            ItemEditorField::Description,
            ItemEditorField::Price,
            ItemEditorField::Category,
            ItemEditorField::Notes,
            ItemEditorField::Removed,
        ]
    }

    fn label(self) -> &'static str {
        match self {
            ItemEditorField::Description => "Description",
            ItemEditorField::Price => "Price",
            ItemEditorField::Category => "Category",
            ItemEditorField::Notes => "Notes",
            ItemEditorField::Removed => "Removed",
        }
    }
}

#[derive(Clone, Debug)]
struct ItemEditorState {
    item_index: usize,
    field_state: ListState,
}

impl ItemEditorState {
    fn new(item_index: usize) -> Self {
        let mut field_state = ListState::default();
        field_state.select(Some(0));
        Self {
            item_index,
            field_state,
        }
    }

    fn selected_field(&self) -> ItemEditorField {
        let fields = ItemEditorField::all();
        self.field_state
            .selected()
            .and_then(|index| fields.get(index).copied())
            .unwrap_or(ItemEditorField::Description)
    }

    fn move_selection(&mut self, delta: isize) {
        let len = ItemEditorField::all().len();
        let current = self.field_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.field_state.select(Some(next));
    }

    fn select_field(&mut self, field: ItemEditorField) {
        let index = ItemEditorField::all()
            .iter()
            .position(|candidate| *candidate == field)
            .unwrap_or(0);
        self.field_state.select(Some(index));
    }
}

#[derive(Clone, Debug)]
struct CategoryPickerState {
    item_index: usize,
    category_state: ListState,
}

impl CategoryPickerState {
    const PAGE_STEP: isize = 8;

    fn new(item_index: usize, selected_index: usize) -> Self {
        let mut category_state = ListState::default();
        category_state.select(Some(selected_index));
        Self {
            item_index,
            category_state,
        }
    }

    fn move_selection(&mut self, delta: isize, len: usize) {
        if len == 0 {
            self.category_state.select(None);
            return;
        }
        let current = self.category_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.category_state.select(Some(next));
    }
}

#[derive(Clone, Debug)]
struct TextInputState {
    target: ReviewEditTarget,
    label: String,
    value: String,
    cursor: usize,
}

impl TextInputState {
    fn with_value(target: ReviewEditTarget, label: String, value: String) -> Self {
        let cursor = value.chars().count();
        Self {
            target,
            label,
            value,
            cursor,
        }
    }

    fn move_left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }

    fn move_right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.value.chars().count());
    }

    fn move_home(&mut self) {
        self.cursor = 0;
    }

    fn move_end(&mut self) {
        self.cursor = self.value.chars().count();
    }

    fn insert_char(&mut self, ch: char) {
        let idx = char_to_byte_index(&self.value, self.cursor);
        self.value.insert(idx, ch);
        self.cursor += 1;
    }

    fn backspace(&mut self) {
        if self.cursor == 0 {
            return;
        }
        let end = char_to_byte_index(&self.value, self.cursor);
        let start = char_to_byte_index(&self.value, self.cursor - 1);
        self.value.replace_range(start..end, "");
        self.cursor -= 1;
    }

    fn delete(&mut self) {
        let len = self.value.chars().count();
        if self.cursor >= len {
            return;
        }
        let start = char_to_byte_index(&self.value, self.cursor);
        let end = char_to_byte_index(&self.value, self.cursor + 1);
        self.value.replace_range(start..end, "");
    }
}

#[derive(Clone, Debug)]
struct ImportPageState {
    routes: Vec<ImportRouteOption>,
    route_state: ListState,
    focus: ImportPaneFocus,
    has_uncommitted_changes: bool,
    allow_uncommitted: bool,
    planner_status: String,
    planner_error: Option<String>,
    account_label: Option<String>,
    account_options: Vec<String>,
    account_state: ListState,
    account_as_of: Option<String>,
    account_error: Option<String>,
    last_result: Option<ApplyImportResponse>,
}

impl ImportPageState {
    fn new() -> Self {
        let mut route_state = ListState::default();
        route_state.select(None);
        let mut account_state = ListState::default();
        account_state.select(None);
        Self {
            routes: Vec::new(),
            route_state,
            focus: ImportPaneFocus::Routes,
            has_uncommitted_changes: false,
            allow_uncommitted: false,
            planner_status: "not_loaded".to_string(),
            planner_error: None,
            account_label: None,
            account_options: Vec::new(),
            account_state,
            account_as_of: None,
            account_error: None,
            last_result: None,
        }
    }

    fn selected_route(&self) -> Option<&ImportRouteOption> {
        self.route_state
            .selected()
            .and_then(|index| self.routes.get(index))
    }

    fn selected_account(&self) -> Option<&str> {
        self.account_state
            .selected()
            .and_then(|index| self.account_options.get(index))
            .map(String::as_str)
    }

    fn set_routes(&mut self, routes: Vec<ImportRouteOption>, preferred_source_path: Option<&str>) {
        let current_source = preferred_source_path
            .map(ToOwned::to_owned)
            .or_else(|| self.selected_route().map(|route| route.source_path.clone()));
        self.routes = routes;
        let selected_index = current_source.as_deref().and_then(|source_path| {
            self.routes
                .iter()
                .position(|route| route.source_path == source_path)
        });
        match (self.routes.len(), selected_index) {
            (0, _) => self.route_state.select(None),
            (_, Some(index)) => self.route_state.select(Some(index)),
            (_, None) => self.route_state.select(Some(0)),
        }
    }

    fn move_route_selection(&mut self, delta: isize) {
        let len = self.routes.len();
        if len == 0 {
            self.route_state.select(None);
            return;
        }
        let current = self.route_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.route_state.select(Some(next));
    }

    fn move_account_selection(&mut self, delta: isize) {
        let len = self.account_options.len();
        if len == 0 {
            self.account_state.select(None);
            return;
        }
        let current = self.account_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.account_state.select(Some(next));
    }

    fn clear_account_resolution(&mut self) {
        self.account_label = None;
        self.account_options.clear();
        self.account_state.select(None);
        self.account_as_of = None;
        self.account_error = None;
    }

    fn apply_account_resolution(
        &mut self,
        response: ResolveImportAccountsResponse,
        preferred_account: Option<&str>,
    ) {
        self.account_label = response.account_label;
        self.account_as_of = response.as_of;
        self.account_error = if response.status == "error" {
            response.error
        } else {
            None
        };
        self.account_options = response.account_options.unwrap_or_default();
        let selected_index = preferred_account.and_then(|account| {
            self.account_options
                .iter()
                .position(|candidate| candidate == account)
        });
        match (self.account_options.len(), selected_index) {
            (0, _) => self.account_state.select(None),
            (_, Some(index)) => self.account_state.select(Some(index)),
            (_, None) => self.account_state.select(Some(0)),
        }
    }

    fn detail_lines(&self) -> Vec<String> {
        let mut lines = vec![
            format!(
                "Planner Status: {}",
                match self.planner_status.as_str() {
                    "not_loaded" => "not loaded (press `r` to load routes)",
                    other => other,
                }
            ),
            format!(
                "Git Working Tree: {}",
                if self.has_uncommitted_changes {
                    "uncommitted changes detected"
                } else {
                    "clean"
                }
            ),
            format!(
                "Allow Import With Uncommitted Changes: {}",
                if self.allow_uncommitted { "yes" } else { "no" }
            ),
        ];

        if let Some(error) = &self.planner_error {
            lines.push(String::new());
            lines.push("Planner Error:".to_string());
            lines.extend(error.lines().map(ToOwned::to_owned));
        }

        if let Some(route) = self.selected_route() {
            lines.push(String::new());
            lines.push("Selected Route".to_string());
            lines.push(format!("CSV: {}", route.csv_file));
            lines.push(format!("Source: {}", route.source_path));
            lines.push(format!("Type: {}", route.import_type_label()));
            lines.push(format!("Importer: {}", route.importer_id));
            lines.push(format!("Rule: {}", route.rule_id));
            lines.push(format!("Stage: {}", route.stage));
        }

        if self.account_label.is_some() || self.account_error.is_some() {
            lines.push(String::new());
            lines.push("Account Resolution".to_string());
            if let Some(label) = &self.account_label {
                lines.push(format!("Label: {label}"));
            }
            if let Some(as_of) = &self.account_as_of {
                lines.push(format!("As Of: {as_of}"));
            }
            if let Some(account) = self.selected_account() {
                lines.push(format!("Selected Account: {account}"));
            }
            if let Some(error) = &self.account_error {
                lines.push("Error:".to_string());
                lines.extend(error.lines().map(ToOwned::to_owned));
            }
        }

        if let Some(result) = &self.last_result {
            lines.push(String::new());
            lines.push("Last Import Result".to_string());
            lines.push(format!("Status: {}", result.status));
            lines.push(format!("Import Type: {}", result.import_type));
            if let Some(summary) = &result.summary {
                lines.push(format!("Summary: {summary}"));
            }
            if let Some(account) = &result.account {
                lines.push(format!("Account: {account}"));
            }
            if let Some(start_date) = &result.start_date {
                lines.push(format!("Start Date: {start_date}"));
            }
            if let Some(end_date) = &result.end_date {
                lines.push(format!("End Date: {end_date}"));
            }
            if let Some(path) = &result.result_file_path {
                lines.push(format!("Result File: {path}"));
            }
            if let Some(name) = &result.result_file_name {
                lines.push(format!("Result File Name: {name}"));
            }
            if let Some(error) = &result.error {
                lines.push("Error:".to_string());
                lines.extend(error.lines().map(ToOwned::to_owned));
            }
            if !result.warnings.is_empty() {
                lines.push("Warnings:".to_string());
                lines.extend(result.warnings.iter().map(|warning| format!("- {warning}")));
            }
            if !result.validation_errors.is_empty() {
                lines.push("Validation Errors:".to_string());
                lines.extend(
                    result
                        .validation_errors
                        .iter()
                        .map(|error| format!("- {error}")),
                );
            }
        }

        lines
    }
}

struct ReviewState {
    source_queue: Queue,
    path: String,
    receipt_dir: String,
    stage_file: String,
    original_document: Value,
    pane: ReviewPane,
    preview_tab: ReviewTab,
    preview_scroll_y: u16,
    fields: Vec<ReceiptFieldState>,
    field_state: ListState,
    items: Vec<ReviewItemState>,
    item_state: ListState,
    category_options: Vec<CategoryOption>,
    item_editor: Option<ItemEditorState>,
    category_picker: Option<CategoryPickerState>,
    text_input: Option<TextInputState>,
    next_added_item_number: usize,
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

struct ManagedProcess {
    child: Child,
    command: String,
}

struct ServePageState {
    process: Option<ManagedProcess>,
    log_lines: Arc<Mutex<VecDeque<String>>>,
    health_ok: bool,
    health_message: String,
    last_exit_code: Option<i32>,
}

struct FavaPageState {
    process: Option<ManagedProcess>,
    log_lines: Arc<Mutex<VecDeque<String>>>,
    health_ok: bool,
    health_message: String,
    last_exit_code: Option<i32>,
}

struct OcrPageState {
    summary_lines: Vec<String>,
    log_lines: Vec<String>,
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

impl ServePageState {
    fn new() -> Self {
        let log_lines = Arc::new(Mutex::new(VecDeque::new()));
        replace_log_lines(
            &log_lines,
            vec![
                "Use `s` to start and `x` to stop the TUI-managed `bb serve` instance.".to_string(),
            ],
        );
        Self {
            process: None,
            log_lines,
            health_ok: false,
            health_message: "Health not checked yet".to_string(),
            last_exit_code: None,
        }
    }

    fn snapshot_logs(&self) -> Vec<String> {
        snapshot_log_lines(&self.log_lines)
    }
}

impl OcrPageState {
    fn new() -> Self {
        Self {
            summary_lines: vec!["Refreshing Podman container state...".to_string()],
            log_lines: vec!["No container logs loaded yet.".to_string()],
        }
    }
}

impl FavaPageState {
    fn new() -> Self {
        let log_lines = Arc::new(Mutex::new(VecDeque::new()));
        replace_log_lines(
            &log_lines,
            vec!["Use `s` to start and `x` to stop the TUI-managed Fava instance.".to_string()],
        );
        Self {
            process: None,
            log_lines,
            health_ok: false,
            health_message: "Health not checked yet".to_string(),
            last_exit_code: None,
        }
    }

    fn snapshot_logs(&self) -> Vec<String> {
        snapshot_log_lines(&self.log_lines)
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

impl ReviewState {
    fn from_detail(
        source_queue: Queue,
        detail: &ShowReceiptResponse,
        category_options: Vec<CategoryOption>,
    ) -> Self {
        let mut field_state = ListState::default();
        field_state.select(Some(0));
        let mut item_state = ListState::default();

        let document = &detail.document;
        let fields = [
            ReceiptReviewField::Merchant,
            ReceiptReviewField::Date,
            ReceiptReviewField::Subtotal,
            ReceiptReviewField::Tax,
            ReceiptReviewField::Total,
            ReceiptReviewField::Notes,
        ]
        .into_iter()
        .map(|field| {
            let value = effective_receipt_text(document, field.key());
            ReceiptFieldState {
                field,
                original: value.clone(),
                value,
            }
        })
        .collect::<Vec<_>>();

        let mut items = Vec::new();
        if let Some(item_docs) = document.get("items").and_then(Value::as_array) {
            for item in item_docs {
                if let Some(review_item) = ReviewItemState::from_document(item) {
                    items.push(review_item);
                }
            }
        }
        if !items.is_empty() {
            item_state.select(Some(0));
        }

        Self {
            source_queue,
            path: detail.path.clone(),
            receipt_dir: detail.summary.receipt_dir.clone(),
            stage_file: detail.summary.stage_file.clone(),
            original_document: detail.document.clone(),
            pane: ReviewPane::Items,
            preview_tab: ReviewTab::Effective,
            preview_scroll_y: 0,
            fields,
            field_state,
            items,
            item_state,
            category_options,
            item_editor: None,
            category_picker: None,
            text_input: None,
            next_added_item_number: 1,
        }
    }

    fn mode_label(&self) -> &'static str {
        match self.source_queue {
            Queue::Scanned => "Review Scanned Receipt",
            Queue::Approved => "Review Approved Receipt",
        }
    }

    fn submit_label(&self) -> &'static str {
        match self.source_queue {
            Queue::Scanned => "approve",
            Queue::Approved => "save",
        }
    }

    fn selected_field_index(&self) -> Option<usize> {
        self.field_state.selected()
    }

    fn selected_item_index(&self) -> Option<usize> {
        self.item_state.selected()
    }

    fn start_selected_field_edit(&mut self) {
        let Some(index) = self.selected_field_index() else {
            return;
        };
        if let Some(field) = self.fields.get(index) {
            self.text_input = Some(TextInputState::with_value(
                ReviewEditTarget::ReceiptField(index),
                field.field.label().to_string(),
                field.value.clone(),
            ));
        }
    }

    fn start_item_description_edit(&mut self, index: usize) {
        if let Some(item) = self.items.get(index) {
            self.text_input = Some(TextInputState::with_value(
                ReviewEditTarget::ItemDescription(index),
                format!("Item Description ({})", item.id),
                item.description.clone(),
            ));
        }
    }

    fn start_item_price_edit(&mut self, index: usize) {
        if let Some(item) = self.items.get(index) {
            self.text_input = Some(TextInputState::with_value(
                ReviewEditTarget::ItemPrice(index),
                format!("Item Price ({})", item.id),
                item.price.clone(),
            ));
        }
    }

    fn start_item_notes_edit(&mut self, index: usize) {
        if let Some(item) = self.items.get(index) {
            self.text_input = Some(TextInputState::with_value(
                ReviewEditTarget::ItemNotes(index),
                format!("Item Notes ({})", item.id),
                item.notes.clone(),
            ));
        }
    }

    fn open_selected_item_editor(&mut self) {
        let Some(index) = self.selected_item_index() else {
            return;
        };
        self.item_editor = Some(ItemEditorState::new(index));
    }

    fn item_editor_select_field(&mut self, field: ItemEditorField) {
        if self.item_editor.is_none() {
            self.open_selected_item_editor();
        }
        if let Some(editor) = self.item_editor.as_mut() {
            editor.select_field(field);
        }
    }

    fn open_selected_category_picker(&mut self) {
        let Some(index) = self.selected_item_index() else {
            return;
        };
        self.open_category_picker(index);
    }

    fn open_category_picker_from_item_editor(&mut self) {
        let Some(index) = self.item_editor.as_ref().map(|editor| editor.item_index) else {
            return;
        };
        self.open_category_picker(index);
    }

    fn open_category_picker(&mut self, index: usize) {
        let selected_index = self
            .items
            .get(index)
            .and_then(|item| {
                self.category_options
                    .iter()
                    .position(|option| option.key == item.category)
            })
            .unwrap_or(0);
        self.category_picker = Some(CategoryPickerState::new(index, selected_index));
    }

    fn next_added_item_id(&mut self) -> String {
        loop {
            let candidate = format!("item-added-{:04}", self.next_added_item_number);
            self.next_added_item_number += 1;
            if self.items.iter().all(|item| item.id != candidate) {
                return candidate;
            }
        }
    }

    fn add_item(&mut self) -> String {
        let id = self.next_added_item_id();
        self.items.push(ReviewItemState::new_added(id.clone()));
        let index = self.items.len().saturating_sub(1);
        self.item_state.select(Some(index));
        self.item_editor = Some(ItemEditorState::new(index));
        id
    }

    fn toggle_item_removed(&mut self, index: usize) -> Option<String> {
        let item = self.items.get_mut(index)?;
        item.removed = !item.removed;
        Some(format!(
            "{} {}",
            item.id,
            if item.removed {
                "marked removed"
            } else {
                "restored"
            }
        ))
    }

    fn toggle_item_editor_removed(&mut self) -> Option<String> {
        let item_index = self.item_editor.as_ref()?.item_index;
        self.toggle_item_removed(item_index)
    }

    fn apply_selected_category(&mut self) -> Option<String> {
        let (item_index, category_index) = {
            let picker = self.category_picker.as_ref()?;
            (
                picker.item_index,
                picker.category_state.selected().unwrap_or(0),
            )
        };
        let selected = self.category_options.get(category_index)?;
        let item = self.items.get_mut(item_index)?;
        item.category = selected.key.clone();
        self.category_picker = None;
        Some(format!(
            "{} category set to {}",
            item.id,
            if selected.key.is_empty() {
                "<empty>"
            } else {
                selected.key.as_str()
            }
        ))
    }

    fn activate_item_editor_selection(&mut self) -> Option<String> {
        let (item_index, field) = {
            let editor = self.item_editor.as_ref()?;
            (editor.item_index, editor.selected_field())
        };
        match field {
            ItemEditorField::Description => {
                self.start_item_description_edit(item_index);
                Some("Editing item description".to_string())
            }
            ItemEditorField::Price => {
                self.start_item_price_edit(item_index);
                Some("Editing item price".to_string())
            }
            ItemEditorField::Category => {
                self.open_category_picker(item_index);
                Some("Selecting item category".to_string())
            }
            ItemEditorField::Notes => {
                self.start_item_notes_edit(item_index);
                Some("Editing item notes".to_string())
            }
            ItemEditorField::Removed => self.toggle_item_removed(item_index),
        }
    }

    fn commit_text_input(&mut self) {
        let Some(input) = self.text_input.take() else {
            return;
        };
        match input.target {
            ReviewEditTarget::ReceiptField(index) => {
                if let Some(field) = self.fields.get_mut(index) {
                    field.value = input.value;
                }
            }
            ReviewEditTarget::ItemDescription(index) => {
                if let Some(item) = self.items.get_mut(index) {
                    item.description = input.value;
                }
            }
            ReviewEditTarget::ItemPrice(index) => {
                if let Some(item) = self.items.get_mut(index) {
                    item.price = input.value;
                }
            }
            ReviewEditTarget::ItemNotes(index) => {
                if let Some(item) = self.items.get_mut(index) {
                    item.notes = input.value;
                }
            }
        }
    }

    fn payload(&self) -> Value {
        let mut review = serde_json::Map::new();
        for field in &self.fields {
            if field.value != field.original {
                review.insert(
                    field.field.key().to_string(),
                    Value::String(field.value.clone()),
                );
            }
        }

        let mut items = Vec::new();
        for item in &self.items {
            if item.is_new {
                if !item.has_meaningful_content() {
                    continue;
                }
                let mut item_review = serde_json::Map::new();
                if !item.description.trim().is_empty() {
                    item_review.insert(
                        "description".to_string(),
                        Value::String(item.description.clone()),
                    );
                }
                if !item.price.trim().is_empty() {
                    item_review.insert("price".to_string(), Value::String(item.price.clone()));
                }
                if !item.category.trim().is_empty() {
                    item_review
                        .insert("category".to_string(), Value::String(item.category.clone()));
                }
                if !item.notes.trim().is_empty() {
                    item_review.insert("notes".to_string(), Value::String(item.notes.clone()));
                }
                if item.removed {
                    item_review.insert("removed".to_string(), Value::Bool(true));
                }
                items.push(serde_json::json!({
                    "id": item.id.clone(),
                    "create": true,
                    "review": Value::Object(item_review),
                }));
                continue;
            }
            let mut item_review = serde_json::Map::new();
            if item.description != item.original_description {
                item_review.insert(
                    "description".to_string(),
                    Value::String(item.description.clone()),
                );
            }
            if item.price != item.original_price {
                item_review.insert("price".to_string(), Value::String(item.price.clone()));
            }
            if item.category != item.original_category {
                item_review.insert("category".to_string(), Value::String(item.category.clone()));
            }
            if item.notes != item.original_notes {
                item_review.insert("notes".to_string(), Value::String(item.notes.clone()));
            }
            if item.removed != item.original_removed {
                item_review.insert("removed".to_string(), Value::Bool(item.removed));
            }
            if !item_review.is_empty() {
                items.push(serde_json::json!({
                    "id": item.id.clone(),
                    "review": Value::Object(item_review),
                }));
            }
        }

        serde_json::json!({
            "review": Value::Object(review),
            "items": items,
        })
    }

    fn effective_preview_lines(&self) -> Vec<String> {
        let mut lines = vec![
            format!("Receipt Dir: {}", self.receipt_dir),
            format!("Stage File: {}", self.stage_file),
            String::new(),
            "Receipt".to_string(),
        ];
        for field in &self.fields {
            let value = if field.value.trim().is_empty() {
                "<empty>"
            } else {
                field.value.as_str()
            };
            lines.push(format!("{:>8}: {}", field.field.label(), value));
        }
        lines.push(String::new());
        lines.push(format!(
            "Items ({})",
            self.items.iter().filter(|item| !item.removed).count()
        ));
        for (index, item) in self.items.iter().filter(|item| !item.removed).enumerate() {
            let category = if item.category.trim().is_empty() {
                "<uncategorized>"
            } else {
                item.category.as_str()
            };
            let quantity = if item.quantity.trim().is_empty() {
                "1"
            } else {
                item.quantity.as_str()
            };
            let new_item = if item.is_new { " [new]" } else { "" };
            lines.push(format!(
                "{:>2}. {}{}  x{}  ${}  [{}]",
                index + 1,
                item.description,
                new_item,
                quantity,
                if item.price.is_empty() {
                    "0.00"
                } else {
                    item.price.as_str()
                },
                category,
            ));
            if !item.notes.trim().is_empty() {
                lines.push(format!("     notes: {}", item.notes));
            }
        }
        let removed = self.items.iter().filter(|item| item.removed).count();
        if removed > 0 {
            lines.push(String::new());
            lines.push(format!("Removed items: {}", removed));
        }
        lines
    }

    fn diff_lines(&self) -> Vec<String> {
        let mut lines = Vec::new();
        for field in &self.fields {
            if field.value != field.original {
                lines.push(format!(
                    "{}: {} -> {}",
                    field.field.label(),
                    if field.original.is_empty() {
                        "<empty>"
                    } else {
                        field.original.as_str()
                    },
                    if field.value.is_empty() {
                        "<empty>"
                    } else {
                        field.value.as_str()
                    },
                ));
            }
        }
        for item in &self.items {
            if item.is_new {
                if !item.has_meaningful_content() {
                    continue;
                }
                let description = if item.description.trim().is_empty() {
                    "<empty>"
                } else {
                    item.description.as_str()
                };
                let price = if item.price.trim().is_empty() {
                    "<empty>"
                } else {
                    item.price.as_str()
                };
                let category = if item.category.trim().is_empty() {
                    "<empty>"
                } else {
                    item.category.as_str()
                };
                let removed = if item.removed { " [removed]" } else { "" };
                lines.push(format!(
                    "{} added: {} | {} | {}{}",
                    item.id, description, price, category, removed
                ));
                if !item.notes.trim().is_empty() {
                    lines.push(format!("{} notes: <empty> -> {}", item.id, item.notes));
                }
                continue;
            }
            if item.description != item.original_description {
                lines.push(format!(
                    "{} description: {} -> {}",
                    item.id, item.original_description, item.description
                ));
            }
            if item.price != item.original_price {
                lines.push(format!(
                    "{} price: {} -> {}",
                    item.id,
                    if item.original_price.is_empty() {
                        "<empty>"
                    } else {
                        item.original_price.as_str()
                    },
                    if item.price.is_empty() {
                        "<empty>"
                    } else {
                        item.price.as_str()
                    },
                ));
            }
            if item.category != item.original_category {
                lines.push(format!(
                    "{} category: {} -> {}",
                    item.id,
                    if item.original_category.is_empty() {
                        "<empty>"
                    } else {
                        item.original_category.as_str()
                    },
                    if item.category.is_empty() {
                        "<empty>"
                    } else {
                        item.category.as_str()
                    },
                ));
            }
            if item.notes != item.original_notes {
                lines.push(format!(
                    "{} notes: {} -> {}",
                    item.id,
                    if item.original_notes.is_empty() {
                        "<empty>"
                    } else {
                        item.original_notes.as_str()
                    },
                    if item.notes.is_empty() {
                        "<empty>"
                    } else {
                        item.notes.as_str()
                    },
                ));
            }
            if item.removed != item.original_removed {
                lines.push(format!(
                    "{} removed: {} -> {}",
                    item.id, item.original_removed, item.removed
                ));
            }
        }
        if lines.is_empty() {
            lines.push("No unsaved changes.".to_string());
        }
        lines
    }

    fn raw_json_lines(&self) -> Vec<String> {
        match serde_json::to_string_pretty(&self.original_document) {
            Ok(json) => json.lines().map(ToOwned::to_owned).collect(),
            Err(error) => vec![format!("Failed to render JSON: {error}")],
        }
    }

    fn preview_lines(&self) -> Vec<String> {
        match self.preview_tab {
            ReviewTab::Effective => self.effective_preview_lines(),
            ReviewTab::Diff => self.diff_lines(),
            ReviewTab::Raw => self.raw_json_lines(),
        }
    }
}

struct App {
    active_page: Page,
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
    review_state: Option<ReviewState>,
    config: ConfigResponse,
    config_state: Option<ConfigState>,
    match_state: Option<MatchState>,
    serve_state: ServePageState,
    fava_state: FavaPageState,
    ocr_state: OcrPageState,
    imports_state: ImportPageState,
    last_receipts_refresh: Option<Instant>,
    last_serve_refresh: Option<Instant>,
    last_fava_refresh: Option<Instant>,
    last_ocr_refresh: Option<Instant>,
    should_quit: bool,
}

impl App {
    fn new() -> Self {
        let mut scanned_state = ListState::default();
        scanned_state.select(Some(0));
        let mut approved_state = ListState::default();
        approved_state.select(Some(0));
        Self {
            active_page: Page::Receipts,
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
            status: Self::page_help(Page::Receipts).to_string(),
            review_state: None,
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
            serve_state: ServePageState::new(),
            fava_state: FavaPageState::new(),
            ocr_state: OcrPageState::new(),
            imports_state: ImportPageState::new(),
            last_receipts_refresh: None,
            last_serve_refresh: None,
            last_fava_refresh: None,
            last_ocr_refresh: None,
            should_quit: false,
        }
        .with_initial_status()
    }

    fn with_initial_status(mut self) -> Self {
        self.push_status_log(self.status.clone());
        self
    }

    fn page_help(page: Page) -> &'static str {
        match page {
            Page::Receipts => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | Tab switch queues | h/l pane focus | s toggle details/status | j/k move or scroll | e edit | m TUI match | M CLI match | arrows pan | r reload | a approve | c config | q quit"
            }
            Page::Serve => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | s start `bb serve` | x stop `bb serve` | R restart | r refresh health | q quit"
            }
            Page::Fava => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | s start Fava | x stop Fava | R restart | r refresh health | q quit"
            }
            Page::Ocr => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | s start container | x stop container | R restart container | r refresh podman status/logs | q quit"
            }
            Page::Imports => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | r load routes | h/l or Tab switch pane | j/k move | Enter reload accounts | v view csv | x trash csv | a apply import | u toggle allow-uncommitted | q quit"
            }
        }
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

    fn selected_path_for_queue(&self, queue: Queue) -> Option<String> {
        let receipts = self.receipts(queue);
        self.selected_index(queue)
            .and_then(|index| receipts.get(index))
            .map(|receipt| receipt.path.clone())
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

    fn sync_selection_to_path(&mut self, queue: Queue, preferred_path: Option<&str>) {
        let selected_index = preferred_path.and_then(|path| {
            self.receipts(queue)
                .iter()
                .position(|receipt| receipt.path == path)
        });

        match (self.receipts(queue).len(), selected_index) {
            (0, _) => self.list_state_mut(queue).select(None),
            (_, Some(index)) => self.list_state_mut(queue).select(Some(index)),
            (_, None) => self.list_state_mut(queue).select(Some(0)),
        }
    }

    fn select_receipt_by_path(&mut self, queue: Queue, path: &str) -> bool {
        let Some(index) = self
            .receipts(queue)
            .iter()
            .position(|receipt| receipt.path == path)
        else {
            return false;
        };
        self.list_state_mut(queue).select(Some(index));
        true
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

    fn switch_page(&mut self, page: Page) {
        if self.active_page == page {
            return;
        }
        self.active_page = page;
        self.set_status(Self::page_help(page));
    }

    fn set_status(&mut self, message: impl Into<String>) {
        let message = message.into();
        self.status = message.clone();
        self.push_status_log(message);
        if self.active_page == Page::Receipts && self.right_pane == RightPane::StatusLog {
            self.scroll_detail_to_bottom();
        }
    }

    fn set_error(&mut self, message: impl Into<String>) {
        self.show_status_log();
        self.set_status(message);
    }

    fn show_status_log(&mut self) {
        if self.active_page == Page::Receipts {
            self.right_pane = RightPane::StatusLog;
            self.scroll_detail_to_bottom();
        }
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
        self.last_receipts_refresh = Some(Instant::now());
        self.load_detail()?;
        self.refresh_runtime_pages(false)?;
        self.set_status(format!(
            "Loaded {} scanned / {} approved receipt(s)",
            self.scanned.len(),
            self.approved.len()
        ));
        Ok(())
    }

    fn reload_receipts(&mut self) -> AppResult<()> {
        let selected_scanned = self.selected_path_for_queue(Queue::Scanned);
        let selected_approved = self.selected_path_for_queue(Queue::Approved);
        self.scanned = backend_list_receipts(Queue::Scanned)?;
        self.approved = backend_list_receipts(Queue::Approved)?;
        self.sync_selection_to_path(Queue::Scanned, selected_scanned.as_deref());
        self.sync_selection_to_path(Queue::Approved, selected_approved.as_deref());
        Ok(())
    }

    fn refresh_receipts_page(&mut self, force: bool) -> AppResult<()> {
        if self.active_page != Page::Receipts
            || self.review_state.is_some()
            || self.config_state.is_some()
            || self.match_state.is_some()
        {
            return Ok(());
        }

        let now = Instant::now();
        if !force
            && self
                .last_receipts_refresh
                .is_some_and(|last| now.duration_since(last) < RECEIPTS_REFRESH_INTERVAL)
        {
            return Ok(());
        }

        let active_path_before = self.selected_path_for_queue(self.active_queue);
        let detail_path_before = self.detail_path.clone();
        self.reload_receipts()?;
        self.last_receipts_refresh = Some(now);

        let active_path_after = self.selected_path_for_queue(self.active_queue);
        if force
            || active_path_before != active_path_after
            || detail_path_before != active_path_after
        {
            self.load_detail()?;
        }

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
        self.detail_lines = render_detail_lines(self.active_queue, &detail);
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
        match backend_show_receipt(&receipt.path) {
            Ok(detail) => match backend_list_item_categories() {
                Ok(categories) => {
                    let mut category_options = vec![CategoryOption {
                        key: String::new(),
                        account: String::new(),
                    }];
                    category_options.extend(categories);
                    self.review_state = Some(ReviewState::from_detail(
                        self.active_queue,
                        &detail,
                        category_options,
                    ));
                    self.set_status(
                            "Review receipt: h/l switch pane | Enter item editor | i add item | v price | n notes | c choose category | x toggle removed | p preview | a submit | Esc cancel",
                        );
                }
                Err(error) => {
                    self.set_error(format!(
                        "Failed to load category options for receipt review: {error}"
                    ));
                }
            },
            Err(error) => self.set_error(format!("Failed to load receipt review state: {error}")),
        }
    }

    fn apply_review_changes(&mut self) -> AppResult<()> {
        let Some(review_state) = self.review_state.as_ref() else {
            self.set_status("Missing review state");
            return Ok(());
        };
        let payload = serde_json::to_string(&review_state.payload())?;
        let source_queue = review_state.source_queue;
        let source_path = review_state.path.clone();
        let result_path = match source_queue {
            Queue::Scanned => {
                let result = backend_approve_scanned_with_review(&source_path, &payload)?;
                result.approved_path
            }
            Queue::Approved => {
                let result = backend_re_edit_approved_with_review(&source_path, &payload)?;
                result
                    .updated_path
                    .ok_or_else(|| "missing updated path from approved re-edit".to_string())?
            }
        };
        self.review_state = None;
        self.refresh()?;
        self.active_queue = match source_queue {
            Queue::Scanned => Queue::Approved,
            Queue::Approved => Queue::Approved,
        };
        if !self.select_receipt_by_path(self.active_queue, &result_path) {
            self.sync_selection(self.active_queue);
        }
        self.focus = PaneFocus::List;
        self.load_detail()?;
        self.set_status(match source_queue {
            Queue::Scanned => format!("Approved {} -> {}", source_path, result_path),
            Queue::Approved => format!(
                "Saved approved review stage {} -> {}",
                source_path, result_path
            ),
        });
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

    fn refresh_imports_page(&mut self) -> AppResult<()> {
        let preferred_source = self
            .imports_state
            .selected_route()
            .map(|route| route.source_path.clone());
        let preferred_account = self.imports_state.selected_account().map(str::to_string);
        let response = backend_refresh_import_page(preferred_source.as_deref())?;
        self.imports_state.has_uncommitted_changes = response.has_uncommitted_changes;
        self.imports_state.planner_status = response.planner_status;
        self.imports_state.planner_error = response.planner_error;
        self.imports_state
            .set_routes(response.routes, response.selected_source_path.as_deref());

        match response.account_resolution {
            Some(account_resolution) => self
                .imports_state
                .apply_account_resolution(account_resolution, preferred_account.as_deref()),
            None => self.imports_state.clear_account_resolution(),
        }
        Ok(())
    }

    fn resolve_selected_import_accounts(&mut self) -> AppResult<()> {
        let selected_account = self.imports_state.selected_account().map(str::to_string);
        let Some(route) = self.imports_state.selected_route().cloned() else {
            self.imports_state.clear_account_resolution();
            self.set_status("No import route selected. Press `r` to load statement routes.");
            return Ok(());
        };
        self.imports_state.clear_account_resolution();
        let response = backend_resolve_import_accounts(
            &route.import_type,
            &route.csv_file,
            &route.importer_id,
        )?;
        self.imports_state
            .apply_account_resolution(response, selected_account.as_deref());
        Ok(())
    }

    fn move_import_route_selection(&mut self, delta: isize) -> AppResult<()> {
        let before = self
            .imports_state
            .selected_route()
            .map(|route| route.source_path.clone());
        self.imports_state.move_route_selection(delta);
        let after = self
            .imports_state
            .selected_route()
            .map(|route| route.source_path.clone());
        if before != after {
            self.resolve_selected_import_accounts()?;
        }
        Ok(())
    }

    fn apply_selected_import(&mut self) -> AppResult<()> {
        let Some(route) = self.imports_state.selected_route().cloned() else {
            self.set_status("No import route selected. Press `r` to load statement routes.");
            return Ok(());
        };
        let selected_account = self.imports_state.selected_account().map(ToOwned::to_owned);
        let response = backend_apply_import(
            &route.import_type,
            &route.csv_file,
            &route.importer_id,
            selected_account.as_deref(),
            self.imports_state.allow_uncommitted,
        )?;
        let status = match response.status.as_str() {
            "ok" => response
                .summary
                .clone()
                .unwrap_or_else(|| format!("Imported {}", route.csv_file)),
            "aborted" => response
                .error
                .clone()
                .unwrap_or_else(|| "Import aborted".to_string()),
            _ => response
                .error
                .clone()
                .unwrap_or_else(|| format!("Import failed: {}", response.status)),
        };
        self.imports_state.last_result = Some(response);
        self.refresh_imports_page()?;
        self.set_status(status);
        Ok(())
    }

    fn selected_import_source_path(&mut self) -> AppResult<Option<String>> {
        let Some(route) = self.imports_state.selected_route().cloned() else {
            self.set_status("No import route selected. Press `r` to load statement routes.");
            return Ok(None);
        };
        if Path::new(&route.source_path).exists() {
            return Ok(Some(route.source_path));
        }
        self.refresh_imports_page()?;
        self.set_status("Selected import file changed on disk; refreshed statement routes");
        Ok(None)
    }

    fn trash_selected_import_csv(&mut self) -> AppResult<()> {
        let Some(path) = self.selected_import_source_path()? else {
            return Ok(());
        };
        let output = process_util::trash_file(&path)?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let stdout = String::from_utf8_lossy(&output.stdout);
            return Err(format!(
                "trash failed for {}\nstdout:\n{}\nstderr:\n{}",
                path,
                stdout.trim(),
                stderr.trim()
            )
            .into());
        }
        self.refresh_imports_page()?;
        self.set_status(format!("Moved {} to Trash", path));
        Ok(())
    }

    fn refresh_current_page(&mut self) -> AppResult<()> {
        match self.active_page {
            Page::Receipts => self.refresh(),
            Page::Serve => {
                self.refresh_runtime_pages(true)?;
                self.set_status("Refreshed `bb serve` status and health");
                Ok(())
            }
            Page::Fava => {
                self.refresh_runtime_pages(true)?;
                self.set_status("Refreshed Fava status and health");
                Ok(())
            }
            Page::Ocr => {
                self.refresh_runtime_pages(true)?;
                self.set_status(format!(
                    "Refreshed Podman container `{}` status and logs",
                    OCR_CONTAINER_NAME
                ));
                Ok(())
            }
            Page::Imports => {
                self.refresh_imports_page()?;
                self.set_status("Refreshed statement import routes");
                Ok(())
            }
        }
    }

    fn refresh_runtime_pages(&mut self, force: bool) -> AppResult<()> {
        self.refresh_receipts_page(force)?;
        self.poll_serve_process()?;
        self.poll_fava_process()?;
        let now = Instant::now();

        if force
            || self
                .last_serve_refresh
                .is_none_or(|last| now.duration_since(last) >= SERVE_REFRESH_INTERVAL)
        {
            self.refresh_serve_health();
            self.last_serve_refresh = Some(now);
        }

        if force
            || self
                .last_fava_refresh
                .is_none_or(|last| now.duration_since(last) >= FAVA_REFRESH_INTERVAL)
        {
            self.refresh_fava_health();
            self.last_fava_refresh = Some(now);
        }

        if force
            || (self.active_page == Page::Ocr
                && self
                    .last_ocr_refresh
                    .is_none_or(|last| now.duration_since(last) >= OCR_REFRESH_INTERVAL))
        {
            self.refresh_ocr_page();
            self.last_ocr_refresh = Some(now);
        }

        Ok(())
    }

    fn refresh_serve_health(&mut self) {
        match check_local_health(SERVE_PORT, "/health", &[200]) {
            Ok(message) => {
                self.serve_state.health_ok = true;
                self.serve_state.health_message = message;
            }
            Err(error) => {
                self.serve_state.health_ok = false;
                self.serve_state.health_message = error;
            }
        }
    }

    fn refresh_fava_health(&mut self) {
        match check_local_health(FAVA_PORT, "/", &[200, 302, 303, 307, 308]) {
            Ok(message) => {
                self.fava_state.health_ok = true;
                self.fava_state.health_message = message;
            }
            Err(error) => {
                self.fava_state.health_ok = false;
                self.fava_state.health_message = error;
            }
        }
    }

    fn refresh_ocr_page(&mut self) {
        self.ocr_state = query_ocr_page_state();
    }

    fn start_ocr_container(&mut self) -> AppResult<()> {
        match podman_container_state()? {
            OcrContainerState::Running => {
                self.refresh_runtime_pages(true)?;
                self.set_status(format!(
                    "Podman container `{OCR_CONTAINER_NAME}` is already running"
                ));
            }
            OcrContainerState::Stopped => {
                ensure_podman_success(
                    process_util::podman_start_ocr()?,
                    "podman start beanbeaver-ocr",
                )?;
                self.refresh_runtime_pages(true)?;
                self.set_status(format!("Started Podman container `{OCR_CONTAINER_NAME}`"));
            }
            OcrContainerState::Missing => {
                self.set_status(format!(
                    "Container `{OCR_CONTAINER_NAME}` does not exist. Create it first with `podman run --replace --name beanbeaver-ocr --network=slirp4netns -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest`"
                ));
            }
        }
        Ok(())
    }

    fn stop_ocr_container(&mut self) -> AppResult<()> {
        match podman_container_state()? {
            OcrContainerState::Running => {
                ensure_podman_success(
                    process_util::podman_stop_ocr()?,
                    "podman stop beanbeaver-ocr",
                )?;
                self.refresh_runtime_pages(true)?;
                self.set_status(format!("Stopped Podman container `{OCR_CONTAINER_NAME}`"));
            }
            OcrContainerState::Stopped => {
                self.refresh_runtime_pages(true)?;
                self.set_status(format!(
                    "Podman container `{OCR_CONTAINER_NAME}` is already stopped"
                ));
            }
            OcrContainerState::Missing => {
                self.set_status(format!(
                    "Container `{OCR_CONTAINER_NAME}` does not exist. Create it first with `podman run --replace --name beanbeaver-ocr --network=slirp4netns -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest`"
                ));
            }
        }
        Ok(())
    }

    fn restart_ocr_container(&mut self) -> AppResult<()> {
        match podman_container_state()? {
            OcrContainerState::Running | OcrContainerState::Stopped => {
                ensure_podman_success(
                    process_util::podman_restart_ocr()?,
                    "podman restart beanbeaver-ocr",
                )?;
                self.refresh_runtime_pages(true)?;
                self.set_status(format!("Restarted Podman container `{OCR_CONTAINER_NAME}`"));
            }
            OcrContainerState::Missing => {
                self.set_status(format!(
                    "Container `{OCR_CONTAINER_NAME}` does not exist. Create it first with `podman run --replace --name beanbeaver-ocr --network=slirp4netns -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest`"
                ));
            }
        }
        Ok(())
    }

    fn poll_serve_process(&mut self) -> AppResult<()> {
        let exit_status = match self.serve_state.process.as_mut() {
            Some(process) => process.child.try_wait()?,
            None => None,
        };

        if let Some(status) = exit_status {
            let exit_code = exit_status_code(status);
            push_bounded_log_line(
                &self.serve_state.log_lines,
                format!("Process exited with code {exit_code}."),
            );
            self.serve_state.process = None;
            self.serve_state.last_exit_code = Some(exit_code);
            self.refresh_serve_health();
        }

        Ok(())
    }

    fn poll_fava_process(&mut self) -> AppResult<()> {
        let exit_status = match self.fava_state.process.as_mut() {
            Some(process) => process.child.try_wait()?,
            None => None,
        };

        if let Some(status) = exit_status {
            let exit_code = exit_status_code(status);
            push_bounded_log_line(
                &self.fava_state.log_lines,
                format!("Process exited with code {exit_code}."),
            );
            self.fava_state.process = None;
            self.fava_state.last_exit_code = Some(exit_code);
            self.refresh_fava_health();
        }

        Ok(())
    }

    fn restart_serve_process(&mut self) -> AppResult<()> {
        self.poll_serve_process()?;
        if self.serve_state.process.is_some() {
            self.stop_serve_process()?;
        }
        self.start_serve_process()
    }

    fn start_serve_process(&mut self) -> AppResult<()> {
        if self.serve_state.process.is_some() {
            self.set_status("A TUI-managed `bb serve` process is already running");
            return Ok(());
        }

        let command_line = process_util::backend_serve_command_line();

        replace_log_lines(
            &self.serve_state.log_lines,
            vec![format!("Starting managed process: {command_line}")],
        );

        let spawned = process_util::spawn_backend_serve(&self.serve_state.log_lines)?;
        let child = spawned.child;

        let pid = child.id();
        self.serve_state.process = Some(ManagedProcess {
            child,
            command: spawned.command_line,
        });
        self.serve_state.last_exit_code = None;
        push_bounded_log_line(
            &self.serve_state.log_lines,
            format!("Managed `bb serve` started with PID {pid}."),
        );
        self.refresh_serve_health();
        self.set_status(format!(
            "Started managed `bb serve` on http://{SERVE_HEALTH_HOST}:{SERVE_PORT}"
        ));
        Ok(())
    }

    fn restart_fava_process(&mut self) -> AppResult<()> {
        self.poll_fava_process()?;
        if self.fava_state.process.is_some() {
            self.stop_fava_process()?;
        }
        self.start_fava_process()
    }

    fn start_fava_process(&mut self) -> AppResult<()> {
        if self.fava_state.process.is_some() {
            self.set_status("A TUI-managed Fava process is already running");
            return Ok(());
        }
        if self.config.resolved_main_beancount_path.is_empty() {
            return Err("Resolved ledger path is empty; configure the project root first".into());
        }
        let ledger_path = Path::new(&self.config.resolved_main_beancount_path);
        if !ledger_path.exists() {
            return Err(format!("Ledger file not found: {}", ledger_path.display()).into());
        }

        let command_line =
            process_util::fava_command_line(&self.config.resolved_main_beancount_path);

        replace_log_lines(
            &self.fava_state.log_lines,
            vec![format!("Starting managed process: {command_line}")],
        );

        let spawned = process_util::spawn_fava(
            &self.config.resolved_main_beancount_path,
            &self.fava_state.log_lines,
        )?;
        let child = spawned.child;

        let pid = child.id();
        self.fava_state.process = Some(ManagedProcess {
            child,
            command: spawned.command_line,
        });
        self.fava_state.last_exit_code = None;
        push_bounded_log_line(
            &self.fava_state.log_lines,
            format!("Managed Fava started with PID {pid}."),
        );
        self.refresh_fava_health();
        self.set_status(format!(
            "Started managed Fava on http://{FAVA_HOST}:{FAVA_PORT}"
        ));
        Ok(())
    }

    fn stop_serve_process(&mut self) -> AppResult<()> {
        let Some(mut process) = self.serve_state.process.take() else {
            self.set_status("No TUI-managed `bb serve` process is running");
            return Ok(());
        };

        let exit_code = match process.child.try_wait()? {
            Some(status) => exit_status_code(status),
            None => {
                process.child.kill()?;
                exit_status_code(process.child.wait()?)
            }
        };

        push_bounded_log_line(
            &self.serve_state.log_lines,
            format!("Managed process stopped with code {exit_code}."),
        );
        self.serve_state.last_exit_code = Some(exit_code);
        self.refresh_serve_health();
        self.set_status(format!(
            "Stopped TUI-managed `bb serve` (exit code {exit_code})"
        ));
        Ok(())
    }

    fn stop_fava_process(&mut self) -> AppResult<()> {
        let Some(mut process) = self.fava_state.process.take() else {
            self.set_status("No TUI-managed Fava process is running");
            return Ok(());
        };

        let exit_code = match process.child.try_wait()? {
            Some(status) => exit_status_code(status),
            None => {
                process.child.kill()?;
                exit_status_code(process.child.wait()?)
            }
        };

        push_bounded_log_line(
            &self.fava_state.log_lines,
            format!("Managed process stopped with code {exit_code}."),
        );
        self.fava_state.last_exit_code = Some(exit_code);
        self.refresh_fava_health();
        self.set_status(format!("Stopped TUI-managed Fava (exit code {exit_code})"));
        Ok(())
    }

    fn shutdown(&mut self) -> AppResult<()> {
        if self.serve_state.process.is_some() {
            self.stop_serve_process()?;
        }
        if self.fava_state.process.is_some() {
            self.stop_fava_process()?;
        }
        Ok(())
    }
}

fn run_backend(args: &[&str]) -> AppResult<String> {
    run_backend_with_input(args, None)
}

fn run_backend_with_input(args: &[&str], stdin_input: Option<&str>) -> AppResult<String> {
    let (output, rendered_command) = process_util::run_backend_capture(args, stdin_input)?;

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

fn backend_list_item_categories() -> AppResult<Vec<CategoryOption>> {
    let stdout = run_backend(&["api", "list-item-categories"])?;
    let response: CategoryListResponse = serde_json::from_str(&stdout)?;
    Ok(response.categories)
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
    let response: ReEditApprovedResponse = serde_json::from_str(&stdout)?;
    if response.status != "updated" {
        return Err(format!(
            "unexpected re-edit status: {} ({})",
            response.status,
            response
                .normalize_error
                .as_deref()
                .unwrap_or("no normalize error provided")
        )
        .into());
    }
    Ok(response)
}

fn backend_get_config() -> AppResult<ConfigResponse> {
    let stdout = run_backend(&["api", "get-config"])?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_refresh_import_page(
    preferred_source_path: Option<&str>,
) -> AppResult<RefreshImportPageResponse> {
    let payload = serde_json::json!({
        "preferred_source_path": preferred_source_path,
    });
    let stdout = run_backend_with_input(
        &["api", "refresh-import-page"],
        Some(&serde_json::to_string(&payload)?),
    )?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_resolve_import_accounts(
    import_type: &str,
    csv_file: &str,
    importer_id: &str,
) -> AppResult<ResolveImportAccountsResponse> {
    let payload = serde_json::json!({
        "import_type": import_type,
        "csv_file": csv_file,
        "importer_id": importer_id,
    });
    let stdout = run_backend_with_input(
        &["api", "resolve-import-accounts"],
        Some(&serde_json::to_string(&payload)?),
    )?;
    Ok(serde_json::from_str(&stdout)?)
}

fn backend_apply_import(
    import_type: &str,
    csv_file: &str,
    importer_id: &str,
    selected_account: Option<&str>,
    allow_uncommitted: bool,
) -> AppResult<ApplyImportResponse> {
    let payload = serde_json::json!({
        "import_type": import_type,
        "csv_file": csv_file,
        "importer_id": importer_id,
        "selected_account": selected_account,
        "allow_uncommitted": allow_uncommitted,
    });
    let stdout = run_backend_with_input(
        &["api", "import-apply"],
        Some(&serde_json::to_string(&payload)?),
    )?;
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
    let status = process_util::run_backend_interactive(args)?;
    Ok(status.code().unwrap_or(1))
}

fn exit_status_code(status: ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

fn replace_log_lines(buffer: &Arc<Mutex<VecDeque<String>>>, lines: Vec<String>) {
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

fn push_bounded_log_line(buffer: &Arc<Mutex<VecDeque<String>>>, line: impl Into<String>) {
    let mut guard = buffer
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.push_back(line.into());
    while guard.len() > MAX_RUNTIME_LOG_LINES {
        guard.pop_front();
    }
}

fn snapshot_log_lines(buffer: &Arc<Mutex<VecDeque<String>>>) -> Vec<String> {
    let guard = buffer
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if guard.is_empty() {
        vec!["No logs yet.".to_string()]
    } else {
        guard.iter().cloned().collect()
    }
}

fn spawn_log_reader<R: Read + Send + 'static>(
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

fn check_local_health(port: u16, path: &str, success_codes: &[u16]) -> Result<String, String> {
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

fn ensure_podman_success(output: std::process::Output, rendered_command: &str) -> AppResult<()> {
    if output.status.success() {
        return Ok(());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!(
        "podman command failed: {rendered_command}\nstdout:\n{}\nstderr:\n{}",
        stdout.trim(),
        stderr.trim()
    )
    .into())
}

fn podman_container_state() -> AppResult<OcrContainerState> {
    let exists_output = process_util::podman_container_exists()?;
    match exists_output.status.code().unwrap_or(1) {
        0 => {}
        1 => return Ok(OcrContainerState::Missing),
        _ => {
            let stdout = String::from_utf8_lossy(&exists_output.stdout);
            let stderr = String::from_utf8_lossy(&exists_output.stderr);
            return Err(format!(
                "podman command failed: podman container exists {OCR_CONTAINER_NAME}\nstdout:\n{}\nstderr:\n{}",
                stdout.trim(),
                stderr.trim()
            )
            .into());
        }
    }

    let inspect_output = process_util::podman_inspect_running()?;
    if !inspect_output.status.success() {
        let stdout = String::from_utf8_lossy(&inspect_output.stdout);
        let stderr = String::from_utf8_lossy(&inspect_output.stderr);
        return Err(format!(
            "podman command failed: podman inspect --format {{{{.State.Running}}}} {OCR_CONTAINER_NAME}\nstdout:\n{}\nstderr:\n{}",
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

fn query_ocr_page_state() -> OcrPageState {
    let exists_output = match process_util::podman_container_exists() {
        Ok(output) => output,
        Err(error) => {
            return OcrPageState {
                summary_lines: vec![
                    "Podman unavailable".to_string(),
                    format!("Error: {error}"),
                    format!("Container: {OCR_CONTAINER_NAME}"),
                ],
                log_lines: vec![
                    "Install Podman or ensure `podman` is available on PATH.".to_string()
                ],
            };
        }
    };

    match exists_output.status.code().unwrap_or(1) {
        0 => {}
        1 => {
            return OcrPageState {
                summary_lines: vec![
                    "Podman available".to_string(),
                    format!("Container `{OCR_CONTAINER_NAME}` not found."),
                    "Suggested command:".to_string(),
                    "podman run --replace --name beanbeaver-ocr --network=slirp4netns -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest".to_string(),
                ],
                log_lines: vec!["No logs because the container does not exist.".to_string()],
            };
        }
        _ => {
            let stderr = String::from_utf8_lossy(&exists_output.stderr)
                .trim()
                .to_string();
            let stdout = String::from_utf8_lossy(&exists_output.stdout)
                .trim()
                .to_string();
            return OcrPageState {
                summary_lines: vec![
                    "Podman failed".to_string(),
                    format!("`podman container exists {OCR_CONTAINER_NAME}` returned a non-zero status."),
                    format!("stdout: {}", if stdout.is_empty() { "<empty>" } else { &stdout }),
                    format!("stderr: {}", if stderr.is_empty() { "<empty>" } else { &stderr }),
                ],
                log_lines: vec!["Unable to inspect container logs.".to_string()],
            };
        }
    }

    let inspect_output = match process_util::podman_inspect_ocr() {
        Ok(output) => output,
        Err(error) => {
            return OcrPageState {
                summary_lines: vec![
                    "Podman inspect failed".to_string(),
                    format!("Error: {error}"),
                ],
                log_lines: vec!["Unable to inspect container logs.".to_string()],
            };
        }
    };

    if !inspect_output.status.success() {
        let stderr = String::from_utf8_lossy(&inspect_output.stderr)
            .trim()
            .to_string();
        return OcrPageState {
            summary_lines: vec![
                "Podman inspect failed".to_string(),
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

    let summary_lines = match podman_summary_lines(&String::from_utf8_lossy(&inspect_output.stdout))
    {
        Ok(lines) => lines,
        Err(error) => vec![
            "Failed to parse `podman inspect` output".to_string(),
            format!("Error: {error}"),
        ],
    };

    let logs_output = match process_util::podman_logs_ocr_tail() {
        Ok(output) => output,
        Err(error) => {
            return OcrPageState {
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
            "Failed to fetch `podman logs`: {}",
            if stderr.is_empty() {
                "<empty>"
            } else {
                &stderr
            }
        )]
    };

    OcrPageState {
        summary_lines,
        log_lines,
    }
}

fn podman_summary_lines(raw_json: &str) -> AppResult<Vec<String>> {
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
        .map(format_podman_ports)
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

fn format_podman_ports(ports_value: &Value) -> String {
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

fn render_detail_lines(queue: Queue, detail: &ShowReceiptResponse) -> Vec<String> {
    let document = match queue {
        Queue::Scanned => detail.document.clone(),
        Queue::Approved => effective_detail_document(&detail.document),
    };

    let mut lines = vec![
        format!("Receipt Dir: {}", detail.summary.receipt_dir),
        format!("Stage File: {}", detail.summary.stage_file),
        String::new(),
    ];
    lines.extend(render_receipt_summary_lines(
        &document,
        match queue {
            Queue::Scanned => "Parsed Receipt",
            Queue::Approved => "Reviewed Receipt",
        },
    ));
    lines
}

fn render_receipt_summary_lines(document: &Value, title: &str) -> Vec<String> {
    let mut lines = vec![title.to_string(), String::new(), "Receipt".to_string()];

    let receipt_fields = [
        ("Merchant", effective_receipt_text(document, "merchant")),
        ("Date", effective_receipt_text(document, "date")),
        ("Currency", effective_receipt_text(document, "currency")),
        ("Subtotal", effective_receipt_text(document, "subtotal")),
        ("Tax", effective_receipt_text(document, "tax")),
        ("Total", effective_receipt_text(document, "total")),
        ("Notes", effective_receipt_text(document, "notes")),
    ];
    for (label, value) in receipt_fields {
        if !value.trim().is_empty() {
            lines.push(format!("{label}: {value}"));
        }
    }

    let visible_items: Vec<&Value> = document
        .get("items")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter(|item| !effective_item_removed(item))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    lines.push(String::new());
    lines.push(format!("Items ({})", visible_items.len()));
    if visible_items.is_empty() {
        lines.push("No items found".to_string());
    } else {
        for (index, item) in visible_items.iter().enumerate() {
            let description = effective_item_text(item, "description");
            let price = effective_item_text(item, "price");
            let category = effective_item_category_text(item);
            let quantity = effective_item_text(item, "quantity");
            let notes = effective_item_text(item, "notes");

            let mut item_parts = vec![format!(
                "{:>2}. {}",
                index + 1,
                if description.trim().is_empty() {
                    "<no description>"
                } else {
                    description.trim()
                }
            )];
            if !price.trim().is_empty() {
                item_parts.push(format!("${price}"));
            }
            if !category.trim().is_empty() {
                item_parts.push(category);
            }
            lines.push(item_parts.join("  |  "));

            if !quantity.trim().is_empty() {
                lines.push(format!("    Qty: {quantity}"));
            }
            if !notes.trim().is_empty() {
                lines.push(format!("    Notes: {notes}"));
            }

            let item_warnings = render_warning_values(item.get("warnings"));
            for warning in item_warnings {
                lines.push(format!("    Warning: {warning}"));
            }
        }
    }

    let receipt_warnings = render_warning_values(document.get("warnings"));
    if !receipt_warnings.is_empty() {
        lines.push(String::new());
        lines.push("Warnings".to_string());
        for warning in receipt_warnings {
            lines.push(format!("- {warning}"));
        }
    }

    lines
}

fn effective_detail_document(document: &Value) -> Value {
    let mut output = serde_json::Map::new();

    if let Some(meta) = document.get("meta") {
        output.insert("meta".to_string(), meta.clone());
    }

    let mut receipt = document
        .get("receipt")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    for key in ["merchant", "date", "subtotal", "tax", "total", "notes"] {
        let value = effective_receipt_text(document, key);
        if value.trim().is_empty() {
            receipt.remove(key);
        } else {
            receipt.insert(key.to_string(), Value::String(value));
        }
    }
    output.insert("receipt".to_string(), Value::Object(receipt));

    let mut items = Vec::new();
    if let Some(item_docs) = document.get("items").and_then(Value::as_array) {
        for item in item_docs {
            if effective_item_removed(item) {
                continue;
            }
            let mut effective_item = serde_json::Map::new();
            if let Some(id) = item.get("id") {
                effective_item.insert("id".to_string(), id.clone());
            }

            for key in ["description", "price", "quantity", "notes"] {
                let value = effective_item_text(item, key);
                if !value.trim().is_empty() {
                    effective_item.insert(key.to_string(), Value::String(value));
                }
            }

            if let Some(classification) = effective_item_classification(item) {
                effective_item.insert("classification".to_string(), classification);
            }

            if let Some(warnings) = item.get("warnings") {
                effective_item.insert("warnings".to_string(), warnings.clone());
            }

            items.push(Value::Object(effective_item));
        }
    }
    output.insert("items".to_string(), Value::Array(items));

    if let Some(warnings) = document.get("warnings") {
        output.insert("warnings".to_string(), warnings.clone());
    }
    if let Some(raw_text) = document.get("raw_text") {
        output.insert("raw_text".to_string(), raw_text.clone());
    }

    Value::Object(output)
}

fn json_value_to_text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::Bool(flag)) => flag.to_string(),
        _ => String::new(),
    }
}

fn render_warning_values(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(values)) => values
            .iter()
            .map(|warning| {
                let text = json_value_to_text(Some(warning));
                if text.is_empty() {
                    warning.to_string()
                } else {
                    text
                }
            })
            .collect(),
        Some(other) => {
            let text = json_value_to_text(Some(other));
            if text.is_empty() {
                vec![other.to_string()]
            } else {
                vec![text]
            }
        }
        None => Vec::new(),
    }
}

fn char_to_byte_index(text: &str, char_idx: usize) -> usize {
    text.char_indices()
        .nth(char_idx)
        .map(|(index, _)| index)
        .unwrap_or(text.len())
}

fn popup_style() -> Style {
    Style::default()
        .bg(Color::Rgb(235, 235, 235))
        .fg(Color::Black)
}

fn effective_receipt_text(document: &Value, key: &str) -> String {
    if let Some(value) = document
        .get("review")
        .and_then(Value::as_object)
        .and_then(|review| review.get(key))
    {
        if !value.is_null() {
            return json_value_to_text(Some(value));
        }
    }

    json_value_to_text(
        document
            .get("receipt")
            .and_then(Value::as_object)
            .and_then(|receipt| receipt.get(key)),
    )
}

fn effective_item_text(item: &Value, key: &str) -> String {
    if let Some(value) = item
        .get("review")
        .and_then(Value::as_object)
        .and_then(|review| review.get(key))
    {
        if !value.is_null() {
            return json_value_to_text(Some(value));
        }
    }

    json_value_to_text(item.get(key))
}

fn effective_item_category_text(item: &Value) -> String {
    if let Some(value) = item
        .get("review")
        .and_then(Value::as_object)
        .and_then(|review| review.get("classification"))
        .and_then(Value::as_object)
        .and_then(|classification| classification.get("category"))
    {
        if !value.is_null() {
            return json_value_to_text(Some(value));
        }
    }

    json_value_to_text(
        item.get("classification")
            .and_then(Value::as_object)
            .and_then(|classification| classification.get("category")),
    )
}

fn effective_item_classification(item: &Value) -> Option<Value> {
    let mut classification = item
        .get("classification")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if let Some(review_classification) = item
        .get("review")
        .and_then(Value::as_object)
        .and_then(|review| review.get("classification"))
        .and_then(Value::as_object)
    {
        classification.extend(review_classification.clone());
    }

    if classification.is_empty() {
        None
    } else {
        Some(Value::Object(classification))
    }
}

fn effective_item_removed(item: &Value) -> bool {
    item.get("review")
        .and_then(Value::as_object)
        .and_then(|review| review.get("removed"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn render_app(frame: &mut ratatui::Frame<'_>, app: &mut App) {
    frame.render_widget(Clear, frame.area());
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .split(frame.area());

    let tabs = Tabs::new([
        "Receipts [1]",
        "Serve [2]",
        "Fava [3]",
        "OCR [4]",
        "Imports [5]",
    ])
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Pages (press 1/2/3/4/5)"),
    )
    .select(app.active_page.tab_index())
    .highlight_style(
        Style::default()
            .fg(Color::Yellow)
            .add_modifier(Modifier::BOLD),
    );
    frame.render_widget(tabs, chunks[0]);

    if let Some(review_state) = &mut app.review_state {
        render_review_screen(frame, review_state, chunks[1]);
    } else {
        match app.active_page {
            Page::Receipts => render_receipts_page(frame, app, chunks[1]),
            Page::Serve => render_serve_page(frame, app, chunks[1]),
            Page::Fava => render_fava_page(frame, app, chunks[1]),
            Page::Ocr => render_ocr_page(frame, app, chunks[1]),
            Page::Imports => render_imports_page(frame, app, chunks[1]),
        }
    }

    let footer = Paragraph::new(app.status.clone())
        .block(Block::default().borders(Borders::ALL).title("Status"))
        .wrap(Wrap { trim: true });
    frame.render_widget(footer, chunks[2]);

    if let Some(config_state) = &app.config_state {
        render_config_modal(frame, &app.config, config_state);
    }
    if let Some(match_state) = &mut app.match_state {
        render_match_modal(frame, match_state);
    }
}

fn render_receipts_page(
    frame: &mut ratatui::Frame<'_>,
    app: &mut App,
    area: ratatui::layout::Rect,
) {
    let page_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(8)])
        .split(area);

    let tabs = Tabs::new(["Scanned", "Approved"])
        .block(Block::default().borders(Borders::ALL).title("Queues (Tab)"))
        .select(app.active_queue.tab_index())
        .highlight_style(
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        );
    frame.render_widget(tabs, page_chunks[0]);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
        .split(page_chunks[1]);

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
}

fn render_serve_page(frame: &mut ratatui::Frame<'_>, app: &App, area: ratatui::layout::Rect) {
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(9), Constraint::Min(8)])
        .split(area);

    let process_status = match app.serve_state.process.as_ref() {
        Some(process) => format!("Running (managed by this TUI, PID {})", process.child.id()),
        None if app.serve_state.health_ok => {
            "No managed process, but a healthy listener is responding".to_string()
        }
        None => "Stopped".to_string(),
    };
    let command = app
        .serve_state
        .process
        .as_ref()
        .map(|process| process.command.clone())
        .unwrap_or_else(process_util::backend_serve_command_line);
    let last_exit = app
        .serve_state
        .last_exit_code
        .map(|code| code.to_string())
        .unwrap_or_else(|| "n/a".to_string());
    let summary = Paragraph::new(format!(
        "Status: {process_status}\nHealth: {}\nEndpoints: http://{SERVE_HEALTH_HOST}:{SERVE_PORT}/upload | /beanbeaver | /bb\nCommand: {command}\nLast managed exit code: {last_exit}\nLifecycle: TUI-managed `bb serve` is terminated when `bb-tui` exits.",
        app.serve_state.health_message,
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("`bb serve` (`s` start, `x` stop, `R` restart)"),
    )
    .wrap(Wrap { trim: true });
    frame.render_widget(summary, sections[0]);

    let logs = Paragraph::new(Text::from(
        app.serve_state
            .snapshot_logs()
            .into_iter()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(Block::default().borders(Borders::ALL).title("Serve Logs"))
    .wrap(Wrap { trim: false });
    frame.render_widget(logs, sections[1]);
}

fn render_fava_page(frame: &mut ratatui::Frame<'_>, app: &App, area: ratatui::layout::Rect) {
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(9), Constraint::Min(8)])
        .split(area);

    let process_status = match app.fava_state.process.as_ref() {
        Some(process) => format!("Running (managed by this TUI, PID {})", process.child.id()),
        None if app.fava_state.health_ok => {
            "No managed process, but a healthy listener is responding".to_string()
        }
        None => "Stopped".to_string(),
    };
    let command = app
        .fava_state
        .process
        .as_ref()
        .map(|process| process.command.clone())
        .unwrap_or_else(|| {
            process_util::fava_command_line(&app.config.resolved_main_beancount_path)
        });
    let last_exit = app
        .fava_state
        .last_exit_code
        .map(|code| code.to_string())
        .unwrap_or_else(|| "n/a".to_string());
    let ledger_path = if app.config.resolved_main_beancount_path.is_empty() {
        "<unconfigured>".to_string()
    } else {
        app.config.resolved_main_beancount_path.clone()
    };
    let summary = Paragraph::new(format!(
        "Status: {process_status}\nHealth: {}\nURL: http://{FAVA_HOST}:{FAVA_PORT}\nLedger: {ledger_path}\nCommand: {command}\nLast managed exit code: {last_exit}\nLifecycle: TUI-managed Fava is terminated when `bb-tui` exits.",
        app.fava_state.health_message,
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Fava (`s` start, `x` stop, `R` restart)"),
    )
    .wrap(Wrap { trim: true });
    frame.render_widget(summary, sections[0]);

    let logs = Paragraph::new(Text::from(
        app.fava_state
            .snapshot_logs()
            .into_iter()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(Block::default().borders(Borders::ALL).title("Fava Logs"))
    .wrap(Wrap { trim: false });
    frame.render_widget(logs, sections[1]);
}

fn render_ocr_page(frame: &mut ratatui::Frame<'_>, app: &App, area: ratatui::layout::Rect) {
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(11), Constraint::Min(8)])
        .split(area);

    let summary = Paragraph::new(Text::from(
        app.ocr_state
            .summary_lines
            .iter()
            .cloned()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Podman Container (`s` start, `x` stop, `R` restart)"),
    )
    .wrap(Wrap { trim: true });
    frame.render_widget(summary, sections[0]);

    let logs = Paragraph::new(Text::from(
        app.ocr_state
            .log_lines
            .iter()
            .cloned()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(format!("`podman logs --tail 80 {OCR_CONTAINER_NAME}`")),
    )
    .wrap(Wrap { trim: false });
    frame.render_widget(logs, sections[1]);
}

fn render_imports_page(frame: &mut ratatui::Frame<'_>, app: &mut App, area: ratatui::layout::Rect) {
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(6), Constraint::Min(10)])
        .split(area);

    let summary = Paragraph::new(format!(
        "Detected routes: {}\nWorking tree: {}\nAllow import with uncommitted changes: {}\nSelected route: {}",
        app.imports_state.routes.len(),
        if app.imports_state.has_uncommitted_changes {
            "uncommitted changes detected"
        } else {
            "clean"
        },
        if app.imports_state.allow_uncommitted {
            "yes"
        } else {
            "no"
        },
        app.imports_state
            .selected_route()
            .map(|route| route.csv_file.as_str())
            .unwrap_or("<none>")
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Statement Import (`a` apply, `u` toggle allow-uncommitted)"),
    )
    .wrap(Wrap { trim: true });
    frame.render_widget(summary, sections[0]);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(sections[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(10), Constraint::Min(8)])
        .split(body[1]);

    let route_items: Vec<ListItem> = app
        .imports_state
        .routes
        .iter()
        .map(|route| ListItem::new(Line::from(route.display_label())))
        .collect();
    let routes = List::new(route_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!("Routes ({})", app.imports_state.routes.len()))
                .border_style(if app.imports_state.focus == ImportPaneFocus::Routes {
                    Style::default().fg(Color::Yellow)
                } else {
                    Style::default()
                }),
        )
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(routes, body[0], &mut app.imports_state.route_state);

    let account_items: Vec<ListItem> = app
        .imports_state
        .account_options
        .iter()
        .map(|account| ListItem::new(Line::from(account.clone())))
        .collect();
    let account_title = app
        .imports_state
        .account_label
        .clone()
        .unwrap_or_else(|| "Accounts".to_string());
    let accounts = List::new(account_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(account_title)
                .border_style(if app.imports_state.focus == ImportPaneFocus::Accounts {
                    Style::default().fg(Color::Yellow)
                } else {
                    Style::default()
                }),
        )
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(accounts, right[0], &mut app.imports_state.account_state);

    let details = Paragraph::new(Text::from(
        app.imports_state
            .detail_lines()
            .into_iter()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Import Details"),
    )
    .wrap(Wrap { trim: false });
    frame.render_widget(details, right[1]);
}

fn render_review_screen(
    frame: &mut ratatui::Frame<'_>,
    review_state: &mut ReviewState,
    area: ratatui::layout::Rect,
) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(2),
        ])
        .split(area);

    let header = Paragraph::new(format!(
        "{}  |  {} / {}",
        review_state.mode_label(),
        review_state.receipt_dir,
        review_state.stage_file
    ))
    .block(Block::default().borders(Borders::ALL).title("Review Mode"))
    .wrap(Wrap { trim: true });
    frame.render_widget(header, rows[0]);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
        .split(rows[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
        .split(body[1]);

    let item_lines: Vec<ListItem> = review_state
        .items
        .iter()
        .map(|item| {
            let removed = if item.removed { " [removed]" } else { "" };
            let new_item = if item.is_new { " [new]" } else { "" };
            let notes = if item.notes.trim().is_empty() {
                ""
            } else {
                " [note]"
            };
            let category = if item.category.trim().is_empty() {
                "<uncategorized>"
            } else {
                item.category.as_str()
            };
            ListItem::new(Line::from(format!(
                "{}{}  ${}  {}{}{}",
                item.description,
                new_item,
                if item.price.is_empty() {
                    "0.00"
                } else {
                    item.price.as_str()
                },
                category,
                notes,
                removed,
            )))
        })
        .collect();
    let items = List::new(item_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Items")
                .border_style(if review_state.pane == ReviewPane::Items {
                    Style::default().fg(Color::Yellow)
                } else {
                    Style::default()
                }),
        )
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(items, body[0], &mut review_state.item_state);

    let field_lines: Vec<ListItem> = review_state
        .fields
        .iter()
        .map(|field| {
            let changed = if field.value != field.original {
                " *"
            } else {
                ""
            };
            let value = if field.value.trim().is_empty() {
                "<empty>"
            } else {
                field.value.as_str()
            };
            ListItem::new(Line::from(format!(
                "{}: {}{}",
                field.field.label(),
                value,
                changed,
            )))
        })
        .collect();
    let fields = List::new(field_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Receipt Fields")
                .border_style(if review_state.pane == ReviewPane::Fields {
                    Style::default().fg(Color::Yellow)
                } else {
                    Style::default()
                }),
        )
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(fields, right[0], &mut review_state.field_state);

    let preview = Paragraph::new(Text::from(
        review_state
            .preview_lines()
            .into_iter()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(review_state.preview_tab.title())
            .border_style(if review_state.pane == ReviewPane::Preview {
                Style::default().fg(Color::Yellow)
            } else {
                Style::default()
            }),
    )
    .scroll((review_state.preview_scroll_y, 0))
    .wrap(Wrap { trim: false });
    frame.render_widget(preview, right[1]);

    let help = Paragraph::new(format!(
        "h/l pane  |  j/k move  |  Enter open item editor / edit field  |  i add item  |  v price  |  n notes  |  c category picker  |  x toggle removed  |  p preview tab  |  a {}  |  Esc cancel",
        review_state.submit_label()
    ))
    .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[2]);

    if review_state.item_editor.is_some() {
        render_item_editor_modal(frame, review_state);
    }
    if review_state.category_picker.is_some() {
        render_category_picker_modal(frame, review_state);
    }
    if let Some(text_input) = &review_state.text_input {
        render_text_input_modal(frame, text_input);
    }
}

fn render_item_editor_modal(frame: &mut ratatui::Frame<'_>, review_state: &mut ReviewState) {
    let Some(item_index) = review_state
        .item_editor
        .as_ref()
        .map(|editor| editor.item_index)
    else {
        return;
    };
    let Some(item) = review_state.items.get(item_index) else {
        return;
    };

    let popup = centered_rect(72, 11, frame.area());
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title(if item.is_new {
                format!("Edit New Item ({})", item.id)
            } else {
                format!("Edit Item ({})", item.id)
            })
            .style(popup_style()),
        popup,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(6), Constraint::Length(2)])
        .split(popup);

    let items = ItemEditorField::all()
        .into_iter()
        .map(|field| {
            let value = match field {
                ItemEditorField::Description => {
                    if item.description.trim().is_empty() {
                        "<empty>".to_string()
                    } else {
                        item.description.clone()
                    }
                }
                ItemEditorField::Price => {
                    if item.price.trim().is_empty() {
                        "<empty>".to_string()
                    } else {
                        item.price.clone()
                    }
                }
                ItemEditorField::Category => {
                    if item.category.trim().is_empty() {
                        "<empty>".to_string()
                    } else {
                        item.category.clone()
                    }
                }
                ItemEditorField::Notes => {
                    if item.notes.trim().is_empty() {
                        "<empty>".to_string()
                    } else {
                        item.notes.clone()
                    }
                }
                ItemEditorField::Removed => item.removed.to_string(),
            };
            ListItem::new(Line::from(format!("{}: {}", field.label(), value)))
        })
        .collect::<Vec<_>>();

    let list = List::new(items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Fields")
                .style(popup_style()),
        )
        .style(popup_style())
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    if let Some(editor) = review_state.item_editor.as_mut() {
        frame.render_stateful_widget(list, rows[0], &mut editor.field_state);
    }

    let help = Paragraph::new(
        "Up/Down select  |  Enter edit or open category picker  |  x / Space toggle removed  |  Esc close",
    )
    .style(popup_style())
    .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[1]);
}

fn render_category_picker_modal(frame: &mut ratatui::Frame<'_>, review_state: &mut ReviewState) {
    let Some(item_index) = review_state
        .category_picker
        .as_ref()
        .map(|picker| picker.item_index)
    else {
        return;
    };
    let Some(item) = review_state.items.get(item_index) else {
        return;
    };

    let popup = centered_rect(78, 14, frame.area());
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title(format!("Select Category ({})", item.id))
            .style(popup_style()),
        popup,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(8), Constraint::Length(2)])
        .split(popup);

    let options = review_state
        .category_options
        .iter()
        .map(|option| ListItem::new(Line::from(option.display_label())))
        .collect::<Vec<_>>();
    let list = List::new(options)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Categories")
                .style(popup_style()),
        )
        .style(popup_style())
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    if let Some(picker) = review_state.category_picker.as_mut() {
        frame.render_stateful_widget(list, rows[0], &mut picker.category_state);
    }

    let help =
        Paragraph::new("Up/Down select  |  PageUp/PageDown jump  |  Enter apply  |  Esc cancel")
            .style(popup_style())
            .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[1]);
}

fn render_text_input_modal(frame: &mut ratatui::Frame<'_>, text_input: &TextInputState) {
    let popup = centered_rect(64, 6, frame.area());
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title(text_input.label.as_str())
            .style(popup_style()),
        popup,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Length(1)])
        .split(popup);
    let chars = text_input.value.chars().collect::<Vec<_>>();
    let cursor = text_input.cursor.min(chars.len());
    let before = chars.iter().take(cursor).collect::<String>();
    let current = chars
        .get(cursor)
        .map(char::to_string)
        .unwrap_or_else(|| " ".to_string());
    let after = if cursor < chars.len() {
        chars.iter().skip(cursor + 1).collect::<String>()
    } else {
        String::new()
    };
    let input = Paragraph::new(Line::from(vec![
        Span::raw(before),
        Span::styled(current, Style::default().bg(Color::Yellow).fg(Color::Black)),
        Span::raw(after),
    ]))
    .block(Block::default().borders(Borders::ALL).title("Edit"))
    .style(popup_style().add_modifier(Modifier::BOLD))
    .wrap(Wrap { trim: false });
    frame.render_widget(input, rows[0]);

    let help = Paragraph::new(
        "Enter apply  |  Esc cancel  |  Left/Right move  |  Home/End  |  Backspace/Delete",
    )
    .style(popup_style())
    .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[1]);
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
            .style(popup_style()),
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
        .style(popup_style().add_modifier(Modifier::BOLD))
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
            .style(popup_style()),
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
    let mut terminal = Terminal::new(backend)?;
    terminal.clear()?;
    terminal.hide_cursor()?;
    Ok(terminal)
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

fn run_app(
    terminal: &mut Terminal<CrosstermBackend<Stdout>>,
    app: &mut App,
    quit_after_launch: bool,
) -> AppResult<()> {
    loop {
        terminal.draw(|frame| render_app(frame, app))?;
        if quit_after_launch {
            return Ok(());
        }
        if app.should_quit {
            return Ok(());
        }

        if !event::poll(Duration::from_millis(200))? {
            app.refresh_runtime_pages(false)?;
            continue;
        }

        let Event::Key(key) = event::read()? else {
            continue;
        };

        if app.review_state.is_some() {
            let mut review_cancelled = false;
            let mut approve_requested = false;
            let mut status_message: Option<String> = None;
            if let Some(review_state) = app.review_state.as_mut() {
                if let Some(text_input) = review_state.text_input.as_mut() {
                    match key.code {
                        KeyCode::Esc => {
                            review_state.text_input = None;
                            status_message = Some("Cancelled field edit".to_string());
                        }
                        KeyCode::Enter => {
                            review_state.commit_text_input();
                            status_message = Some("Applied edit to review state".to_string());
                        }
                        KeyCode::Left => {
                            text_input.move_left();
                        }
                        KeyCode::Right => {
                            text_input.move_right();
                        }
                        KeyCode::Home => {
                            text_input.move_home();
                        }
                        KeyCode::End => {
                            text_input.move_end();
                        }
                        KeyCode::Backspace => {
                            text_input.backspace();
                        }
                        KeyCode::Delete => {
                            text_input.delete();
                        }
                        KeyCode::Char(ch) => {
                            text_input.insert_char(ch);
                        }
                        _ => {}
                    }
                } else if review_state.category_picker.is_some() {
                    match key.code {
                        KeyCode::Esc => {
                            review_state.category_picker = None;
                            status_message = Some("Cancelled category selection".to_string());
                        }
                        KeyCode::Down | KeyCode::Char('j') => {
                            let len = review_state.category_options.len();
                            if let Some(picker) = review_state.category_picker.as_mut() {
                                picker.move_selection(1, len);
                            }
                        }
                        KeyCode::Up | KeyCode::Char('k') => {
                            let len = review_state.category_options.len();
                            if let Some(picker) = review_state.category_picker.as_mut() {
                                picker.move_selection(-1, len);
                            }
                        }
                        KeyCode::PageDown => {
                            let len = review_state.category_options.len();
                            if let Some(picker) = review_state.category_picker.as_mut() {
                                picker.move_selection(CategoryPickerState::PAGE_STEP, len);
                            }
                        }
                        KeyCode::PageUp => {
                            let len = review_state.category_options.len();
                            if let Some(picker) = review_state.category_picker.as_mut() {
                                picker.move_selection(-CategoryPickerState::PAGE_STEP, len);
                            }
                        }
                        KeyCode::Enter => {
                            status_message = review_state.apply_selected_category();
                        }
                        _ => {}
                    }
                } else if review_state.item_editor.is_some() {
                    match key.code {
                        KeyCode::Esc => {
                            review_state.item_editor = None;
                            status_message = Some("Closed item editor".to_string());
                        }
                        KeyCode::Down | KeyCode::Char('j') => {
                            if let Some(editor) = review_state.item_editor.as_mut() {
                                editor.move_selection(1);
                            }
                        }
                        KeyCode::Up | KeyCode::Char('k') => {
                            if let Some(editor) = review_state.item_editor.as_mut() {
                                editor.move_selection(-1);
                            }
                        }
                        KeyCode::Enter => {
                            status_message = review_state.activate_item_editor_selection();
                        }
                        KeyCode::Char('x') | KeyCode::Char(' ') => {
                            status_message = review_state.toggle_item_editor_removed();
                        }
                        KeyCode::Char('c') => {
                            review_state.open_category_picker_from_item_editor();
                            status_message = Some("Selecting item category".to_string());
                        }
                        _ => {}
                    }
                } else {
                    match (key.code, key.modifiers) {
                        (KeyCode::Esc, _) => review_cancelled = true,
                        (KeyCode::Char('a'), _) => approve_requested = true,
                        (KeyCode::Char('p'), _) => {
                            review_state.preview_tab = review_state.preview_tab.next();
                            review_state.preview_scroll_y = 0;
                        }
                        (KeyCode::Tab, _)
                        | (KeyCode::Char('l'), KeyModifiers::NONE)
                        | (KeyCode::Right, _) => {
                            review_state.pane = review_state.pane.next();
                        }
                        (KeyCode::BackTab, _)
                        | (KeyCode::Char('h'), KeyModifiers::NONE)
                        | (KeyCode::Left, _) => {
                            review_state.pane = review_state.pane.previous();
                        }
                        (KeyCode::Down, _) | (KeyCode::Char('j'), KeyModifiers::NONE) => {
                            match review_state.pane {
                                ReviewPane::Items => {
                                    let len = review_state.items.len();
                                    if len > 0 {
                                        let current =
                                            review_state.item_state.selected().unwrap_or(0)
                                                as isize;
                                        let next =
                                            (current + 1).clamp(0, (len - 1) as isize) as usize;
                                        review_state.item_state.select(Some(next));
                                    }
                                }
                                ReviewPane::Fields => {
                                    let len = review_state.fields.len();
                                    if len > 0 {
                                        let current =
                                            review_state.field_state.selected().unwrap_or(0)
                                                as isize;
                                        let next =
                                            (current + 1).clamp(0, (len - 1) as isize) as usize;
                                        review_state.field_state.select(Some(next));
                                    }
                                }
                                ReviewPane::Preview => {
                                    review_state.preview_scroll_y =
                                        review_state.preview_scroll_y.saturating_add(1);
                                }
                            }
                        }
                        (KeyCode::Up, _) | (KeyCode::Char('k'), KeyModifiers::NONE) => {
                            match review_state.pane {
                                ReviewPane::Items => {
                                    let len = review_state.items.len();
                                    if len > 0 {
                                        let current =
                                            review_state.item_state.selected().unwrap_or(0)
                                                as isize;
                                        let next =
                                            (current - 1).clamp(0, (len - 1) as isize) as usize;
                                        review_state.item_state.select(Some(next));
                                    }
                                }
                                ReviewPane::Fields => {
                                    let len = review_state.fields.len();
                                    if len > 0 {
                                        let current =
                                            review_state.field_state.selected().unwrap_or(0)
                                                as isize;
                                        let next =
                                            (current - 1).clamp(0, (len - 1) as isize) as usize;
                                        review_state.field_state.select(Some(next));
                                    }
                                }
                                ReviewPane::Preview => {
                                    review_state.preview_scroll_y =
                                        review_state.preview_scroll_y.saturating_sub(1);
                                }
                            }
                        }
                        (KeyCode::Enter, _) => match review_state.pane {
                            ReviewPane::Items => review_state.open_selected_item_editor(),
                            ReviewPane::Fields => review_state.start_selected_field_edit(),
                            ReviewPane::Preview => {}
                        },
                        (KeyCode::Char('i'), _) => {
                            if review_state.pane == ReviewPane::Items {
                                let item_id = review_state.add_item();
                                status_message = Some(format!(
                                    "Added {item_id}; blank new items are ignored on submit"
                                ));
                            }
                        }
                        (KeyCode::Char('c'), _) => {
                            if review_state.pane == ReviewPane::Items {
                                review_state.open_selected_category_picker();
                                status_message = Some("Selecting item category".to_string());
                            }
                        }
                        (KeyCode::Char('v'), _) => {
                            if review_state.pane == ReviewPane::Items {
                                review_state.item_editor_select_field(ItemEditorField::Price);
                                status_message = review_state.activate_item_editor_selection();
                            }
                        }
                        (KeyCode::Char('n'), _) => {
                            if review_state.pane == ReviewPane::Items {
                                review_state.item_editor_select_field(ItemEditorField::Notes);
                                status_message = review_state.activate_item_editor_selection();
                            }
                        }
                        (KeyCode::Char('x'), _) => {
                            if review_state.pane == ReviewPane::Items {
                                if let Some(index) = review_state.selected_item_index() {
                                    status_message = review_state.toggle_item_removed(index);
                                }
                            }
                        }
                        _ => {}
                    }
                }
            }
            if review_cancelled {
                app.review_state = None;
                app.set_status("Review cancelled");
            } else if approve_requested {
                if let Err(error) = app.apply_review_changes() {
                    app.set_error(error.to_string());
                }
            } else if let Some(message) = status_message {
                app.set_status(message);
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
            (KeyCode::Char('1'), _) => {
                app.switch_page(Page::Receipts);
                if let Err(error) = app.refresh_runtime_pages(true) {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('2'), _) => {
                app.switch_page(Page::Serve);
                if let Err(error) = app.refresh_runtime_pages(true) {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('3'), _) => {
                app.switch_page(Page::Fava);
                if let Err(error) = app.refresh_runtime_pages(true) {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('4'), _) => {
                app.switch_page(Page::Ocr);
                if let Err(error) = app.refresh_runtime_pages(true) {
                    app.set_error(error.to_string());
                }
            }
            (KeyCode::Char('5'), _) => {
                app.switch_page(Page::Imports);
                app.set_status("Switched to Imports. Press `r` to load statement routes.");
            }
            (KeyCode::Char('r'), _) => {
                if let Err(error) = app.refresh_current_page() {
                    app.set_error(error.to_string());
                }
            }
            _ => match app.active_page {
                Page::Receipts => match (key.code, key.modifiers) {
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
                    (KeyCode::Char('a'), _) => {
                        if let Err(error) = app.approve_selected_scanned() {
                            app.set_error(error.to_string());
                        }
                    }
                    (KeyCode::Char('e'), _) => {
                        app.begin_edit_selected();
                    }
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
                    _ => {}
                },
                Page::Serve => match (key.code, key.modifiers) {
                    (KeyCode::Char('s'), KeyModifiers::NONE) => {
                        if let Err(error) = app.start_serve_process() {
                            app.set_error(error.to_string());
                        }
                    }
                    (KeyCode::Char('x'), KeyModifiers::NONE) => {
                        if let Err(error) = app.stop_serve_process() {
                            app.set_error(error.to_string());
                        }
                    }
                    (KeyCode::Char('R'), KeyModifiers::SHIFT) => {
                        if let Err(error) = app.restart_serve_process() {
                            app.set_error(error.to_string());
                        }
                    }
                    _ => {}
                },
                Page::Fava => match (key.code, key.modifiers) {
                    (KeyCode::Char('s'), KeyModifiers::NONE) => {
                        if let Err(error) = app.start_fava_process() {
                            app.set_error(error.to_string());
                        }
                    }
                    (KeyCode::Char('x'), KeyModifiers::NONE) => {
                        if let Err(error) = app.stop_fava_process() {
                            app.set_error(error.to_string());
                        }
                    }
                    (KeyCode::Char('R'), KeyModifiers::SHIFT) => {
                        if let Err(error) = app.restart_fava_process() {
                            app.set_error(error.to_string());
                        }
                    }
                    _ => {}
                },
                Page::Ocr => {
                    match (key.code, key.modifiers) {
                        (KeyCode::Char('s'), KeyModifiers::NONE) => {
                            if let Err(error) = app.start_ocr_container() {
                                app.set_error(error.to_string());
                            }
                        }
                        (KeyCode::Char('x'), KeyModifiers::NONE) => {
                            if let Err(error) = app.stop_ocr_container() {
                                app.set_error(error.to_string());
                            }
                        }
                        (KeyCode::Char('R'), KeyModifiers::SHIFT) => {
                            if let Err(error) = app.restart_ocr_container() {
                                app.set_error(error.to_string());
                            }
                        }
                        _ => {}
                    }
                    if let Err(error) = app.refresh_runtime_pages(false) {
                        app.set_error(error.to_string());
                    }
                }
                Page::Imports => match (key.code, key.modifiers) {
                    (KeyCode::Tab, _)
                    | (KeyCode::Char('l'), KeyModifiers::NONE)
                    | (KeyCode::Right, _) => {
                        app.imports_state.focus = ImportPaneFocus::Accounts;
                    }
                    (KeyCode::BackTab, _)
                    | (KeyCode::Char('h'), KeyModifiers::NONE)
                    | (KeyCode::Left, _) => {
                        app.imports_state.focus = ImportPaneFocus::Routes;
                    }
                    (KeyCode::Down, _) | (KeyCode::Char('j'), KeyModifiers::NONE) => {
                        if app.imports_state.focus == ImportPaneFocus::Routes {
                            if let Err(error) = app.move_import_route_selection(1) {
                                app.set_error(error.to_string());
                            }
                        } else {
                            app.imports_state.move_account_selection(1);
                        }
                    }
                    (KeyCode::Up, _) | (KeyCode::Char('k'), KeyModifiers::NONE) => {
                        if app.imports_state.focus == ImportPaneFocus::Routes {
                            if let Err(error) = app.move_import_route_selection(-1) {
                                app.set_error(error.to_string());
                            }
                        } else {
                            app.imports_state.move_account_selection(-1);
                        }
                    }
                    (KeyCode::Enter, _) => {
                        if let Err(error) = app.resolve_selected_import_accounts() {
                            app.set_error(error.to_string());
                        } else {
                            app.set_status("Reloaded account choices for selected statement");
                        }
                    }
                    (KeyCode::Char('u'), KeyModifiers::NONE) => {
                        app.imports_state.allow_uncommitted = !app.imports_state.allow_uncommitted;
                        app.set_status(format!(
                            "Allow import with uncommitted changes: {}",
                            if app.imports_state.allow_uncommitted {
                                "enabled"
                            } else {
                                "disabled"
                            }
                        ));
                    }
                    (KeyCode::Char('a'), _) => {
                        if let Err(error) = app.apply_selected_import() {
                            app.set_error(error.to_string());
                        }
                    }
                    (KeyCode::Char('v'), KeyModifiers::NONE) => {
                        match app.selected_import_source_path() {
                            Ok(Some(path)) => {
                                suspend_terminal(terminal)?;
                                let view_result = process_util::view_csv_file(&path);
                                resume_terminal(terminal)?;
                                match view_result {
                                    Ok(status) if status.success() => {
                                        app.set_status(format!("Viewed {}", path));
                                    }
                                    Ok(status) => {
                                        app.set_error(format!(
                                            "CSV viewer exited with code {} for {}",
                                            status
                                                .code()
                                                .map(|code| code.to_string())
                                                .unwrap_or_else(|| "signal".to_string()),
                                            path
                                        ));
                                    }
                                    Err(error) => {
                                        app.set_error(format!("Failed to view {}: {}", path, error))
                                    }
                                }
                            }
                            Ok(None) => {}
                            Err(error) => app.set_error(error.to_string()),
                        }
                    }
                    (KeyCode::Char('x'), KeyModifiers::NONE) => {
                        if let Err(error) = app.trash_selected_import_csv() {
                            app.set_error(error.to_string());
                        }
                    }
                    _ => {}
                },
            },
        }
    }
}

pub fn run(quit_after_launch: bool) -> AppResult<()> {
    let mut terminal = setup_terminal()?;
    let result = (|| -> AppResult<()> {
        let mut app = App::new();
        app.refresh()?;
        app.refresh_runtime_pages(true)?;
        let run_result = run_app(&mut terminal, &mut app, quit_after_launch);
        let shutdown_result = app.shutdown();
        run_result.and(shutdown_result)
    })();
    restore_terminal(terminal)?;
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_review_detail() -> ShowReceiptResponse {
        ShowReceiptResponse {
            path: "/tmp/review_stage_1.receipt.json".to_string(),
            summary: ReceiptSummary {
                path: "/tmp/review_stage_1.receipt.json".to_string(),
                receipt_dir: "2026-03-07_costco_466_68_ad51".to_string(),
                stage_file: "review_stage_1.receipt.json".to_string(),
                merchant: Some("COSTCO".to_string()),
                date: Some("2026-03-07".to_string()),
                total: Some("466.68".to_string()),
            },
            document: serde_json::json!({
                "receipt": {
                    "merchant": "COSTCO",
                    "date": "2026-03-07",
                    "total": "466.68"
                },
                "items": [],
                "debug": {
                    "ocr_payload": {"detections": []}
                }
            }),
        }
    }

    #[test]
    fn render_detail_lines_scanned_shows_human_readable_summary() {
        let detail = ShowReceiptResponse {
            path: "/tmp/scanned.receipt.json".to_string(),
            summary: ReceiptSummary {
                path: "/tmp/scanned.receipt.json".to_string(),
                receipt_dir: "2026-03-07_costco_466_68_ad51".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("COSTCO".to_string()),
                date: Some("2026-03-07".to_string()),
                total: Some("466.68".to_string()),
            },
            document: serde_json::json!({
                "receipt": {
                    "merchant": "COSTCO",
                    "date": "2026-03-07",
                    "currency": "CAD",
                    "subtotal": "460.96",
                    "tax": "5.72",
                    "total": "466.68"
                },
                "items": [
                    {
                        "description": "810 LCBO CARD",
                        "price": "400.00",
                        "quantity": 1,
                        "classification": {"category": "alcohol"},
                        "warnings": []
                    }
                ],
                "debug": {
                    "ocr_payload": {"detections": []}
                }
            }),
        };

        let lines = render_detail_lines(Queue::Scanned, &detail);
        let rendered = lines.join("\n");

        assert!(rendered.contains("Parsed Receipt"));
        assert!(rendered.contains("Receipt"));
        assert!(rendered.contains("Items (1)"));
        assert!(rendered.contains("810 LCBO CARD  |  $400.00  |  alcohol"));
        assert!(!rendered.contains("Stage JSON"));
        assert!(!rendered.contains("\"debug\""));
    }

    #[test]
    fn render_detail_lines_approved_applies_review_overrides() {
        let detail = ShowReceiptResponse {
            path: "/tmp/review_stage_1.receipt.json".to_string(),
            summary: ReceiptSummary {
                path: "/tmp/review_stage_1.receipt.json".to_string(),
                receipt_dir: "2026-03-07_costco_466_68_ad51".to_string(),
                stage_file: "review_stage_1.receipt.json".to_string(),
                merchant: Some("COSTCO".to_string()),
                date: Some("2026-03-07".to_string()),
                total: Some("466.68".to_string()),
            },
            document: serde_json::json!({
                "receipt": {
                    "merchant": "COSTCO",
                    "date": "2026-03-07",
                    "total": "466.68"
                },
                "review": {
                    "notes": "manual review"
                },
                "items": [
                    {
                        "description": "810 LCBO CARD",
                        "price": "400.00",
                        "classification": {"category": "uncategorized"},
                        "review": {
                            "description": "LCBO",
                            "classification": {"category": "alcohol"},
                            "notes": "gift"
                        },
                        "warnings": []
                    },
                    {
                        "description": "REMOVE ME",
                        "price": "1.00",
                        "review": {"removed": true},
                        "warnings": []
                    }
                ],
                "debug": {
                    "ocr_payload": {"detections": []}
                }
            }),
        };

        let lines = render_detail_lines(Queue::Approved, &detail);
        let rendered = lines.join("\n");

        assert!(rendered.contains("Reviewed Receipt"));
        assert!(rendered.contains("Notes: manual review"));
        assert!(rendered.contains("LCBO  |  $400.00  |  alcohol"));
        assert!(rendered.contains("    Notes: gift"));
        assert!(!rendered.contains("REMOVE ME"));
        assert!(!rendered.contains("\"debug\""));
    }

    #[test]
    fn review_payload_includes_create_patch_for_added_items() {
        let detail = sample_review_detail();
        let mut review_state = ReviewState::from_detail(Queue::Approved, &detail, Vec::new());

        review_state.add_item();
        let item = review_state.items.get_mut(0).expect("new item");
        item.description = "BANANAS".to_string();
        item.price = "3.99".to_string();
        item.category = "Expenses:Food:Grocery:Fruit".to_string();
        item.notes = "manual add".to_string();

        assert_eq!(
            review_state.payload(),
            serde_json::json!({
                "review": {},
                "items": [
                    {
                        "id": "item-added-0001",
                        "create": true,
                        "review": {
                            "description": "BANANAS",
                            "price": "3.99",
                            "category": "Expenses:Food:Grocery:Fruit",
                            "notes": "manual add",
                        }
                    }
                ]
            })
        );
    }

    #[test]
    fn review_payload_ignores_blank_added_item_drafts() {
        let detail = sample_review_detail();
        let mut review_state = ReviewState::from_detail(Queue::Approved, &detail, Vec::new());

        review_state.add_item();

        assert_eq!(
            review_state.payload(),
            serde_json::json!({
                "review": {},
                "items": []
            })
        );
    }

    #[test]
    fn sync_selection_to_path_preserves_selected_receipt_across_insertions() {
        let mut app = App::new();
        app.scanned = vec![
            ReceiptSummary {
                path: "/tmp/older.receipt.json".to_string(),
                receipt_dir: "older".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("OLDER".to_string()),
                date: Some("2026-03-09".to_string()),
                total: Some("10.00".to_string()),
            },
            ReceiptSummary {
                path: "/tmp/current.receipt.json".to_string(),
                receipt_dir: "current".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("CURRENT".to_string()),
                date: Some("2026-03-10".to_string()),
                total: Some("20.00".to_string()),
            },
        ];
        app.scanned_state.select(Some(1));

        let selected_path = app.selected_path_for_queue(Queue::Scanned);

        app.scanned = vec![
            ReceiptSummary {
                path: "/tmp/newest.receipt.json".to_string(),
                receipt_dir: "newest".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("NEWEST".to_string()),
                date: Some("2026-03-11".to_string()),
                total: Some("30.00".to_string()),
            },
            ReceiptSummary {
                path: "/tmp/older.receipt.json".to_string(),
                receipt_dir: "older".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("OLDER".to_string()),
                date: Some("2026-03-09".to_string()),
                total: Some("10.00".to_string()),
            },
            ReceiptSummary {
                path: "/tmp/current.receipt.json".to_string(),
                receipt_dir: "current".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("CURRENT".to_string()),
                date: Some("2026-03-10".to_string()),
                total: Some("20.00".to_string()),
            },
        ];

        app.sync_selection_to_path(Queue::Scanned, selected_path.as_deref());

        assert_eq!(app.scanned_state.selected(), Some(2));
        assert_eq!(
            app.selected_path_for_queue(Queue::Scanned).as_deref(),
            Some("/tmp/current.receipt.json")
        );
    }

    #[test]
    fn import_page_state_preserves_selected_route_by_source_path() {
        let mut state = ImportPageState::new();
        state.set_routes(
            vec![
                ImportRouteOption {
                    csv_file: "statement.csv".to_string(),
                    source_path: "/tmp/statement.csv".to_string(),
                    import_type: "cc".to_string(),
                    importer_id: "bmo".to_string(),
                    rule_id: "cc-bmo-statement".to_string(),
                    stage: 1,
                },
                ImportRouteOption {
                    csv_file: "Preferred_Package_foo.csv".to_string(),
                    source_path: "/tmp/Preferred_Package_foo.csv".to_string(),
                    import_type: "chequing".to_string(),
                    importer_id: "scotia_chequing".to_string(),
                    rule_id: "chequing-scotia".to_string(),
                    stage: 1,
                },
            ],
            None,
        );
        state.route_state.select(Some(1));

        state.set_routes(
            vec![
                ImportRouteOption {
                    csv_file: "new.csv".to_string(),
                    source_path: "/tmp/new.csv".to_string(),
                    import_type: "cc".to_string(),
                    importer_id: "rogers".to_string(),
                    rule_id: "cc-rogers".to_string(),
                    stage: 1,
                },
                ImportRouteOption {
                    csv_file: "Preferred_Package_foo.csv".to_string(),
                    source_path: "/tmp/Preferred_Package_foo.csv".to_string(),
                    import_type: "chequing".to_string(),
                    importer_id: "scotia_chequing".to_string(),
                    rule_id: "chequing-scotia".to_string(),
                    stage: 1,
                },
            ],
            None,
        );

        assert_eq!(state.route_state.selected(), Some(1));
        assert_eq!(
            state
                .selected_route()
                .map(|route| route.source_path.as_str()),
            Some("/tmp/Preferred_Package_foo.csv")
        );
    }

    #[test]
    fn import_page_detail_lines_include_last_result_and_warnings() {
        let mut state = ImportPageState::new();
        state.planner_status = "ready".to_string();
        state.has_uncommitted_changes = true;
        state.allow_uncommitted = true;
        state.set_routes(
            vec![ImportRouteOption {
                csv_file: "statement.csv".to_string(),
                source_path: "/tmp/statement.csv".to_string(),
                import_type: "cc".to_string(),
                importer_id: "bmo".to_string(),
                rule_id: "cc-bmo-statement".to_string(),
                stage: 1,
            }],
            None,
        );
        state.apply_account_resolution(
            ResolveImportAccountsResponse {
                status: "ready".to_string(),
                _import_type: "cc".to_string(),
                _csv_file: "statement.csv".to_string(),
                _importer_id: "bmo".to_string(),
                account_label: Some("BMO credit card".to_string()),
                account_options: Some(vec!["Liabilities:CreditCard:Primary:BMO:CardA".to_string()]),
                as_of: Some("2026-03-04".to_string()),
                error: None,
            },
            None,
        );
        state.last_result = Some(ApplyImportResponse {
            status: "ok".to_string(),
            import_type: "cc".to_string(),
            result_file_path: Some("/tmp/carda_0304_0304.beancount".to_string()),
            result_file_name: Some("carda_0304_0304.beancount".to_string()),
            account: Some("Liabilities:CreditCard:Primary:BMO:CardA".to_string()),
            start_date: Some("0304".to_string()),
            end_date: Some("0304".to_string()),
            error: None,
            warnings: vec!["Ledger validation found errors after import.".to_string()],
            validation_errors: vec!["Validation error 1".to_string()],
            summary: Some("Import complete: /tmp/carda_0304_0304.beancount".to_string()),
        });

        let rendered = state.detail_lines().join("\n");

        assert!(rendered.contains("Planner Status: ready"));
        assert!(rendered.contains("Git Working Tree: uncommitted changes detected"));
        assert!(rendered.contains("Selected Route"));
        assert!(rendered.contains("Selected Account: Liabilities:CreditCard:Primary:BMO:CardA"));
        assert!(rendered.contains("Last Import Result"));
        assert!(rendered.contains("Warnings:"));
        assert!(rendered.contains("Validation Errors:"));
    }
}
