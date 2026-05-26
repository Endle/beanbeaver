use super::*;

use std::io::{self, Stdout, Write};
use std::time::Duration;

use crossterm::event::{self, Event, KeyCode, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;

pub(crate) fn setup_terminal() -> AppResult<Terminal<CrosstermBackend<Stdout>>> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    terminal.clear()?;
    terminal.hide_cursor()?;
    Ok(terminal)
}

pub(crate) fn restore_terminal(mut terminal: Terminal<CrosstermBackend<Stdout>>) -> AppResult<()> {
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    Ok(())
}

pub(crate) fn suspend_terminal(terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> AppResult<()> {
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    Ok(())
}

pub(crate) fn resume_terminal(terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> AppResult<()> {
    enable_raw_mode()?;
    execute!(terminal.backend_mut(), EnterAlternateScreen)?;
    terminal.hide_cursor()?;
    terminal.clear()?;
    Ok(())
}

pub(crate) fn run_app(
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
                } else if review_state.tender_editor.is_some() {
                    match key.code {
                        KeyCode::Esc => {
                            review_state.tender_editor = None;
                            status_message = Some("Closed tender editor".to_string());
                        }
                        KeyCode::Down | KeyCode::Char('j') => {
                            if let Some(editor) = review_state.tender_editor.as_mut() {
                                editor.move_selection(1);
                            }
                        }
                        KeyCode::Up | KeyCode::Char('k') => {
                            if let Some(editor) = review_state.tender_editor.as_mut() {
                                editor.move_selection(-1);
                            }
                        }
                        KeyCode::Enter => {
                            status_message = review_state.activate_tender_editor_selection();
                        }
                        KeyCode::Char('x') | KeyCode::Char(' ') => {
                            status_message = review_state.toggle_tender_editor_removed();
                        }
                        KeyCode::Left => {
                            let tender_index = review_state
                                .tender_editor
                                .as_ref()
                                .map(|editor| editor.tender_index);
                            if let Some(index) = tender_index {
                                status_message = review_state.cycle_tender_kind(index, -1);
                            }
                        }
                        KeyCode::Right => {
                            let tender_index = review_state
                                .tender_editor
                                .as_ref()
                                .map(|editor| editor.tender_index);
                            if let Some(index) = tender_index {
                                status_message = review_state.cycle_tender_kind(index, 1);
                            }
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
                                ReviewPane::Tenders => {
                                    let len = review_state.tenders.len();
                                    if len > 0 {
                                        let current =
                                            review_state.tender_state.selected().unwrap_or(0)
                                                as isize;
                                        let next =
                                            (current + 1).clamp(0, (len - 1) as isize) as usize;
                                        review_state.tender_state.select(Some(next));
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
                                ReviewPane::Tenders => {
                                    let len = review_state.tenders.len();
                                    if len > 0 {
                                        let current =
                                            review_state.tender_state.selected().unwrap_or(0)
                                                as isize;
                                        let next =
                                            (current - 1).clamp(0, (len - 1) as isize) as usize;
                                        review_state.tender_state.select(Some(next));
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
                            ReviewPane::Tenders => review_state.open_selected_tender_editor(),
                            ReviewPane::Preview => {}
                        },
                        (KeyCode::Char('i'), _) => match review_state.pane {
                            ReviewPane::Items => {
                                let item_id = review_state.add_item();
                                status_message = Some(format!(
                                    "Added {item_id}; blank new items are ignored on submit"
                                ));
                            }
                            ReviewPane::Tenders => {
                                let tender_id = review_state.add_tender();
                                status_message = Some(format!(
                                    "Added {tender_id}; enter amount to keep on submit"
                                ));
                            }
                            _ => {}
                        },
                        (KeyCode::Char('T'), _) => {
                            let tender_id = review_state.add_tender();
                            review_state.pane = ReviewPane::Tenders;
                            status_message = Some(format!(
                                "Added {tender_id}; enter amount to keep on submit"
                            ));
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
                        (KeyCode::Char('x'), _) => match review_state.pane {
                            ReviewPane::Items => {
                                if let Some(index) = review_state.selected_item_index() {
                                    status_message = review_state.toggle_item_removed(index);
                                }
                            }
                            ReviewPane::Tenders => {
                                if let Some(index) = review_state.selected_tender_index() {
                                    status_message = review_state.toggle_tender_removed(index);
                                }
                            }
                            _ => {}
                        },
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

        if app.imports_state.cc_review.is_some() {
            handle_cc_review_key(app, key.code);
            continue;
        }

        if app.imports_state.decision_picker.is_some() {
            let candidates_len = app
                .imports_state
                .decision_picker
                .as_ref()
                .and_then(|picker| app.imports_state.decisions.get(picker.decision_index))
                .map(|decision| decision.candidates.len())
                .unwrap_or(0);
            match key.code {
                KeyCode::Esc => {
                    app.imports_state.close_decision_picker();
                    app.set_status("Decision picker cancelled");
                }
                KeyCode::Enter => {
                    let picked = app.imports_state.confirm_decision_picker();
                    let unresolved = app.imports_state.unresolved_decisions();
                    if let Some(account) = picked {
                        let short_account = account
                            .rsplit_once(':')
                            .map_or(account.as_str(), |(_, tail)| tail);
                        if unresolved == 0 {
                            app.set_status(format!(
                                "Picked {short_account}. All decisions resolved — press `a` to apply."
                            ));
                        } else {
                            app.set_status(format!(
                                "Picked {short_account}. {unresolved} decision(s) still unresolved — press Enter again to pick the next one, or `a` to apply once done."
                            ));
                        }
                    }
                }
                KeyCode::Down | KeyCode::Char('j') => {
                    if let Some(picker) = app.imports_state.decision_picker.as_mut() {
                        picker.move_selection(1, candidates_len);
                    }
                }
                KeyCode::Up | KeyCode::Char('k') => {
                    if let Some(picker) = app.imports_state.decision_picker.as_mut() {
                        picker.move_selection(-1, candidates_len);
                    }
                }
                KeyCode::Char('c') => {
                    app.imports_state.clear_decision_picker_selection();
                    app.set_status("Cleared selection for this decision");
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
                    (KeyCode::Char('o'), KeyModifiers::NONE) => {
                        app.open_selected_original_image();
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
                        app.imports_state.focus = match app.imports_state.focus {
                            ImportPaneFocus::Routes => ImportPaneFocus::Accounts,
                            ImportPaneFocus::Accounts => ImportPaneFocus::Decisions,
                            ImportPaneFocus::Decisions => ImportPaneFocus::Routes,
                        };
                    }
                    (KeyCode::BackTab, _)
                    | (KeyCode::Char('h'), KeyModifiers::NONE)
                    | (KeyCode::Left, _) => {
                        app.imports_state.focus = match app.imports_state.focus {
                            ImportPaneFocus::Routes => ImportPaneFocus::Decisions,
                            ImportPaneFocus::Accounts => ImportPaneFocus::Routes,
                            ImportPaneFocus::Decisions => ImportPaneFocus::Accounts,
                        };
                    }
                    (KeyCode::Down, _) | (KeyCode::Char('j'), KeyModifiers::NONE) => {
                        match app.imports_state.focus {
                            ImportPaneFocus::Routes => {
                                if let Err(error) = app.move_import_route_selection(1) {
                                    app.set_error(error.to_string());
                                }
                            }
                            ImportPaneFocus::Accounts => {
                                app.imports_state.move_account_selection(1);
                            }
                            ImportPaneFocus::Decisions => {
                                app.imports_state.move_decisions_selection(1);
                            }
                        }
                    }
                    (KeyCode::Up, _) | (KeyCode::Char('k'), KeyModifiers::NONE) => {
                        match app.imports_state.focus {
                            ImportPaneFocus::Routes => {
                                if let Err(error) = app.move_import_route_selection(-1) {
                                    app.set_error(error.to_string());
                                }
                            }
                            ImportPaneFocus::Accounts => {
                                app.imports_state.move_account_selection(-1);
                            }
                            ImportPaneFocus::Decisions => {
                                app.imports_state.move_decisions_selection(-1);
                            }
                        }
                    }
                    (KeyCode::Enter, _) => {
                        if app.imports_state.focus == ImportPaneFocus::Decisions {
                            app.imports_state.open_decision_picker();
                        } else if app.imports_state.jump_to_first_unresolved_and_open() {
                            app.set_status(
                                "Pick an account for this ambiguous transaction (Esc to cancel)",
                            );
                        } else {
                            match app.imports_state.focus {
                                ImportPaneFocus::Accounts => {
                                    app.imports_state.clear_decisions();
                                    if let Err(error) = app.refresh_import_decisions() {
                                        app.set_error(error.to_string());
                                    } else {
                                        app.set_status(
                                            "Reloaded import decisions for selected account",
                                        );
                                    }
                                }
                                ImportPaneFocus::Routes => {
                                    if let Err(error) = app.resolve_selected_import_accounts() {
                                        app.set_error(error.to_string());
                                    } else {
                                        app.set_status(
                                            "Reloaded account choices for selected statement",
                                        );
                                    }
                                }
                                ImportPaneFocus::Decisions => unreachable!(),
                            }
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
                    (KeyCode::Char('d'), KeyModifiers::NONE) => {
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

/// Drive the credit-card category review modal, including its per-transaction editor.
///
/// Precedence of sub-modes (innermost first): amount text input → category picker →
/// editor field navigation → the transaction list.
fn handle_cc_review_key(app: &mut App, code: KeyCode) {
    let Some((amount_open, picker_open, editor_open)) =
        app.imports_state.cc_review.as_ref().map(|review| {
            review.editor.as_ref().map_or((false, false, false), |editor| {
                (editor.amount_input.is_some(), editor.picker.is_some(), true)
            })
        })
    else {
        return;
    };

    if amount_open {
        match code {
            KeyCode::Esc => {
                if let Some(review) = app.imports_state.cc_review.as_mut() {
                    review.cancel_amount_input();
                }
                app.set_status("Cancelled amount edit");
            }
            KeyCode::Enter => {
                let value = app
                    .imports_state
                    .cc_review
                    .as_mut()
                    .and_then(|review| review.commit_amount_input());
                if let Some(amount) = value {
                    app.set_status(format!("Amount set to {amount}"));
                }
            }
            KeyCode::Backspace => {
                if let Some(review) = app.imports_state.cc_review.as_mut() {
                    review.amount_input_backspace();
                }
            }
            KeyCode::Char(ch) => {
                if let Some(review) = app.imports_state.cc_review.as_mut() {
                    review.amount_input_push(ch);
                }
            }
            _ => {}
        }
        return;
    }

    if picker_open {
        let candidates_len = app
            .imports_state
            .cc_review
            .as_ref()
            .map(|review| review.candidate_categories.len())
            .unwrap_or(0);
        let mut move_picker = |delta: isize| {
            if let Some(picker) = app
                .imports_state
                .cc_review
                .as_mut()
                .and_then(|review| review.editor.as_mut())
                .and_then(|editor| editor.picker.as_mut())
            {
                picker.move_selection(delta, candidates_len);
            }
        };
        match code {
            KeyCode::Esc => {
                if let Some(review) = app.imports_state.cc_review.as_mut() {
                    review.close_picker();
                }
                app.set_status("Cancelled category selection");
            }
            KeyCode::Enter => {
                let picked = app
                    .imports_state
                    .cc_review
                    .as_mut()
                    .and_then(|review| review.confirm_picker());
                if let Some(category) = picked {
                    app.set_status(format!("Set category to {category}"));
                }
            }
            KeyCode::Down | KeyCode::Char('j') => move_picker(1),
            KeyCode::Up | KeyCode::Char('k') => move_picker(-1),
            KeyCode::PageDown => move_picker(CategoryPickerState::PAGE_STEP),
            KeyCode::PageUp => move_picker(-CategoryPickerState::PAGE_STEP),
            _ => {}
        }
        return;
    }

    if editor_open {
        let field = app
            .imports_state
            .cc_review
            .as_ref()
            .and_then(|review| review.editor.as_ref())
            .map(|editor| editor.selected_field());
        match code {
            KeyCode::Esc => {
                if let Some(review) = app.imports_state.cc_review.as_mut() {
                    review.close_editor();
                }
                app.set_status("Closed transaction editor");
            }
            KeyCode::Down | KeyCode::Char('j') => {
                if let Some(editor) = app
                    .imports_state
                    .cc_review
                    .as_mut()
                    .and_then(|review| review.editor.as_mut())
                {
                    editor.move_field(1);
                }
            }
            KeyCode::Up | KeyCode::Char('k') => {
                if let Some(editor) = app
                    .imports_state
                    .cc_review
                    .as_mut()
                    .and_then(|review| review.editor.as_mut())
                {
                    editor.move_field(-1);
                }
            }
            KeyCode::Enter => match field {
                Some(CcEntryField::Category) => {
                    if let Some(review) = app.imports_state.cc_review.as_mut() {
                        review.open_picker();
                    }
                    app.set_status("Select a category (↑↓ · PgUp/PgDn · Enter confirm · Esc cancel)");
                }
                Some(CcEntryField::Amount) => {
                    if let Some(review) = app.imports_state.cc_review.as_mut() {
                        review.begin_amount_input();
                    }
                    app.set_status("Edit amount, then Enter to confirm (Esc to cancel)");
                }
                Some(CcEntryField::Delete) | None => {
                    toggle_cc_editor_deleted(app);
                }
            },
            KeyCode::Char('x') | KeyCode::Char(' ') => toggle_cc_editor_deleted(app),
            _ => {}
        }
        return;
    }

    match code {
        KeyCode::Esc => app.cancel_cc_category_review(),
        KeyCode::Char('a') => {
            if let Err(error) = app.finalize_cc_category_review() {
                app.set_error(error.to_string());
            }
        }
        KeyCode::Enter | KeyCode::Char('e') => {
            if let Some(review) = app.imports_state.cc_review.as_mut() {
                review.open_editor();
            }
            app.set_status("Editing transaction: ↑↓ field · Enter change · Esc back");
        }
        KeyCode::Char('x') => {
            if let Some(deleted) = app
                .imports_state
                .cc_review
                .as_mut()
                .and_then(|review| review.toggle_selected_deleted())
            {
                app.set_status(if deleted {
                    "Marked transaction for deletion"
                } else {
                    "Restored transaction"
                });
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if let Some(review) = app.imports_state.cc_review.as_mut() {
                review.move_selection(1);
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if let Some(review) = app.imports_state.cc_review.as_mut() {
                review.move_selection(-1);
            }
        }
        _ => {}
    }
}

fn toggle_cc_editor_deleted(app: &mut App) {
    if let Some(deleted) = app
        .imports_state
        .cc_review
        .as_mut()
        .and_then(|review| review.toggle_editor_deleted())
    {
        app.set_status(if deleted {
            "Marked transaction for deletion"
        } else {
            "Restored transaction"
        });
    }
}

pub(crate) fn autostart_disabled_from_env(raw: Option<&str>) -> bool {
    match raw {
        None => false,
        Some(value) => matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
    }
}

pub(crate) fn autostart_disabled() -> bool {
    autostart_disabled_from_env(std::env::var(AUTOSTART_DISABLE_ENV).ok().as_deref())
}

pub fn run(quit_after_launch: bool) -> AppResult<()> {
    let mut terminal = setup_terminal()?;
    let result = (|| -> AppResult<()> {
        let mut app = App::new();
        app.refresh()?;
        app.refresh_runtime_pages(true)?;
        if !quit_after_launch && !autostart_disabled() {
            app.autostart_services();
        }
        let run_result = run_app(&mut terminal, &mut app, quit_after_launch);
        let shutdown_result = app.shutdown();
        run_result.and(shutdown_result)
    })();
    restore_terminal(terminal)?;
    result
}
