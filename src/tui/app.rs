use super::*;

use std::path::Path;
use std::sync::mpsc::{self, TryRecvError};
use std::thread;
use std::time::Instant;

use ratatui::widgets::ListState;

pub(crate) struct App {
    pub(crate) active_page: Page,
    pub(crate) active_queue: Queue,
    pub(crate) focus: PaneFocus,
    pub(crate) right_pane: RightPane,
    pub(crate) scanned: Vec<ReceiptSummary>,
    pub(crate) approved: Vec<ReceiptSummary>,
    pub(crate) scanned_state: ListState,
    pub(crate) approved_state: ListState,
    pub(crate) detail_lines: Vec<String>,
    pub(crate) status_log_lines: Vec<String>,
    pub(crate) detail_path: Option<String>,
    pub(crate) detail_scroll_y: u16,
    pub(crate) detail_scroll_x: u16,
    pub(crate) status: String,
    pub(crate) review_state: Option<ReviewState>,
    pub(crate) config: ConfigResponse,
    pub(crate) config_state: Option<ConfigState>,
    pub(crate) match_state: Option<MatchState>,
    pub(crate) serve_state: ServePageState,
    pub(crate) fava_state: FavaPageState,
    pub(crate) ocr_state: OcrPageState,
    pub(crate) pending_ocr_action: Option<PendingOcrAction>,
    pub(crate) imports_state: ImportPageState,
    pub(crate) last_receipts_refresh: Option<Instant>,
    pub(crate) last_serve_refresh: Option<Instant>,
    pub(crate) last_fava_refresh: Option<Instant>,
    pub(crate) last_ocr_refresh: Option<Instant>,
    pub(crate) should_quit: bool,
}

