use super::*;

use ratatui::widgets::ListState;
use serde::Deserialize;
use serde_json::Value;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OcrContainerRuntime {
    Podman,
    Docker,
}

impl OcrContainerRuntime {
    pub(crate) fn command(self) -> &'static str {
        match self {
            Self::Podman => "podman",
            Self::Docker => "docker",
        }
    }

    pub(crate) fn display_name(self) -> &'static str {
        match self {
            Self::Podman => "Podman",
            Self::Docker => "Docker",
        }
    }

    pub(crate) fn suggested_run_command(self) -> &'static str {
        match self {
            Self::Podman => {
                "podman run -d --replace --name beanbeaver-ocr --network=slirp4netns -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest"
            }
            Self::Docker => {
                "docker run -d --name beanbeaver-ocr -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest"
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Page {
    Receipts,
    Serve,
    Fava,
    Ocr,
    Imports,
}

impl Page {
    pub(crate) fn tab_index(self) -> usize {
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
pub(crate) enum Queue {
    Scanned,
    Approved,
}

impl Queue {
    pub(crate) fn title(self) -> &'static str {
        match self {
            Queue::Scanned => "Scanned",
            Queue::Approved => "Approved",
        }
    }

    pub(crate) fn api_list_command(self) -> &'static str {
        match self {
            Queue::Scanned => "list-scanned",
            Queue::Approved => "list-approved",
        }
    }

    pub(crate) fn tab_index(self) -> usize {
        match self {
            Queue::Scanned => 0,
            Queue::Approved => 1,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum PaneFocus {
    List,
    Detail,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum RightPane {
    Details,
    StatusLog,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ImportPaneFocus {
    Routes,
    Accounts,
    Decisions,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OcrContainerState {
    Missing,
    Running,
    Stopped,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ReceiptsResponse {
    pub(crate) receipts: Vec<ReceiptSummary>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ReceiptSummary {
    pub(crate) path: String,
    pub(crate) receipt_dir: String,
    pub(crate) stage_file: String,
    pub(crate) merchant: Option<String>,
    pub(crate) date: Option<String>,
    pub(crate) total: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ShowReceiptResponse {
    pub(crate) path: String,
    pub(crate) summary: ReceiptSummary,
    pub(crate) document: Value,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct CategoryOption {
    pub(crate) key: String,
    pub(crate) account: String,
}

impl CategoryOption {
    pub(crate) fn display_label(&self) -> String {
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
pub(crate) struct CategoryListResponse {
    pub(crate) categories: Vec<CategoryOption>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ApproveReceiptResponse {
    pub(crate) status: String,
    pub(crate) source_path: String,
    pub(crate) approved_path: String,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ReEditApprovedResponse {
    pub(crate) status: String,
    #[serde(rename = "source_path")]
    pub(crate) _source_path: String,
    pub(crate) updated_path: Option<String>,
    pub(crate) normalize_error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ConfigResponse {
    pub(crate) config_path: String,
    pub(crate) project_root: String,
    pub(crate) resolved_project_root: String,
    pub(crate) resolved_main_beancount_path: String,
    pub(crate) receipts_dir: String,
    #[serde(default, rename = "scanned_dir")]
    pub(crate) _scanned_dir: String,
    #[serde(default, rename = "approved_dir")]
    pub(crate) _approved_dir: String,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct MatchCandidateSummary {
    pub(crate) file_path: String,
    pub(crate) line_number: i32,
    pub(crate) confidence: f64,
    pub(crate) display: String,
    pub(crate) payee: Option<String>,
    pub(crate) narration: Option<String>,
    pub(crate) date: String,
    pub(crate) amount: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct MatchCandidatesResponse {
    #[serde(rename = "path")]
    pub(crate) _path: String,
    pub(crate) ledger_path: String,
    pub(crate) errors: Vec<String>,
    pub(crate) warning: Option<String>,
    pub(crate) candidates: Vec<MatchCandidateSummary>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ApplyMatchResponse {
    pub(crate) status: String,
    pub(crate) message: Option<String>,
    #[serde(rename = "matched_receipt_path")]
    pub(crate) _matched_receipt_path: Option<String>,
    #[serde(rename = "enriched_path")]
    pub(crate) _enriched_path: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ImportRouteOption {
    pub(crate) csv_file: String,
    pub(crate) source_path: String,
    pub(crate) import_type: String,
    pub(crate) importer_id: String,
    #[serde(default)]
    #[allow(dead_code)]
    pub(crate) rule_id: String,
    #[serde(default)]
    #[allow(dead_code)]
    pub(crate) stage: u32,
}

impl ImportRouteOption {
    pub(crate) fn display_label(&self) -> String {
        format!(
            "{}  {}  {}",
            self.import_type_label(),
            self.importer_id,
            self.csv_file
        )
    }

    pub(crate) fn import_type_label(&self) -> &'static str {
        match self.import_type.as_str() {
            "cc" => "Credit Card",
            "chequing" => "Chequing",
            _ => "Unknown",
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct RefreshImportPageResponse {
    pub(crate) planner_status: String,
    pub(crate) has_uncommitted_changes: bool,
    #[serde(default)]
    pub(crate) routes: Vec<ImportRouteOption>,
    pub(crate) selected_source_path: Option<String>,
    pub(crate) account_resolution: Option<ResolveImportAccountsResponse>,
    pub(crate) planner_error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ResolveImportAccountsResponse {
    pub(crate) status: String,
    #[serde(rename = "import_type")]
    pub(crate) _import_type: String,
    #[serde(rename = "csv_file")]
    pub(crate) _csv_file: String,
    #[serde(rename = "importer_id")]
    pub(crate) _importer_id: String,
    pub(crate) account_label: Option<String>,
    pub(crate) account_options: Option<Vec<String>>,
    pub(crate) as_of: Option<String>,
    pub(crate) error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ImportDecisionPayload {
    pub(crate) kind: String,
    pub(crate) pattern: String,
    pub(crate) txn_date: String,
    pub(crate) txn_description: String,
    pub(crate) txn_amount: String,
    pub(crate) candidates: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct PreflightChequingImportResponse {
    pub(crate) status: String,
    #[serde(default)]
    pub(crate) decisions: Vec<ImportDecisionPayload>,
    pub(crate) error: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct ImportDecisionView {
    pub(crate) kind: String,
    pub(crate) pattern: String,
    pub(crate) txn_date: String,
    pub(crate) txn_description: String,
    pub(crate) txn_amount: String,
    pub(crate) candidates: Vec<String>,
    pub(crate) selected_candidate: Option<usize>,
}

impl ImportDecisionView {
    pub(crate) fn from_payload(payload: ImportDecisionPayload) -> Self {
        Self {
            kind: payload.kind,
            pattern: payload.pattern,
            txn_date: payload.txn_date,
            txn_description: payload.txn_description,
            txn_amount: payload.txn_amount,
            candidates: payload.candidates,
            selected_candidate: None,
        }
    }

    pub(crate) fn selected_account(&self) -> Option<&str> {
        self.selected_candidate
            .and_then(|index| self.candidates.get(index))
            .map(String::as_str)
    }

    pub(crate) fn display_label(&self) -> String {
        let kind_label = match self.kind.as_str() {
            "cc_payment" => "CC payment",
            "bank_transfer" => "Bank transfer",
            other => other,
        };
        let chosen = match self.selected_account() {
            Some(account) => account.to_string(),
            None => format!("<unresolved · {} candidates>", self.candidates.len()),
        };
        format!(
            "{kind_label} '{}' {} {}  →  {}",
            self.pattern, self.txn_date, self.txn_amount, chosen
        )
    }
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct ApplyImportResponse {
    pub(crate) status: String,
    pub(crate) error: Option<String>,
    pub(crate) summary: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ReviewPane {
    Items,
    Fields,
    Preview,
}

impl ReviewPane {
    pub(crate) fn next(self) -> Self {
        match self {
            ReviewPane::Items => ReviewPane::Fields,
            ReviewPane::Fields => ReviewPane::Preview,
            ReviewPane::Preview => ReviewPane::Items,
        }
    }

    pub(crate) fn previous(self) -> Self {
        match self {
            ReviewPane::Items => ReviewPane::Preview,
            ReviewPane::Fields => ReviewPane::Items,
            ReviewPane::Preview => ReviewPane::Fields,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ReviewTab {
    Effective,
    Diff,
    Raw,
}

impl ReviewTab {
    pub(crate) fn next(self) -> Self {
        match self {
            ReviewTab::Effective => ReviewTab::Diff,
            ReviewTab::Diff => ReviewTab::Raw,
            ReviewTab::Raw => ReviewTab::Effective,
        }
    }

    pub(crate) fn title(self) -> &'static str {
        match self {
            ReviewTab::Effective => "Effective Preview",
            ReviewTab::Diff => "Unsaved Diff",
            ReviewTab::Raw => "Raw Stage JSON",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ReceiptReviewField {
    Merchant,
    Date,
    Subtotal,
    Tax,
    Total,
    Notes,
}

impl ReceiptReviewField {
    pub(crate) fn label(self) -> &'static str {
        match self {
            ReceiptReviewField::Merchant => "Merchant",
            ReceiptReviewField::Date => "Date",
            ReceiptReviewField::Subtotal => "Subtotal",
            ReceiptReviewField::Tax => "Tax",
            ReceiptReviewField::Total => "Total",
            ReceiptReviewField::Notes => "Notes",
        }
    }

    pub(crate) fn key(self) -> &'static str {
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
pub(crate) struct ReceiptFieldState {
    pub(crate) field: ReceiptReviewField,
    pub(crate) original: String,
    pub(crate) value: String,
}

#[derive(Clone, Debug)]
pub(crate) struct ReviewItemState {
    pub(crate) id: String,
    pub(crate) is_new: bool,
    pub(crate) original_description: String,
    pub(crate) description: String,
    pub(crate) original_price: String,
    pub(crate) price: String,
    pub(crate) quantity: String,
    pub(crate) original_category: String,
    pub(crate) category: String,
    pub(crate) original_notes: String,
    pub(crate) notes: String,
    pub(crate) original_removed: bool,
    pub(crate) removed: bool,
}

impl ReviewItemState {
    pub(crate) fn from_document(item: &Value) -> Option<Self> {
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

    pub(crate) fn new_added(id: String) -> Self {
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

    pub(crate) fn has_meaningful_content(&self) -> bool {
        !self.description.trim().is_empty()
            || !self.price.trim().is_empty()
            || !self.category.trim().is_empty()
            || !self.notes.trim().is_empty()
    }
}

#[derive(Clone, Debug)]
pub(crate) enum ReviewEditTarget {
    ReceiptField(usize),
    ItemDescription(usize),
    ItemPrice(usize),
    ItemNotes(usize),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ItemEditorField {
    Description,
    Price,
    Category,
    Notes,
    Removed,
}

impl ItemEditorField {
    pub(crate) fn all() -> [Self; 5] {
        [
            ItemEditorField::Description,
            ItemEditorField::Price,
            ItemEditorField::Category,
            ItemEditorField::Notes,
            ItemEditorField::Removed,
        ]
    }

    pub(crate) fn label(self) -> &'static str {
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
pub(crate) struct ItemEditorState {
    pub(crate) item_index: usize,
    pub(crate) field_state: ListState,
}

impl ItemEditorState {
    pub(crate) fn new(item_index: usize) -> Self {
        let mut field_state = ListState::default();
        field_state.select(Some(0));
        Self {
            item_index,
            field_state,
        }
    }

    pub(crate) fn selected_field(&self) -> ItemEditorField {
        let fields = ItemEditorField::all();
        self.field_state
            .selected()
            .and_then(|index| fields.get(index).copied())
            .unwrap_or(ItemEditorField::Description)
    }

    pub(crate) fn move_selection(&mut self, delta: isize) {
        let len = ItemEditorField::all().len();
        let current = self.field_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.field_state.select(Some(next));
    }

    pub(crate) fn select_field(&mut self, field: ItemEditorField) {
        let index = ItemEditorField::all()
            .iter()
            .position(|candidate| *candidate == field)
            .unwrap_or(0);
        self.field_state.select(Some(index));
    }
}

#[derive(Clone, Debug)]
pub(crate) struct CategoryPickerState {
    pub(crate) item_index: usize,
    pub(crate) category_state: ListState,
}

impl CategoryPickerState {
    pub(crate) const PAGE_STEP: isize = 8;

    pub(crate) fn new(item_index: usize, selected_index: usize) -> Self {
        let mut category_state = ListState::default();
        category_state.select(Some(selected_index));
        Self {
            item_index,
            category_state,
        }
    }

    pub(crate) fn move_selection(&mut self, delta: isize, len: usize) {
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
pub(crate) struct TextInputState {
    pub(crate) target: ReviewEditTarget,
    pub(crate) label: String,
    pub(crate) value: String,
    pub(crate) cursor: usize,
}

impl TextInputState {
    pub(crate) fn with_value(target: ReviewEditTarget, label: String, value: String) -> Self {
        let cursor = value.chars().count();
        Self {
            target,
            label,
            value,
            cursor,
        }
    }

    pub(crate) fn move_left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }

    pub(crate) fn move_right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.value.chars().count());
    }

    pub(crate) fn move_home(&mut self) {
        self.cursor = 0;
    }

    pub(crate) fn move_end(&mut self) {
        self.cursor = self.value.chars().count();
    }

    pub(crate) fn insert_char(&mut self, ch: char) {
        let idx = char_to_byte_index(&self.value, self.cursor);
        self.value.insert(idx, ch);
        self.cursor += 1;
    }

    pub(crate) fn backspace(&mut self) {
        if self.cursor == 0 {
            return;
        }
        let end = char_to_byte_index(&self.value, self.cursor);
        let start = char_to_byte_index(&self.value, self.cursor - 1);
        self.value.replace_range(start..end, "");
        self.cursor -= 1;
    }

    pub(crate) fn delete(&mut self) {
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
pub(crate) struct DecisionPickerState {
    pub(crate) decision_index: usize,
    pub(crate) list_state: ListState,
}

impl DecisionPickerState {
    pub(crate) fn new(decision_index: usize, initial_selection: Option<usize>) -> Self {
        let mut list_state = ListState::default();
        list_state.select(initial_selection.or(Some(0)));
        Self {
            decision_index,
            list_state,
        }
    }

    pub(crate) fn move_selection(&mut self, delta: isize, len: usize) {
        if len == 0 {
            self.list_state.select(None);
            return;
        }
        let current = self.list_state.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, (len - 1) as isize) as usize;
        self.list_state.select(Some(next));
    }
}
