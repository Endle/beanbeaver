use super::*;

use std::collections::VecDeque;
use std::io;
use std::path::Path;
use std::process::Child;
use std::sync::mpsc::Receiver;
use std::sync::{Arc, Mutex};

use ratatui::widgets::ListState;
use serde_json::Value;

#[derive(Clone, Debug)]
pub(crate) struct ImportPageState {
    pub(crate) routes: Vec<ImportRouteOption>,
    pub(crate) route_state: ListState,
    pub(crate) focus: ImportPaneFocus,
    pub(crate) has_uncommitted_changes: bool,
    pub(crate) allow_uncommitted: bool,
    pub(crate) planner_status: String,
    pub(crate) planner_error: Option<String>,
    pub(crate) account_label: Option<String>,
    pub(crate) account_options: Vec<String>,
    pub(crate) account_state: ListState,
    pub(crate) account_as_of: Option<String>,
    pub(crate) account_error: Option<String>,
    pub(crate) decisions: Vec<ImportDecisionView>,
    pub(crate) decisions_state: ListState,
    pub(crate) decisions_error: Option<String>,
    pub(crate) decisions_loaded_for: Option<(String, String)>,
    pub(crate) decision_picker: Option<DecisionPickerState>,
    pub(crate) cc_review: Option<CcCategoryReview>,
}

/// Interactive post-import category review for one credit-card statement.
///
/// Built from `preflight-cc-import` (nothing is written yet); the user adjusts categories,
/// then apply commits the statement with the collected overrides.
#[derive(Clone, Debug)]
pub(crate) struct CcCategoryReview {
    pub(crate) csv_file: String,
    pub(crate) importer_id: String,
    pub(crate) selected_account: Option<String>,
    pub(crate) card_account: Option<String>,
    pub(crate) entries: Vec<CcCategoryEntryView>,
    pub(crate) entries_state: ListState,
    pub(crate) candidate_categories: Vec<String>,
    pub(crate) editor: Option<CcEntryEditor>,
    pub(crate) has_uncommitted_changes: bool,
}

impl CcCategoryReview {
    pub(crate) fn new(
        csv_file: String,
        importer_id: String,
        selected_account: Option<String>,
        response: PreflightCcImportResponse,
    ) -> Self {
        let entries: Vec<CcCategoryEntryView> = response
            .entries
            .into_iter()
            .map(CcCategoryEntryView::from_payload)
            .collect();
        let mut entries_state = ListState::default();
        entries_state.select(if entries.is_empty() { None } else { Some(0) });
        Self {
            csv_file,
            importer_id,
            selected_account,
            card_account: response.card_account,
            entries,
            entries_state,
            candidate_categories: response.candidate_categories,
            editor: None,
            has_uncommitted_changes: response.has_uncommitted_changes,
        }
    }

    pub(crate) fn move_selection(&mut self, delta: isize) {
        let len = self.entries.len();
        if len == 0 {
            self.entries_state.select(None);
            return;
        }
        let current = self.entries_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.entries_state.select(Some(next));
    }

    pub(crate) fn open_editor(&mut self) {
        let Some(index) = self.entries_state.selected() else {
            return;
        };
        if self.entries.get(index).is_none() {
            return;
        }
        self.editor = Some(CcEntryEditor::new(index));
    }

    pub(crate) fn close_editor(&mut self) {
        self.editor = None;
    }

    pub(crate) fn editor_entry_index(&self) -> Option<usize> {
        self.editor.as_ref().map(|editor| editor.entry_index)
    }

    /// Open the category picker for the transaction currently in the editor.
    pub(crate) fn open_picker(&mut self) {
        let Some(index) = self.editor_entry_index() else {
            return;
        };
        let Some(entry) = self.entries.get(index) else {
            return;
        };
        if self.candidate_categories.is_empty() {
            return;
        }
        let selected = self
            .candidate_categories
            .iter()
            .position(|candidate| candidate == &entry.chosen_category)
            .unwrap_or(0);
        if let Some(editor) = self.editor.as_mut() {
            editor.picker = Some(CategoryPickerState::new(index, selected));
        }
    }