impl App {
    pub(crate) fn new() -> Self {
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
                receipts_dir: String::new(),
                _scanned_dir: String::new(),
                _approved_dir: String::new(),
            },
            config_state: None,
            match_state: None,
            serve_state: ServePageState::new(),
            fava_state: FavaPageState::new(),
            ocr_state: OcrPageState::new(),
            pending_ocr_action: None,
            imports_state: ImportPageState::new(),
            last_receipts_refresh: None,
            last_serve_refresh: None,
            last_fava_refresh: None,
            last_ocr_refresh: None,
            should_quit: false,
        }
        .with_initial_status()
    }

    pub(crate) fn with_initial_status(mut self) -> Self {
        self.push_status_log(self.status.clone());
        self
    }

    pub(crate) fn page_help(page: Page) -> &'static str {
        match page {
            Page::Receipts => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | Tab switch queues | h/l pane focus | s toggle details/status | j/k move or scroll | e edit | o open image | m TUI match | M CLI match | arrows pan | r reload | a approve | c config | q quit"
            }
            Page::Serve => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | s start `bb serve` | x stop `bb serve` | R restart | r refresh health | q quit"
            }
            Page::Fava => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | s start Fava | x stop Fava | R restart | r refresh health | q quit"
            }
            Page::Ocr => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | s start/create container | x stop container | R restart container | r refresh container status/logs | q quit"
            }
            Page::Imports => {
                "1 receipts | 2 serve | 3 fava | 4 OCR | 5 imports | r load routes | h/l or Tab cycle Routes/Accounts/Decisions | j/k move | Enter opens picker (Routes: reload accounts) | v view csv | x trash csv | a apply import | u toggle allow-uncommitted | q quit"
            }
        }
    }

    pub(crate) fn receipts(&self, queue: Queue) -> &[ReceiptSummary] {
        match queue {
            Queue::Scanned => &self.scanned,
            Queue::Approved => &self.approved,
        }
    }

    pub(crate) fn list_state_mut(&mut self, queue: Queue) -> &mut ListState {
        match queue {
            Queue::Scanned => &mut self.scanned_state,
            Queue::Approved => &mut self.approved_state,
        }
    }

    pub(crate) fn selected_index(&self, queue: Queue) -> Option<usize> {
        match queue {
            Queue::Scanned => self.scanned_state.selected(),
            Queue::Approved => self.approved_state.selected(),
        }
    }

    pub(crate) fn selected_receipt(&self) -> Option<&ReceiptSummary> {
        let receipts = self.receipts(self.active_queue);
        self.selected_index(self.active_queue)
            .and_then(|index| receipts.get(index))
    }

    pub(crate) fn selected_path_for_queue(&self, queue: Queue) -> Option<String> {
        let receipts = self.receipts(queue);
        self.selected_index(queue)
            .and_then(|index| receipts.get(index))
            .map(|receipt| receipt.path.clone())
    }

    pub(crate) fn sync_selection(&mut self, queue: Queue) {
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

    pub(crate) fn sync_selection_to_path(&mut self, queue: Queue, preferred_path: Option<&str>) {
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

    pub(crate) fn select_receipt_by_path(&mut self, queue: Queue, path: &str) -> bool {
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

    pub(crate) fn move_selection(&mut self, delta: isize) {
        let len = self.receipts(self.active_queue).len();
        if len == 0 {
            return;
        }
        let current = self.selected_index(self.active_queue).unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.list_state_mut(self.active_queue).select(Some(next));
    }

    pub(crate) fn switch_queue(&mut self) {
        self.active_queue = match self.active_queue {
            Queue::Scanned => Queue::Approved,
            Queue::Approved => Queue::Scanned,
        };
        self.sync_selection(self.active_queue);
        self.focus = PaneFocus::List;
    }

    pub(crate) fn switch_page(&mut self, page: Page) {
        if self.active_page == page {
            return;
        }
        self.active_page = page;
        self.set_status(Self::page_help(page));
    }

    pub(crate) fn set_status(&mut self, message: impl Into<String>) {
        let message = message.into();
        self.status = message.clone();
        self.push_status_log(message);
        if self.active_page == Page::Receipts && self.right_pane == RightPane::StatusLog {
            self.scroll_detail_to_bottom();
        }
    }

    pub(crate) fn set_error(&mut self, message: impl Into<String>) {
        self.show_status_log();
        self.set_status(message);
    }

    pub(crate) fn show_status_log(&mut self) {
        if self.active_page == Page::Receipts {
            self.right_pane = RightPane::StatusLog;
            self.scroll_detail_to_bottom();
        }
    }

    pub(crate) fn push_status_log(&mut self, message: String) {
        if !self.status_log_lines.is_empty() {
            self.status_log_lines.push(String::new());
        }
        self.status_log_lines
            .extend(message.lines().map(ToOwned::to_owned));
        if self.status_log_lines.is_empty() {
            self.status_log_lines.push(String::new());
        }
    }

    pub(crate) fn toggle_right_pane(&mut self) {
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

    pub(crate) fn right_pane_lines(&self) -> &[String] {
        match self.right_pane {
            RightPane::Details => &self.detail_lines,
            RightPane::StatusLog => &self.status_log_lines,
        }
    }

    pub(crate) fn right_pane_title(&self) -> String {
        match self.right_pane {
            RightPane::Details => match &self.detail_path {
                Some(path) => format!("Details: {path}"),
                None => "Details".to_string(),
            },
            RightPane::StatusLog => "Status Log".to_string(),
        }
    }

    pub(crate) fn refresh(&mut self) -> AppResult<()> {
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

    pub(crate) fn reload_receipts(&mut self) -> AppResult<()> {
        let selected_scanned = self.selected_path_for_queue(Queue::Scanned);
        let selected_approved = self.selected_path_for_queue(Queue::Approved);
        self.scanned = backend_list_receipts(Queue::Scanned)?;
        self.approved = backend_list_receipts(Queue::Approved)?;
        self.sync_selection_to_path(Queue::Scanned, selected_scanned.as_deref());
        self.sync_selection_to_path(Queue::Approved, selected_approved.as_deref());
        Ok(())
    }

    pub(crate) fn refresh_receipts_page(&mut self, force: bool) -> AppResult<()> {
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

    pub(crate) fn load_detail(&mut self) -> AppResult<()> {
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

    pub(crate) fn scroll_detail_vertical(&mut self, delta: i32) {
        if delta >= 0 {
            self.detail_scroll_y = self.detail_scroll_y.saturating_add(delta as u16);
        } else {
            self.detail_scroll_y = self.detail_scroll_y.saturating_sub((-delta) as u16);
        }
    }

    pub(crate) fn scroll_detail_horizontal(&mut self, delta: i32) {
        if delta >= 0 {
            self.detail_scroll_x = self.detail_scroll_x.saturating_add(delta as u16);
        } else {
            self.detail_scroll_x = self.detail_scroll_x.saturating_sub((-delta) as u16);
        }
    }

    pub(crate) fn scroll_detail_to_top(&mut self) {
        self.detail_scroll_y = 0;
    }

    pub(crate) fn scroll_detail_to_bottom(&mut self) {
        self.detail_scroll_y = self.right_pane_lines().len().saturating_sub(1) as u16;
    }

    pub(crate) fn focus_list(&mut self) {
        self.focus = PaneFocus::List;
    }

    pub(crate) fn focus_detail(&mut self) {
        self.focus = PaneFocus::Detail;
    }

    pub(crate) fn approve_selected_scanned(&mut self) -> AppResult<()> {
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

    pub(crate) fn open_selected_original_image(&mut self) {
        let Some(stage_path) = self.selected_receipt().map(|receipt| receipt.path.clone()) else {
            self.set_status("No receipt selected");
            return;
        };
        let Some(receipt_dir) = receipt_dir_from_stage_path(Path::new(&stage_path)) else {
            self.set_error(format!("Cannot derive receipt dir from {stage_path}"));
            return;
        };
        let image_path = match process_util::find_original_image(receipt_dir) {
            Ok(path) => path,
            Err(error) => {
                self.set_error(format!("Cannot find original image: {error}"));
                return;
            }
        };
        match process_util::xdg_open_detached(&image_path) {
            Ok(()) => self.set_status(format!("Opened {} via xdg-open", image_path.display())),
            Err(error) => {
                self.set_error(format!("xdg-open {} failed: {error}", image_path.display()))
            }
        }
    }

    pub(crate) fn begin_edit_selected(&mut self) {
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

    pub(crate) fn apply_review_changes(&mut self) -> AppResult<()> {
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

    pub(crate) fn can_match_selected_approved(&mut self) -> AppResult<bool> {
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

    pub(crate) fn begin_match_selected_approved(&mut self) -> AppResult<()> {
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

    pub(crate) fn apply_selected_match(&mut self) -> AppResult<()> {
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

    pub(crate) fn begin_config_edit(&mut self) {
        self.config_state = Some(ConfigState::from_response(&self.config));
        self.set_status("Edit project root, Enter to save, Esc to cancel, Backspace delete");
    }

    pub(crate) fn apply_config(&mut self) -> AppResult<()> {
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

    pub(crate) fn refresh_imports_page(&mut self) -> AppResult<()> {
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
        self.refresh_import_decisions()?;
        Ok(())
    }

    pub(crate) fn refresh_import_decisions(&mut self) -> AppResult<()> {
        let Some(route) = self.imports_state.selected_route().cloned() else {
            self.imports_state.clear_decisions();
            return Ok(());
        };
        if route.import_type != "chequing" {
            self.imports_state.clear_decisions();
            return Ok(());
        }
        let Some(account) = self.imports_state.selected_account().map(str::to_string) else {
            self.imports_state.clear_decisions();
            return Ok(());
        };
        let key = (route.source_path.clone(), account.clone());
        if self.imports_state.decisions_loaded_for.as_ref() == Some(&key) {
            return Ok(());
        }
        let response = backend_preflight_chequing_import(&route.csv_file, Some(&account))?;
        if response.status != "ok" {
            self.imports_state.decisions.clear();
            self.imports_state.decisions_state.select(None);
            self.imports_state.decisions_error = response.error.clone();
            self.imports_state.decisions_loaded_for = Some(key);
            self.set_status(format!(
                "Preflight failed: {}",
                response.error.as_deref().unwrap_or("unknown error")
            ));
            return Ok(());
        }
        let decisions: Vec<ImportDecisionView> = response
            .decisions
            .into_iter()
            .map(ImportDecisionView::from_payload)
            .collect();
        let count = decisions.len();
        self.imports_state.set_decisions(decisions, Some(key));
        if count == 0 {
            self.set_status("Preflight ok: no ambiguous decisions to resolve");
        } else {
            self.set_status(format!(
                "Preflight ok: {count} ambiguous decision(s) — focus Decisions pane (Tab) and press Enter to pick"
            ));
        }
        Ok(())
    }

    pub(crate) fn resolve_selected_import_accounts(&mut self) -> AppResult<()> {
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

    pub(crate) fn move_import_route_selection(&mut self, delta: isize) -> AppResult<()> {
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

    pub(crate) fn apply_selected_import(&mut self) -> AppResult<()> {
        let Some(route) = self.imports_state.selected_route().cloned() else {
            self.set_status("No import route selected. Press `r` to load statement routes.");
            return Ok(());
        };
        let selected_account = self.imports_state.selected_account().map(ToOwned::to_owned);
        // Credit-card statements route through an interactive category review before anything
        // is written; the review then finalizes the apply with the collected overrides.
        if route.import_type == "cc" {
            return self.start_cc_category_review(&route, selected_account);
        }
        let unresolved = self.imports_state.unresolved_decisions();
        if unresolved > 0 {
            self.set_status(format!(
                "Apply blocked: {} unresolved account decision(s). Focus Decisions pane (Tab) and press Enter to pick.",
                unresolved
            ));
            return Ok(());
        }
        let mut cc_overrides: Vec<ImportOverridePayload> = Vec::new();
        let mut bank_overrides: Vec<ImportOverridePayload> = Vec::new();
        for decision in &self.imports_state.decisions {
            let account = match decision.selected_account() {
                Some(a) => a.to_string(),
                None => continue,
            };
            let payload = ImportOverridePayload {
                date: decision.txn_date.clone(),
                description: decision.txn_description.clone(),
                amount: decision.txn_amount.clone(),
                account,
            };
            match decision.kind.as_str() {
                "cc_payment" => cc_overrides.push(payload),
                "bank_transfer" => bank_overrides.push(payload),
                _ => {}
            }
        }
        let response = backend_apply_import(
            &route.import_type,
            &route.csv_file,
            &route.importer_id,
            selected_account.as_deref(),
            self.imports_state.allow_uncommitted,
            &cc_overrides,
            &bank_overrides,
            &[],
        )?;
        let status = Self::import_status_message(&response, &route.csv_file);
        let _ = response;
        self.refresh_imports_page()?;
        self.set_status(status);
        Ok(())
    }

    fn import_status_message(response: &ApplyImportResponse, csv_file: &str) -> String {
        match response.status.as_str() {
            "ok" => response
                .summary
                .clone()
                .unwrap_or_else(|| format!("Imported {csv_file}")),
            "aborted" => response
                .error
                .clone()
                .unwrap_or_else(|| "Import aborted".to_string()),
            _ => response
                .error
                .clone()
                .unwrap_or_else(|| format!("Import failed: {}", response.status)),
        }
    }

    fn start_cc_category_review(
        &mut self,
        route: &ImportRouteOption,
        selected_account: Option<String>,
    ) -> AppResult<()> {
        let response = backend_preflight_cc_import(
            &route.csv_file,
            &route.importer_id,
            selected_account.as_deref(),
        )?;
        if response.status != "ok" {
            self.set_error(
                response
                    .error
                    .unwrap_or_else(|| format!("Category preflight failed for {}", route.csv_file)),
            );
            return Ok(());
        }
        let review = CcCategoryReview::new(
            route.csv_file.clone(),
            route.importer_id.clone(),
            selected_account,
            response,
        );
        let total = review.entries.len();
        let uncategorized = review
            .entries
            .iter()
            .filter(|entry| entry.uncategorized)
            .count();
        self.imports_state.cc_review = Some(review);
        self.set_status(format!(
            "Review categories for {total} transaction(s) ({uncategorized} uncategorized): \
             ↑↓ select · Enter change · a apply · Esc cancel"
        ));
        Ok(())
    }

    pub(crate) fn finalize_cc_category_review(&mut self) -> AppResult<()> {
        let Some(review) = self.imports_state.cc_review.take() else {
            return Ok(());
        };
        let edits = review.transaction_edits();
        let changed = edits.len();
        let deleted = review.deleted_count();
        let response = backend_apply_import(
            "cc",
            &review.csv_file,
            &review.importer_id,
            review.selected_account.as_deref(),
            self.imports_state.allow_uncommitted,
            &[],
            &[],
            &edits,
        )?;
        let mut status = Self::import_status_message(&response, &review.csv_file);
        if response.status == "ok" && changed > 0 {
            status = format!("{status} ({changed} edit(s), {deleted} deleted)");
        }
        self.refresh_imports_page()?;
        self.set_status(status);
        Ok(())
    }

    pub(crate) fn cancel_cc_category_review(&mut self) {
        if self.imports_state.cc_review.take().is_some() {
            self.set_status("Cancelled category review — nothing was written");
        }
    }

    pub(crate) fn selected_import_source_path(&mut self) -> AppResult<Option<String>> {
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

    pub(crate) fn trash_selected_import_csv(&mut self) -> AppResult<()> {
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

    pub(crate) fn refresh_current_page(&mut self) -> AppResult<()> {
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
                let runtime = preferred_ocr_runtime();
                self.set_status(format!(
                    "Refreshed {} container `{}` status and logs",
                    runtime.display_name(),
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

    pub(crate) fn refresh_runtime_pages(&mut self, force: bool) -> AppResult<()> {
        self.refresh_receipts_page(force)?;
        self.poll_serve_process()?;
        self.poll_fava_process()?;
        self.poll_ocr_action();
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

    pub(crate) fn refresh_serve_health(&mut self) {
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

    pub(crate) fn refresh_fava_health(&mut self) {
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

    pub(crate) fn refresh_ocr_page(&mut self) {
        self.ocr_state = query_ocr_page_state();
    }

    pub(crate) fn start_ocr_action(
        &mut self,
        runtime: OcrContainerRuntime,
        action: OcrAction,
    ) -> AppResult<()> {
        if self.pending_ocr_action.is_some() {
            self.set_status("An OCR container action is already running");
            return Ok(());
        }

        let (sender, receiver) = mpsc::channel();
        self.pending_ocr_action = Some(PendingOcrAction { receiver });
        self.set_status(action.progress_message(runtime));

        thread::spawn(move || {
            let command = action.rendered_command(runtime);
            let result = match action.execute(runtime) {
                Ok(output) => ensure_ocr_runtime_success(runtime, output, &command)
                    .map(|_| action.success_message(runtime))
                    .map_err(|error| error.to_string()),
                Err(error) => Err(error.to_string()),
            };
            let _ = sender.send(result);
        });

        Ok(())
    }

    pub(crate) fn poll_ocr_action(&mut self) {
        let outcome = match self.pending_ocr_action.as_mut() {
            Some(action) => match action.receiver.try_recv() {
                Ok(outcome) => Some(outcome),
                Err(TryRecvError::Empty) => None,
                Err(TryRecvError::Disconnected) => Some(Err(
                    "OCR container action worker exited unexpectedly".to_string(),
                )),
            },
            None => None,
        };

        let Some(outcome) = outcome else {
            return;
        };

        self.pending_ocr_action = None;
        self.refresh_ocr_page();
        self.last_ocr_refresh = Some(Instant::now());
        match outcome {
            Ok(message) => self.set_status(message),
            Err(error) => self.set_error(error),
        }
    }

    pub(crate) fn start_ocr_container(&mut self) -> AppResult<()> {
        let runtime = preferred_ocr_runtime();
        match ocr_container_state(runtime)? {
            OcrContainerState::Running => {
                self.refresh_runtime_pages(true)?;
                self.set_status(format!(
                    "{} container `{OCR_CONTAINER_NAME}` is already running",
                    runtime.display_name()
                ));
            }
            OcrContainerState::Stopped => {
                self.start_ocr_action(runtime, OcrAction::Start)?;
            }
            OcrContainerState::Missing => {
                self.start_ocr_action(runtime, OcrAction::CreateAndStart)?;
            }
        }
        Ok(())
    }

    pub(crate) fn stop_ocr_container(&mut self) -> AppResult<()> {
        let runtime = preferred_ocr_runtime();
        match ocr_container_state(runtime)? {
            OcrContainerState::Running => {
                self.start_ocr_action(runtime, OcrAction::Stop)?;
            }
            OcrContainerState::Stopped => {
                self.refresh_runtime_pages(true)?;
                self.set_status(format!(
                    "{} container `{OCR_CONTAINER_NAME}` is already stopped",
                    runtime.display_name()
                ));
            }
            OcrContainerState::Missing => {
                self.set_status(format!(
                    "Container `{OCR_CONTAINER_NAME}` does not exist. Create it first with `{}`",
                    runtime.suggested_run_command()
                ));
            }
        }
        Ok(())
    }

    pub(crate) fn restart_ocr_container(&mut self) -> AppResult<()> {
        let runtime = preferred_ocr_runtime();
        match ocr_container_state(runtime)? {
            OcrContainerState::Running | OcrContainerState::Stopped => {
                self.start_ocr_action(runtime, OcrAction::Restart)?;
            }
            OcrContainerState::Missing => {
                self.set_status(format!(
                    "Container `{OCR_CONTAINER_NAME}` does not exist. Create it first with `{}`",
                    runtime.suggested_run_command()
                ));
            }
        }
        Ok(())
    }

    pub(crate) fn poll_serve_process(&mut self) -> AppResult<()> {
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

    pub(crate) fn poll_fava_process(&mut self) -> AppResult<()> {
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

    pub(crate) fn restart_serve_process(&mut self) -> AppResult<()> {
        self.poll_serve_process()?;
        if self.serve_state.process.is_some() {
            self.stop_serve_process()?;
        }
        self.start_serve_process()
    }

    pub(crate) fn start_serve_process(&mut self) -> AppResult<()> {
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

    pub(crate) fn restart_fava_process(&mut self) -> AppResult<()> {
        self.poll_fava_process()?;
        if self.fava_state.process.is_some() {
            self.stop_fava_process()?;
        }
        self.start_fava_process()
    }

    pub(crate) fn start_fava_process(&mut self) -> AppResult<()> {
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

    pub(crate) fn stop_serve_process(&mut self) -> AppResult<()> {
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

    pub(crate) fn stop_fava_process(&mut self) -> AppResult<()> {
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

    pub(crate) fn shutdown(&mut self) -> AppResult<()> {
        if self.serve_state.process.is_some() {
            self.stop_serve_process()?;
        }
        if self.fava_state.process.is_some() {
            self.stop_fava_process()?;
        }
        Ok(())
    }

    /// Boot the long-lived services the Receipts pane depends on. Errors are
    /// demoted to the status log so a missing OCR runtime or a busy port does
    /// not abort the TUI; the user can recover from the OCR/serve/fava panes.
    pub(crate) fn autostart_services(&mut self) {
        self.set_status("Auto-starting `bb serve`, OCR container, and Fava…");
        if let Err(error) = self.start_serve_process() {
            self.set_error(format!("Auto-start `bb serve` failed: {error}"));
        }
        if let Err(error) = self.start_ocr_container() {
            self.set_error(format!("Auto-start OCR container failed: {error}"));
        }
        // Fava needs a configured ledger path; skip silently when the project
        // root isn't set yet rather than dumping a predictable error.
        if self.config.resolved_main_beancount_path.is_empty() {
            return;
        }
        if let Err(error) = self.start_fava_process() {
            self.set_error(format!("Auto-start Fava failed: {error}"));
        }
    }
}
