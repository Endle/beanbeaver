use std::env;
use std::error::Error;
use std::io::{self, Stdout};
use std::process::Command;
use std::time::Duration;

use crossterm::event::{self, Event, KeyCode, KeyEventKind};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Text};
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Tabs, Wrap};
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

struct App {
    active_queue: Queue,
    scanned: Vec<ReceiptSummary>,
    approved: Vec<ReceiptSummary>,
    scanned_state: ListState,
    approved_state: ListState,
    detail_lines: Vec<String>,
    detail_path: Option<String>,
    status: String,
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
            scanned: Vec::new(),
            approved: Vec::new(),
            scanned_state,
            approved_state,
            detail_lines: vec!["Loading receipts...".to_string()],
            detail_path: None,
            status: "q quit | Tab switch queues | j/k move | r reload | a approve scanned"
                .to_string(),
            should_quit: false,
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
    }

    fn refresh(&mut self) -> AppResult<()> {
        self.scanned = backend_list_receipts(Queue::Scanned)?;
        self.approved = backend_list_receipts(Queue::Approved)?;
        self.sync_selection(Queue::Scanned);
        self.sync_selection(Queue::Approved);
        self.load_detail()?;
        self.status = format!(
            "Loaded {} scanned / {} approved receipt(s)",
            self.scanned.len(),
            self.approved.len()
        );
        Ok(())
    }

    fn load_detail(&mut self) -> AppResult<()> {
        let Some(path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.detail_lines = vec!["No receipt selected.".to_string()];
            self.detail_path = None;
            return Ok(());
        };
        let detail = backend_show_receipt(&path)?;
        self.detail_path = Some(detail.path.clone());
        self.detail_lines = render_detail_lines(&detail);
        Ok(())
    }

    fn approve_selected_scanned(&mut self) -> AppResult<()> {
        if self.active_queue != Queue::Scanned {
            self.status = "Approve is only available in the Scanned queue".to_string();
            return Ok(());
        }
        let Some(path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.status = "No scanned receipt selected".to_string();
            return Ok(());
        };
        let result = backend_approve_scanned(&path)?;
        self.refresh()?;
        self.status = format!(
            "Approved {} -> {}",
            result.source_path, result.approved_path
        );
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
    vec![
        "python".to_string(),
        "-m".to_string(),
        "beanbeaver.cli.main".to_string(),
    ]
}

fn run_backend(args: &[&str]) -> AppResult<String> {
    let backend = backend_command();
    let (program, program_args) = backend
        .split_first()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Empty backend command"))?;
    let output = Command::new(program)
        .args(program_args)
        .args(args)
        .output()?;

    if output.status.success() {
        Ok(String::from_utf8(output.stdout)?)
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        Err(format!(
            "backend command failed: {} {}\nstdout:\n{}\nstderr:\n{}",
            program,
            args.join(" "),
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
            Constraint::Length(2),
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
        .block(Block::default().borders(Borders::ALL).title(list_title))
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    match app.active_queue {
        Queue::Scanned => frame.render_stateful_widget(list, body[0], &mut app.scanned_state),
        Queue::Approved => frame.render_stateful_widget(list, body[0], &mut app.approved_state),
    }

    let detail_title = match &app.detail_path {
        Some(path) => format!("Details: {path}"),
        None => "Details".to_string(),
    };
    let detail = Paragraph::new(Text::from(
        app.detail_lines
            .iter()
            .cloned()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(Block::default().borders(Borders::ALL).title(detail_title))
    .wrap(Wrap { trim: false });
    frame.render_widget(detail, body[1]);

    let footer = Paragraph::new(app.status.clone())
        .block(Block::default().borders(Borders::ALL).title("Status"))
        .wrap(Wrap { trim: true });
    frame.render_widget(footer, chunks[2]);
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

        match key.code {
            KeyCode::Char('q') => app.should_quit = true,
            KeyCode::Tab => {
                app.switch_queue();
                if let Err(error) = app.load_detail() {
                    app.status = error.to_string();
                }
            }
            KeyCode::Down | KeyCode::Char('j') => {
                app.move_selection(1);
                if let Err(error) = app.load_detail() {
                    app.status = error.to_string();
                }
            }
            KeyCode::Up | KeyCode::Char('k') => {
                app.move_selection(-1);
                if let Err(error) = app.load_detail() {
                    app.status = error.to_string();
                }
            }
            KeyCode::Char('r') => {
                if let Err(error) = app.refresh() {
                    app.status = error.to_string();
                }
            }
            KeyCode::Char('a') => {
                if let Err(error) = app.approve_selected_scanned() {
                    app.status = error.to_string();
                }
            }
            KeyCode::Char('1') | KeyCode::Char('2') => {
                let next = if key.code == KeyCode::Char('1') { 0 } else { 1 };
                app.active_queue = Queue::from_tab(next);
                if let Err(error) = app.load_detail() {
                    app.status = error.to_string();
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