    pub(crate) fn close_picker(&mut self) {
        if let Some(editor) = self.editor.as_mut() {
            editor.picker = None;
        }
    }

    pub(crate) fn confirm_picker(&mut self) -> Option<String> {
        let editor = self.editor.as_mut()?;
        let picker = editor.picker.take()?;
        let selection = picker.category_state.selected()?;
        let category = self.candidate_categories.get(selection)?.clone();
        let entry = self.entries.get_mut(picker.item_index)?;
        entry.chosen_category = category.clone();
        Some(category)
    }

    /// Begin editing the amount of the transaction in the editor.
    pub(crate) fn begin_amount_input(&mut self) {
        let Some(index) = self.editor_entry_index() else {
            return;
        };
        let value = self
            .entries
            .get(index)
            .map(|entry| entry.amount.clone())
            .unwrap_or_default();
        if let Some(editor) = self.editor.as_mut() {
            editor.amount_input = Some(value);
        }
    }

    pub(crate) fn amount_input_push(&mut self, ch: char) {
        if let Some(buffer) = self
            .editor
            .as_mut()
            .and_then(|editor| editor.amount_input.as_mut())
        {
            buffer.push(ch);
        }
    }

    pub(crate) fn amount_input_backspace(&mut self) {
        if let Some(buffer) = self
            .editor
            .as_mut()
            .and_then(|editor| editor.amount_input.as_mut())
        {
            buffer.pop();
        }
    }

    pub(crate) fn cancel_amount_input(&mut self) {
        if let Some(editor) = self.editor.as_mut() {
            editor.amount_input = None;
        }
    }

    pub(crate) fn commit_amount_input(&mut self) -> Option<String> {
        let editor = self.editor.as_mut()?;
        let buffer = editor.amount_input.take()?;
        let entry_index = editor.entry_index;
        let trimmed = buffer.trim().to_string();
        let entry = self.entries.get_mut(entry_index)?;
        if !trimmed.is_empty() {
            entry.amount = trimmed;
        }
        Some(entry.amount.clone())
    }

    /// Toggle the deleted flag of the transaction in the editor; returns the new state.
    pub(crate) fn toggle_editor_deleted(&mut self) -> Option<bool> {
        let index = self.editor_entry_index()?;
        let entry = self.entries.get_mut(index)?;
        entry.deleted = !entry.deleted;
        Some(entry.deleted)
    }

    /// Toggle deletion of the transaction highlighted in the list (no editor needed).
    pub(crate) fn toggle_selected_deleted(&mut self) -> Option<bool> {
        let index = self.entries_state.selected()?;
        let entry = self.entries.get_mut(index)?;
        entry.deleted = !entry.deleted;
        Some(entry.deleted)
    }

    pub(crate) fn changed_count(&self) -> usize {
        self.entries.iter().filter(|entry| entry.is_changed()).count()
    }

    pub(crate) fn deleted_count(&self) -> usize {
        self.entries.iter().filter(|entry| entry.deleted).count()
    }

    pub(crate) fn transaction_edits(&self) -> Vec<CcTransactionEditPayload> {
        self.entries
            .iter()
            .filter(|entry| entry.is_changed())
            .map(|entry| CcTransactionEditPayload {
                date: entry.date.clone(),
                payee: entry.payee.clone(),
                amount: entry.original_amount.clone(),
                category: entry.chosen_category.clone(),
                new_amount: if entry.amount_changed() {
                    Some(entry.amount.clone())
                } else {
                    None
                },
                deleted: entry.deleted,
            })
            .collect()
    }
}

