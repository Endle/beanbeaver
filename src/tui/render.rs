use super::*;

use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, Paragraph, Tabs, Wrap};
use serde_json::Value;

pub(crate) fn render_detail_lines(queue: Queue, detail: &ShowReceiptResponse) -> Vec<String> {
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

pub(crate) fn render_receipt_summary_lines(document: &Value, title: &str) -> Vec<String> {
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

pub(crate) fn effective_detail_document(document: &Value) -> Value {
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

pub(crate) fn json_value_to_text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::Bool(flag)) => flag.to_string(),
        _ => String::new(),
    }
}

pub(crate) fn render_warning_values(value: Option<&Value>) -> Vec<String> {
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

pub(crate) fn char_to_byte_index(text: &str, char_idx: usize) -> usize {
    text.char_indices()
        .nth(char_idx)
        .map(|(index, _)| index)
        .unwrap_or(text.len())
}

pub(crate) fn popup_style() -> Style {
    Style::default()
        .bg(Color::Rgb(235, 235, 235))
        .fg(Color::Black)
}

pub(crate) fn effective_receipt_text(document: &Value, key: &str) -> String {
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

pub(crate) fn effective_item_text(item: &Value, key: &str) -> String {
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

pub(crate) fn effective_item_category_text(item: &Value) -> String {
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

pub(crate) fn effective_item_classification(item: &Value) -> Option<Value> {
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

pub(crate) fn effective_item_removed(item: &Value) -> bool {
    item.get("review")
        .and_then(Value::as_object)
        .and_then(|review| review.get("removed"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

pub(crate) fn render_app(frame: &mut ratatui::Frame<'_>, app: &mut App) {
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
    if app.imports_state.decision_picker.is_some() {
        render_decision_picker_modal(frame, &mut app.imports_state);
    }
    if app.imports_state.cc_review.is_some() {
        render_cc_category_review_modal(frame, &mut app.imports_state);
    }
}

pub(crate) fn render_receipts_page(
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

pub(crate) fn render_serve_page(
    frame: &mut ratatui::Frame<'_>,
    app: &App,
    area: ratatui::layout::Rect,
) {
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

pub(crate) fn render_fava_page(
    frame: &mut ratatui::Frame<'_>,
    app: &App,
    area: ratatui::layout::Rect,
) {
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

pub(crate) fn render_ocr_page(
    frame: &mut ratatui::Frame<'_>,
    app: &App,
    area: ratatui::layout::Rect,
) {
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(11), Constraint::Min(8)])
        .split(area);
    let runtime = app.ocr_state.runtime;

    let summary = Paragraph::new(Text::from(
        app.ocr_state
            .summary_lines
            .iter()
            .cloned()
            .map(Line::from)
            .collect::<Vec<_>>(),
    ))
    .block(Block::default().borders(Borders::ALL).title(format!(
        "{} Container (`s` start, `x` stop, `R` restart)",
        runtime.display_name()
    )))
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
    .block(Block::default().borders(Borders::ALL).title(format!(
        "`{} logs --tail 80 {OCR_CONTAINER_NAME}`",
        runtime.command()
    )))
    .wrap(Wrap { trim: false });
    frame.render_widget(logs, sections[1]);
}

pub(crate) fn render_imports_page(
    frame: &mut ratatui::Frame<'_>,
    app: &mut App,
    area: ratatui::layout::Rect,
) {
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(7), Constraint::Min(10)])
        .split(area);

    let total_decisions = app.imports_state.decisions.len();
    let unresolved_decisions = app.imports_state.unresolved_decisions();
    let decisions_summary = if total_decisions == 0 {
        "none".to_string()
    } else {
        format!("{total_decisions} total, {unresolved_decisions} unresolved")
    };
    let summary = Paragraph::new(format!(
        "Detected routes: {}\nWorking tree: {}\nAllow import with uncommitted changes: {}\nSelected route: {}\nDecisions: {}",
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
            .unwrap_or("<none>"),
        decisions_summary,
    ))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Statement Import (`a` apply, `u` toggle allow-uncommitted)"),
    )
    .wrap(Wrap { trim: true });
    frame.render_widget(summary, sections[0]);

    let body = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(8), Constraint::Min(6)])
        .split(sections[1]);
    let top_row = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(body[0]);

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
    frame.render_stateful_widget(routes, top_row[0], &mut app.imports_state.route_state);

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
    frame.render_stateful_widget(accounts, top_row[1], &mut app.imports_state.account_state);

    let unresolved = app.imports_state.unresolved_decisions();
    let total = app.imports_state.decisions.len();
    let decisions_title = if let Some(error) = app.imports_state.decisions_error.as_ref() {
        format!("Decisions (error: {})", truncate_for_title(error, 60))
    } else if total == 0 {
        "Decisions (none)".to_string()
    } else {
        format!("Decisions ({total} total · {unresolved} unresolved · Enter to pick)")
    };
    let decision_items: Vec<ListItem> = app
        .imports_state
        .decisions
        .iter()
        .map(|decision| ListItem::new(Line::from(decision.display_label())))
        .collect();
    let decisions_widget = List::new(decision_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(decisions_title)
                .border_style(if app.imports_state.focus == ImportPaneFocus::Decisions {
                    Style::default().fg(Color::Yellow)
                } else {
                    Style::default()
                }),
        )
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(
        decisions_widget,
        body[1],
        &mut app.imports_state.decisions_state,
    );
}

pub(crate) fn truncate_for_title(text: &str, max_len: usize) -> String {
    let cleaned = text.replace('\n', " ");
    if cleaned.chars().count() <= max_len {
        cleaned
    } else {
        let mut truncated: String = cleaned.chars().take(max_len.saturating_sub(1)).collect();
        truncated.push('…');
        truncated
    }
}

pub(crate) fn render_review_screen(
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
        .constraints([
            Constraint::Percentage(30),
            Constraint::Percentage(20),
            Constraint::Percentage(50),
        ])
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

    let tender_lines: Vec<ListItem> = review_state
        .tenders
        .iter()
        .enumerate()
        .map(|(index, tender)| {
            let amount = review_state.preview_tender_amount(index);
            let amount = if amount.trim().is_empty() {
                "0.00"
            } else {
                amount
            };
            let account = review_state.preview_tender_account(index);
            let account = if account.trim().is_empty() {
                "<PENDING>"
            } else {
                account
            };
            let removed = if tender.removed { " [removed]" } else { "" };
            let new_tender = if tender.is_new { " [new]" } else { "" };
            ListItem::new(Line::from(format!(
                "${} {}  -> {}{}{}",
                amount, tender.kind, account, new_tender, removed,
            )))
        })
        .collect();
    let tender_title = if review_state.tenders.is_empty() {
        "Tenders (none — press i to add)".to_string()
    } else {
        let (sum, total, status) = review_state.tender_balance();
        format!(
            "Tenders ({})  ${} / ${}  [{}]",
            review_state.tenders.len(),
            review_scaled_to_currency(sum),
            review_scaled_to_currency(total),
            status,
        )
    };
    let tenders_widget = List::new(tender_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(tender_title)
                .border_style(if review_state.pane == ReviewPane::Tenders {
                    Style::default().fg(Color::Yellow)
                } else {
                    Style::default()
                }),
        )
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(tenders_widget, right[1], &mut review_state.tender_state);

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
    frame.render_widget(preview, right[2]);

    let help = Paragraph::new(format!(
        "h/l pane  |  j/k move  |  Enter open editor  |  i add (item/tender)  |  T add tender  |  f fill remaining  |  v price  |  n notes  |  c category  |  x toggle removed  |  p preview tab  |  a {}  |  Esc cancel",
        review_state.submit_label()
    ))
    .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[2]);

    if review_state.item_editor.is_some() {
        render_item_editor_modal(frame, review_state);
    }
    if review_state.tender_editor.is_some() {
        render_tender_editor_modal(frame, review_state);
    }
    if review_state.category_picker.is_some() {
        render_category_picker_modal(frame, review_state);
    }
    if let Some(text_input) = &review_state.text_input {
        render_text_input_modal(frame, text_input);
    }
}

pub(crate) fn render_tender_editor_modal(
    frame: &mut ratatui::Frame<'_>,
    review_state: &mut ReviewState,
) {
    let Some(tender_index) = review_state
        .tender_editor
        .as_ref()
        .map(|editor| editor.tender_index)
    else {
        return;
    };
    let Some(tender) = review_state.tenders.get(tender_index) else {
        return;
    };

    let popup = centered_rect(72, 12, frame.area());
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title(if tender.is_new {
                format!("Edit New Tender ({})", tender.id)
            } else {
                format!("Edit Tender ({})", tender.id)
            })
            .style(popup_style()),
        popup,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(7), Constraint::Length(2)])
        .split(popup);

    let items = TenderEditorField::all()
        .into_iter()
        .map(|field| {
            let value = match field {
                TenderEditorField::Amount => {
                    if tender.amount.trim().is_empty() {
                        "<empty>".to_string()
                    } else {
                        tender.amount.clone()
                    }
                }
                TenderEditorField::Kind => tender.kind.clone(),
                TenderEditorField::Account => {
                    if tender.account.trim().is_empty() {
                        "<PENDING>".to_string()
                    } else {
                        tender.account.clone()
                    }
                }
                TenderEditorField::RawLabel => {
                    if tender.raw_label.trim().is_empty() {
                        "<empty>".to_string()
                    } else {
                        tender.raw_label.clone()
                    }
                }
                TenderEditorField::Removed => tender.removed.to_string(),
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
    if let Some(editor) = review_state.tender_editor.as_mut() {
        frame.render_stateful_widget(list, rows[0], &mut editor.field_state);
    }

    let help = Paragraph::new(
        "Up/Down select  |  Enter edit / cycle kind / toggle removed  |  Left/Right cycle kind  |  f fill remaining  |  x toggle removed  |  Esc close",
    )
    .style(popup_style())
    .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[1]);
}

pub(crate) fn render_item_editor_modal(
    frame: &mut ratatui::Frame<'_>,
    review_state: &mut ReviewState,
) {
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

pub(crate) fn render_category_picker_modal(
    frame: &mut ratatui::Frame<'_>,
    review_state: &mut ReviewState,
) {
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

pub(crate) fn render_text_input_modal(frame: &mut ratatui::Frame<'_>, text_input: &TextInputState) {
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

pub(crate) fn render_config_modal(
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
        "main.beancount: {}\nreceipts: {}\nconfig: {}",
        config.resolved_main_beancount_path, config.receipts_dir, config.config_path
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

pub(crate) fn render_match_modal(frame: &mut ratatui::Frame<'_>, match_state: &mut MatchState) {
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

pub(crate) fn render_decision_picker_modal(
    frame: &mut ratatui::Frame<'_>,
    imports_state: &mut ImportPageState,
) {
    let Some(picker) = imports_state.decision_picker.as_mut() else {
        return;
    };
    let Some(decision) = imports_state.decisions.get(picker.decision_index) else {
        return;
    };

    let popup = centered_rect(72, 16, frame.area());
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title("Pick account for ambiguous transaction")
            .style(popup_style()),
        popup,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Min(4),
            Constraint::Length(2),
        ])
        .split(popup);

    let kind_label = match decision.kind.as_str() {
        "cc_payment" => "CC payment",
        "bank_transfer" => "Bank transfer",
        other => other,
    };
    let summary = Paragraph::new(format!(
        "Kind: {kind_label}\nPattern: {}\nDate: {}    Amount: {}\nDescription: {}",
        decision.pattern, decision.txn_date, decision.txn_amount, decision.txn_description,
    ))
    .style(popup_style())
    .wrap(Wrap { trim: true });
    frame.render_widget(summary, rows[0]);

    let items: Vec<ListItem> = decision
        .candidates
        .iter()
        .map(|account| ListItem::new(Line::from(account.clone())).style(popup_style()))
        .collect();
    let list = List::new(items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!("Candidates ({})", decision.candidates.len()))
                .style(popup_style()),
        )
        .style(popup_style())
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(list, rows[1], &mut picker.list_state);

    let help = Paragraph::new("Enter pick  |  c clear selection  |  Esc cancel  |  j/k move")
        .style(popup_style())
        .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[2]);
}

pub(crate) fn render_cc_category_review_modal(
    frame: &mut ratatui::Frame<'_>,
    imports_state: &mut ImportPageState,
) {
    let Some(review) = imports_state.cc_review.as_mut() else {
        return;
    };

    let popup = centered_rect(88, 24, frame.area());
    frame.render_widget(Clear, popup);

    let card = review.card_account.as_deref().unwrap_or("(unknown card)");
    let changed = review.changed_count();
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title(format!(
                "Review categories — {card} ({} txns · {changed} changed)",
                review.entries.len()
            ))
            .style(popup_style()),
        popup,
    );

    let warning_height: u16 = if review.has_uncommitted_changes { 1 } else { 0 };
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(warning_height),
            Constraint::Min(4),
            Constraint::Length(2),
        ])
        .split(popup);

    if review.has_uncommitted_changes {
        let warning = Paragraph::new(
            "⚠ Ledger has uncommitted changes — commit before applying to keep history clean",
        )
        .style(popup_style().fg(Color::Yellow).add_modifier(Modifier::BOLD))
        .wrap(Wrap { trim: true });
        frame.render_widget(warning, rows[0]);
    }

    let items: Vec<ListItem> = review
        .entries
        .iter()
        .map(|entry| ListItem::new(Line::from(entry.display_label())).style(popup_style()))
        .collect();
    let list = List::new(items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Transactions (! uncategorized · * changed)")
                .style(popup_style()),
        )
        .style(popup_style())
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(list, rows[1], &mut review.entries_state);

    let help = Paragraph::new("Enter/e edit  |  x delete  |  a apply  |  Esc cancel  |  j/k move")
        .style(popup_style())
        .wrap(Wrap { trim: true });
    frame.render_widget(help, rows[2]);

    // Inner per-transaction editor overlay (fields → category picker / amount input).
    render_cc_entry_editor_overlay(frame, review);
}

fn render_cc_entry_editor_overlay(
    frame: &mut ratatui::Frame<'_>,
    review: &mut CcCategoryReview,
) {
    let Some(entry_index) = review.editor_entry_index() else {
        return;
    };
    let entry = match review.entries.get(entry_index) {
        Some(entry) => entry.clone(),
        None => return,
    };
    let candidate_categories = review.candidate_categories.clone();
    let Some(editor) = review.editor.as_mut() else {
        return;
    };

    let editor_rect = centered_rect(70, 14, frame.area());
    frame.render_widget(Clear, editor_rect);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title(format!("Edit · {} {}", entry.date, entry.payee))
            .style(popup_style()),
        editor_rect,
    );
    let editor_rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(3), Constraint::Length(2)])
        .split(editor_rect);

    let amount_display = match editor.amount_input.as_ref() {
        Some(buffer) => format!("{buffer}_"),
        None => entry.amount.clone(),
    };
    let delete_display = if entry.deleted { "[x] skip" } else { "[ ] keep" };
    let field_items: Vec<ListItem> = CcEntryField::all()
        .into_iter()
        .map(|field| {
            let value = match field {
                CcEntryField::Category => entry.chosen_category.clone(),
                CcEntryField::Amount => amount_display.clone(),
                CcEntryField::Delete => delete_display.to_string(),
            };
            ListItem::new(Line::from(format!("{:<10} {}", field.label(), value))).style(popup_style())
        })
        .collect();
    let field_list = List::new(field_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Fields")
                .style(popup_style()),
        )
        .style(popup_style())
        .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
        .highlight_symbol(">> ");
    frame.render_stateful_widget(field_list, editor_rows[0], &mut editor.field_state);

    let editor_help = if editor.amount_input.is_some() {
        "Type amount  |  Enter confirm  |  Esc cancel"
    } else {
        "Enter edit field  |  x toggle delete  |  Esc back"
    };
    frame.render_widget(
        Paragraph::new(editor_help)
            .style(popup_style())
            .wrap(Wrap { trim: true }),
        editor_rows[1],
    );

    // Innermost: category picker overlay.
    if let Some(picker) = editor.picker.as_mut() {
        let inner = centered_rect(60, 18, frame.area());
        frame.render_widget(Clear, inner);
        let inner_rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Min(4), Constraint::Length(2)])
            .split(inner);
        let candidate_items: Vec<ListItem> = candidate_categories
            .iter()
            .map(|category| ListItem::new(Line::from(category.clone())).style(popup_style()))
            .collect();
        let candidate_list = List::new(candidate_items)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(format!("Pick category ({})", candidate_categories.len()))
                    .style(popup_style()),
            )
            .style(popup_style())
            .highlight_style(Style::default().bg(Color::Blue).fg(Color::White))
            .highlight_symbol(">> ");
        frame.render_stateful_widget(candidate_list, inner_rows[0], &mut picker.category_state);
        frame.render_widget(
            Paragraph::new("Enter confirm  |  Esc cancel  |  j/k · PgUp/PgDn move")
                .style(popup_style())
                .wrap(Wrap { trim: true }),
            inner_rows[1],
        );
    }
}

pub(crate) fn centered_rect(
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
