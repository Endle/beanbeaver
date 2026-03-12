#[derive(Clone, Debug)]
pub(crate) struct MatchCandidateRef {
    pub(crate) file_path: String,
    pub(crate) line_number: i32,
}

#[derive(Clone, Debug)]
pub(crate) struct MatchCandidate {
    pub(crate) candidate_ref: MatchCandidateRef,
    pub(crate) confidence: f64,
    pub(crate) display: String,
    pub(crate) payee: Option<String>,
    pub(crate) narration: Option<String>,
    pub(crate) date_iso: String,
    pub(crate) amount: Option<String>,
    pub(crate) details: String,
    pub(crate) strength: String,
}

#[derive(Clone, Debug)]
pub(crate) struct ReceiptMatchPlan {
    pub(crate) receipt_path: String,
    pub(crate) ledger_path: String,
    pub(crate) candidates: Vec<MatchCandidate>,
    pub(crate) errors: Vec<String>,
    pub(crate) warning: Option<String>,
    pub(crate) used_relaxed_threshold: bool,
}

#[derive(Clone, Debug)]
pub(crate) struct ApplyMatchResult {
    pub(crate) status: String,
    pub(crate) ledger_path: String,
    pub(crate) matched_receipt_path: Option<String>,
    pub(crate) enriched_path: Option<String>,
    pub(crate) message: Option<String>,
}