impl ImportPageState {
    pub(crate) fn new() -> Self {
        let mut route_state = ListState::default();
        route_state.select(None);
        let mut account_state = ListState::default();
        account_state.select(None);
        let mut decisions_state = ListState::default();
        decisions_state.select(None);
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
            decisions: Vec::new(),
            decisions_state,
            decisions_error: None,
            decisions_loaded_for: None,
            decision_picker: None,
            cc_review: None,
        }
    }

    pub(crate) fn open_decision_picker(&mut self) {
        let Some(index) = self.decisions_state.selected() else {
            return;
        };
        let Some(decision) = self.decisions.get(index) else {
            return;
        };
        if decision.candidates.is_empty() {
            return;
        }
        self.decision_picker = Some(DecisionPickerState::new(index, decision.selected_candidate));
    }

    pub(crate) fn first_unresolved_index(&self) -> Option<usize> {
        self.decisions
            .iter()
            .position(|decision| decision.selected_account().is_none())
    }

    pub(crate) fn jump_to_first_unresolved_and_open(&mut self) -> bool {
        let Some(index) = self.first_unresolved_index() else {
            return false;
        };
        self.focus = ImportPaneFocus::Decisions;
        self.decisions_state.select(Some(index));
        self.open_decision_picker();
        true
    }

    pub(crate) fn close_decision_picker(&mut self) {
        self.decision_picker = None;
    }

    pub(crate) fn confirm_decision_picker(&mut self) -> Option<String> {
        let picker = self.decision_picker.take()?;
        let selection = picker.list_state.selected()?;
        let decision = self.decisions.get_mut(picker.decision_index)?;
        if selection >= decision.candidates.len() {
            return None;
        }
        decision.selected_candidate = Some(selection);
        decision.candidates.get(selection).cloned()
    }

    pub(crate) fn clear_decision_picker_selection(&mut self) {
        let Some(picker) = self.decision_picker.take() else {
            return;
        };
        if let Some(decision) = self.decisions.get_mut(picker.decision_index) {
            decision.selected_candidate = None;
        }
    }

    pub(crate) fn set_decisions(
        &mut self,
        decisions: Vec<ImportDecisionView>,
        key: Option<(String, String)>,
    ) {
        self.decisions = decisions;
        self.decisions_loaded_for = key;
        self.decisions_error = None;
        if self.decisions.is_empty() {
            self.decisions_state.select(None);
        } else {
            self.decisions_state.select(Some(0));
        }
    }

    pub(crate) fn clear_decisions(&mut self) {
        self.decisions.clear();
        self.decisions_loaded_for = None;
        self.decisions_error = None;
        self.decisions_state.select(None);
        self.decision_picker = None;
    }

    pub(crate) fn move_decisions_selection(&mut self, delta: isize) {
        let len = self.decisions.len();
        if len == 0 {
            self.decisions_state.select(None);
            return;
        }
        let current = self.decisions_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.decisions_state.select(Some(next));
    }

    pub(crate) fn unresolved_decisions(&self) -> usize {
        self.decisions
            .iter()
            .filter(|decision| decision.selected_account().is_none())
            .count()
    }

    pub(crate) fn selected_route(&self) -> Option<&ImportRouteOption> {
        self.route_state
            .selected()
            .and_then(|index| self.routes.get(index))
    }

    pub(crate) fn selected_account(&self) -> Option<&str> {
        self.account_state
            .selected()
            .and_then(|index| self.account_options.get(index))
            .map(String::as_str)
    }

    pub(crate) fn set_routes(
        &mut self,
        routes: Vec<ImportRouteOption>,
        preferred_source_path: Option<&str>,
    ) {
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

    pub(crate) fn move_route_selection(&mut self, delta: isize) {
        let len = self.routes.len();
        if len == 0 {
            self.route_state.select(None);
            return;
        }
        let current = self.route_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.route_state.select(Some(next));
    }

    pub(crate) fn move_account_selection(&mut self, delta: isize) {
        let len = self.account_options.len();
        if len == 0 {
            self.account_state.select(None);
            return;
        }
        let current = self.account_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.account_state.select(Some(next));
    }

    pub(crate) fn clear_account_resolution(&mut self) {
        self.account_label = None;
        self.account_options.clear();
        self.account_state.select(None);
        self.account_as_of = None;
        self.account_error = None;
        self.clear_decisions();
        self.cc_review = None;
    }

    pub(crate) fn apply_account_resolution(
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
}

pub(crate) struct ReviewState {
    pub(crate) source_queue: Queue,
    pub(crate) path: String,
    pub(crate) receipt_dir: String,
    pub(crate) stage_file: String,
    pub(crate) original_document: Value,
    pub(crate) pane: ReviewPane,
    pub(crate) preview_tab: ReviewTab,
    pub(crate) preview_scroll_y: u16,
    pub(crate) fields: Vec<ReceiptFieldState>,
    pub(crate) field_state: ListState,
    pub(crate) items: Vec<ReviewItemState>,
    pub(crate) item_state: ListState,
    pub(crate) category_options: Vec<CategoryOption>,
    pub(crate) item_editor: Option<ItemEditorState>,
    pub(crate) category_picker: Option<CategoryPickerState>,
    pub(crate) text_input: Option<TextInputState>,
    pub(crate) next_added_item_number: usize,
}

pub(crate) struct ConfigState {
    pub(crate) project_root: String,
}

pub(crate) struct MatchState {
    pub(crate) candidates: Vec<MatchCandidateSummary>,
    pub(crate) state: ListState,
    pub(crate) ledger_path: String,
    pub(crate) warning: Option<String>,
}

pub(crate) struct ManagedProcess {
    pub(crate) child: Child,
    pub(crate) command: String,
}

pub(crate) struct PendingOcrAction {
    pub(crate) receiver: Receiver<Result<String, String>>,
}

pub(crate) struct ServePageState {
    pub(crate) process: Option<ManagedProcess>,
    pub(crate) log_lines: Arc<Mutex<VecDeque<String>>>,
    pub(crate) health_ok: bool,
    pub(crate) health_message: String,
    pub(crate) last_exit_code: Option<i32>,
}

pub(crate) struct FavaPageState {
    pub(crate) process: Option<ManagedProcess>,
    pub(crate) log_lines: Arc<Mutex<VecDeque<String>>>,
    pub(crate) health_ok: bool,
    pub(crate) health_message: String,
    pub(crate) last_exit_code: Option<i32>,
}

pub(crate) struct OcrPageState {
    pub(crate) runtime: OcrContainerRuntime,
    pub(crate) summary_lines: Vec<String>,
    pub(crate) log_lines: Vec<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OcrAction {
    Start,
    Stop,
    Restart,
    CreateAndStart,
}

impl OcrAction {
    pub(crate) fn progress_message(self, runtime: OcrContainerRuntime) -> String {
        match self {
            Self::Start => format!(
                "Starting {} container `{OCR_CONTAINER_NAME}` in the background...",
                runtime.display_name()
            ),
            Self::Stop => format!(
                "Stopping {} container `{OCR_CONTAINER_NAME}` in the background...",
                runtime.display_name()
            ),
            Self::Restart => format!(
                "Restarting {} container `{OCR_CONTAINER_NAME}` in the background...",
                runtime.display_name()
            ),
            Self::CreateAndStart => format!(
                "Creating and starting {} container `{OCR_CONTAINER_NAME}` in the background...",
                runtime.display_name()
            ),
        }
    }

    pub(crate) fn success_message(self, runtime: OcrContainerRuntime) -> String {
        match self {
            Self::Start => format!(
                "Started {} container `{OCR_CONTAINER_NAME}`",
                runtime.display_name()
            ),
            Self::Stop => format!(
                "Stopped {} container `{OCR_CONTAINER_NAME}`",
                runtime.display_name()
            ),
            Self::Restart => format!(
                "Restarted {} container `{OCR_CONTAINER_NAME}`",
                runtime.display_name()
            ),
            Self::CreateAndStart => format!(
                "Created and started {} container `{OCR_CONTAINER_NAME}`",
                runtime.display_name()
            ),
        }
    }

    pub(crate) fn rendered_command(self, runtime: OcrContainerRuntime) -> String {
        match self {
            Self::Start => render_ocr_runtime_command(runtime, &["start", OCR_CONTAINER_NAME]),
            Self::Stop => render_ocr_runtime_command(runtime, &["stop", OCR_CONTAINER_NAME]),
            Self::Restart => render_ocr_runtime_command(runtime, &["restart", OCR_CONTAINER_NAME]),
            Self::CreateAndStart => runtime.suggested_run_command().to_string(),
        }
    }

    pub(crate) fn execute(self, runtime: OcrContainerRuntime) -> io::Result<std::process::Output> {
        match self {
            Self::Start => process_util::ocr_start(runtime),
            Self::Stop => process_util::ocr_stop(runtime),
            Self::Restart => process_util::ocr_restart(runtime),
            Self::CreateAndStart => process_util::ocr_create_and_start(runtime),
        }
    }
}

impl ConfigState {
    pub(crate) fn from_response(config: &ConfigResponse) -> Self {
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
    pub(crate) fn new() -> Self {
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

    pub(crate) fn snapshot_logs(&self) -> Vec<String> {
        snapshot_log_lines(&self.log_lines)
    }
}

impl OcrPageState {
    pub(crate) fn new() -> Self {
        let runtime = preferred_ocr_runtime();
        Self {
            runtime,
            summary_lines: vec![format!(
                "Refreshing {} container state...",
                runtime.display_name()
            )],
            log_lines: vec!["No container logs loaded yet.".to_string()],
        }
    }
}

impl FavaPageState {
    pub(crate) fn new() -> Self {
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

    pub(crate) fn snapshot_logs(&self) -> Vec<String> {
        snapshot_log_lines(&self.log_lines)
    }
}

impl MatchState {
    pub(crate) fn new(response: MatchCandidatesResponse) -> Self {
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

    pub(crate) fn selected(&self) -> Option<&MatchCandidateSummary> {
        self.state
            .selected()
            .and_then(|index| self.candidates.get(index))
    }

    pub(crate) fn move_selection(&mut self, delta: isize) {
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
    pub(crate) fn from_detail(
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

    pub(crate) fn mode_label(&self) -> &'static str {
        match self.source_queue {
            Queue::Scanned => "Review Scanned Receipt",
            Queue::Approved => "Review Approved Receipt",
        }
    }

    pub(crate) fn submit_label(&self) -> &'static str {
        match self.source_queue {
            Queue::Scanned => "approve",
            Queue::Approved => "save",
        }
    }

    pub(crate) fn selected_field_index(&self) -> Option<usize> {
        self.field_state.selected()
    }

    pub(crate) fn selected_item_index(&self) -> Option<usize> {
        self.item_state.selected()
    }

    pub(crate) fn start_selected_field_edit(&mut self) {
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

    pub(crate) fn start_item_description_edit(&mut self, index: usize) {
        if let Some(item) = self.items.get(index) {
            self.text_input = Some(TextInputState::with_value(
                ReviewEditTarget::ItemDescription(index),
                format!("Item Description ({})", item.id),
                item.description.clone(),
            ));
        }
    }

    pub(crate) fn start_item_price_edit(&mut self, index: usize) {
        if let Some(item) = self.items.get(index) {
            self.text_input = Some(TextInputState::with_value(
                ReviewEditTarget::ItemPrice(index),
                format!("Item Price ({})", item.id),
                item.price.clone(),
            ));
        }
    }

    pub(crate) fn start_item_notes_edit(&mut self, index: usize) {
        if let Some(item) = self.items.get(index) {
            self.text_input = Some(TextInputState::with_value(
                ReviewEditTarget::ItemNotes(index),
                format!("Item Notes ({})", item.id),
                item.notes.clone(),
            ));
        }
    }

    pub(crate) fn open_selected_item_editor(&mut self) {
        let Some(index) = self.selected_item_index() else {
            return;
        };
        self.item_editor = Some(ItemEditorState::new(index));
    }

    pub(crate) fn item_editor_select_field(&mut self, field: ItemEditorField) {
        if self.item_editor.is_none() {
            self.open_selected_item_editor();
        }
        if let Some(editor) = self.item_editor.as_mut() {
            editor.select_field(field);
        }
    }

    pub(crate) fn open_selected_category_picker(&mut self) {
        let Some(index) = self.selected_item_index() else {
            return;
        };
        self.open_category_picker(index);
    }

    pub(crate) fn open_category_picker_from_item_editor(&mut self) {
        let Some(index) = self.item_editor.as_ref().map(|editor| editor.item_index) else {
            return;
        };
        self.open_category_picker(index);
    }

    pub(crate) fn open_category_picker(&mut self, index: usize) {
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

    pub(crate) fn next_added_item_id(&mut self) -> String {
        loop {
            let candidate = format!("item-added-{:04}", self.next_added_item_number);
            self.next_added_item_number += 1;
            if self.items.iter().all(|item| item.id != candidate) {
                return candidate;
            }
        }
    }

    pub(crate) fn add_item(&mut self) -> String {
        let id = self.next_added_item_id();
        self.items.push(ReviewItemState::new_added(id.clone()));
        let index = self.items.len().saturating_sub(1);
        self.item_state.select(Some(index));
        self.item_editor = Some(ItemEditorState::new(index));
        id
    }

    pub(crate) fn toggle_item_removed(&mut self, index: usize) -> Option<String> {
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

    pub(crate) fn toggle_item_editor_removed(&mut self) -> Option<String> {
        let item_index = self.item_editor.as_ref()?.item_index;
        self.toggle_item_removed(item_index)
    }

    pub(crate) fn apply_selected_category(&mut self) -> Option<String> {
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

    pub(crate) fn activate_item_editor_selection(&mut self) -> Option<String> {
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

    pub(crate) fn commit_text_input(&mut self) {
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

    pub(crate) fn payload(&self) -> Value {
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

    pub(crate) fn preview_receipt_field_value(&self, index: usize) -> &str {
        if let Some(input) = &self.text_input {
            if let ReviewEditTarget::ReceiptField(target_index) = input.target {
                if target_index == index {
                    return input.value.as_str();
                }
            }
        }
        self.fields
            .get(index)
            .map(|field| field.value.as_str())
            .unwrap_or("")
    }

    pub(crate) fn preview_item_description(&self, index: usize) -> &str {
        if let Some(input) = &self.text_input {
            if let ReviewEditTarget::ItemDescription(target_index) = input.target {
                if target_index == index {
                    return input.value.as_str();
                }
            }
        }
        self.items
            .get(index)
            .map(|item| item.description.as_str())
            .unwrap_or("")
    }

    pub(crate) fn preview_item_price(&self, index: usize) -> &str {
        if let Some(input) = &self.text_input {
            if let ReviewEditTarget::ItemPrice(target_index) = input.target {
                if target_index == index {
                    return input.value.as_str();
                }
            }
        }
        self.items
            .get(index)
            .map(|item| item.price.as_str())
            .unwrap_or("")
    }

    pub(crate) fn preview_item_notes(&self, index: usize) -> &str {
        if let Some(input) = &self.text_input {
            if let ReviewEditTarget::ItemNotes(target_index) = input.target {
                if target_index == index {
                    return input.value.as_str();
                }
            }
        }
        self.items
            .get(index)
            .map(|item| item.notes.as_str())
            .unwrap_or("")
    }

    pub(crate) fn itemized_total_scaled(&self) -> i64 {
        let item_total: i64 = self
            .items
            .iter()
            .enumerate()
            .filter(|(_, item)| !item.removed)
            .map(|(index, _)| review_decimal_to_scaled(self.preview_item_price(index)))
            .sum();
        let tax = self
            .fields
            .iter()
            .position(|field| field.field == ReceiptReviewField::Tax)
            .map(|index| review_decimal_to_scaled(self.preview_receipt_field_value(index)))
            .unwrap_or(0);
        item_total + tax
    }

    pub(crate) fn effective_preview_lines(&self) -> Vec<String> {
        let mut lines = vec![
            format!("Receipt Dir: {}", self.receipt_dir),
            format!("Stage File: {}", self.stage_file),
            String::new(),
            "Receipt".to_string(),
        ];
        for (index, field) in self.fields.iter().enumerate() {
            let value = self.preview_receipt_field_value(index);
            let value = if value.trim().is_empty() {
                "<empty>"
            } else {
                value
            };
            lines.push(format!("{:>8}: {}", field.field.label(), value));
        }
        lines.push(format!(
            "Itemized Total: ${}",
            review_scaled_to_currency(self.itemized_total_scaled())
        ));
        lines.push(String::new());
        lines.push(format!(
            "Items ({})",
            self.items.iter().filter(|item| !item.removed).count()
        ));
        for (index, (item_index, item)) in self
            .items
            .iter()
            .enumerate()
            .filter(|(_, item)| !item.removed)
            .enumerate()
        {
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
                self.preview_item_description(item_index),
                new_item,
                quantity,
                if self.preview_item_price(item_index).is_empty() {
                    "0.00"
                } else {
                    self.preview_item_price(item_index)
                },
                category,
            ));
            if !self.preview_item_notes(item_index).trim().is_empty() {
                lines.push(format!(
                    "     notes: {}",
                    self.preview_item_notes(item_index)
                ));
            }
        }
        let removed = self.items.iter().filter(|item| item.removed).count();
        if removed > 0 {
            lines.push(String::new());
            lines.push(format!("Removed items: {}", removed));
        }
        lines
    }

    pub(crate) fn diff_lines(&self) -> Vec<String> {
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

    pub(crate) fn raw_json_lines(&self) -> Vec<String> {
        match serde_json::to_string_pretty(&self.original_document) {
            Ok(json) => json.lines().map(ToOwned::to_owned).collect(),
            Err(error) => vec![format!("Failed to render JSON: {error}")],
        }
    }

    pub(crate) fn preview_lines(&self) -> Vec<String> {
        match self.preview_tab {
            ReviewTab::Effective => self.effective_preview_lines(),
            ReviewTab::Diff => self.diff_lines(),
            ReviewTab::Raw => self.raw_json_lines(),
        }
    }
}

pub(crate) fn receipt_dir_from_stage_path(stage_path: &Path) -> Option<&Path> {
    let parent = stage_path.parent()?;
    if parent.file_name().and_then(|name| name.to_str()) == Some("stages") {
        parent.parent()
    } else {
        Some(parent)
    }
}

pub(crate) fn review_decimal_to_scaled(value: &str) -> i64 {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return 0;
    }

    let negative = trimmed.starts_with('-');
    let unsigned = trimmed.trim_start_matches('-');
    let mut parts = unsigned.splitn(2, '.');
    let whole = parts.next().unwrap_or("0").parse::<i64>().unwrap_or(0);
    let frac_raw = parts.next().unwrap_or("0");
    let mut frac = frac_raw.chars().take(4).collect::<String>();
    while frac.len() < 4 {
        frac.push('0');
    }
    let frac_value = frac.parse::<i64>().unwrap_or(0);
    let value = whole * 10_000 + frac_value;
    if negative {
        -value
    } else {
        value
    }
}

pub(crate) fn review_scaled_to_currency(value: i64) -> String {
    format!("{:.2}", (value as f64) / 10_000.0)
}
