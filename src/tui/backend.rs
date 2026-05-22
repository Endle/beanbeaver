use super::*;

pub(crate) fn run_backend(args: &[&str]) -> AppResult<String> {
    run_backend_with_input(args, None)
}

pub(crate) fn run_backend_with_input(
    args: &[&str],
    stdin_input: Option<&str>,
) -> AppResult<String> {
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

pub(crate) fn backend_list_receipts(queue: Queue) -> AppResult<Vec<ReceiptSummary>> {
    let stdout = run_backend(&["api", queue.api_list_command()])?;
    let response: ReceiptsResponse = serde_json::from_str(&stdout)?;
    Ok(response.receipts)
}

pub(crate) fn backend_show_receipt(path: &str) -> AppResult<ShowReceiptResponse> {
    let stdout = run_backend(&["api", "show-receipt", path])?;
    Ok(serde_json::from_str(&stdout)?)
}

pub(crate) fn backend_list_item_categories() -> AppResult<Vec<CategoryOption>> {
    let stdout = run_backend(&["api", "list-item-categories"])?;
    let response: CategoryListResponse = serde_json::from_str(&stdout)?;
    Ok(response.categories)
}

pub(crate) fn backend_approve_scanned(path: &str) -> AppResult<ApproveReceiptResponse> {
    let stdout = run_backend(&["api", "approve-scanned", path])?;
    let response: ApproveReceiptResponse = serde_json::from_str(&stdout)?;
    if response.status != "approved" {
        return Err(format!("unexpected approve status: {}", response.status).into());
    }
    Ok(response)
}

pub(crate) fn backend_approve_scanned_with_review(
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

pub(crate) fn backend_re_edit_approved_with_review(
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

pub(crate) fn backend_get_config() -> AppResult<ConfigResponse> {
    let stdout = run_backend(&["api", "get-config"])?;
    Ok(serde_json::from_str(&stdout)?)
}

pub(crate) fn backend_refresh_import_page(
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

pub(crate) fn backend_resolve_import_accounts(
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

pub(crate) fn backend_apply_import(
    import_type: &str,
    csv_file: &str,
    importer_id: &str,
    selected_account: Option<&str>,
    allow_uncommitted: bool,
    cc_payment_overrides: &[ImportOverridePayload],
    bank_transfer_overrides: &[ImportOverridePayload],
) -> AppResult<ApplyImportResponse> {
    let payload = serde_json::json!({
        "import_type": import_type,
        "csv_file": csv_file,
        "importer_id": importer_id,
        "selected_account": selected_account,
        "allow_uncommitted": allow_uncommitted,
        "cc_payment_overrides": cc_payment_overrides,
        "bank_transfer_overrides": bank_transfer_overrides,
    });
    let stdout = run_backend_with_input(
        &["api", "import-apply"],
        Some(&serde_json::to_string(&payload)?),
    )?;
    Ok(serde_json::from_str(&stdout)?)
}

pub(crate) fn backend_preflight_chequing_import(
    csv_file: &str,
    selected_account: Option<&str>,
) -> AppResult<PreflightChequingImportResponse> {
    let payload = serde_json::json!({
        "csv_file": csv_file,
        "selected_account": selected_account,
    });
    let stdout = run_backend_with_input(
        &["api", "preflight-chequing-import"],
        Some(&serde_json::to_string(&payload)?),
    )?;
    Ok(serde_json::from_str(&stdout)?)
}

#[derive(Clone, Debug, serde::Serialize)]
pub(crate) struct ImportOverridePayload {
    pub(crate) date: String,
    pub(crate) description: String,
    pub(crate) amount: String,
    pub(crate) account: String,
}

pub(crate) fn backend_set_config(project_root: &str) -> AppResult<ConfigResponse> {
    let payload = serde_json::json!({
        "project_root": project_root,
    });
    let stdout = run_backend_with_input(
        &["api", "set-config"],
        Some(&serde_json::to_string(&payload)?),
    )?;
    Ok(serde_json::from_str(&stdout)?)
}

pub(crate) fn backend_match_candidates(path: &str) -> AppResult<MatchCandidatesResponse> {
    let stdout = run_backend(&["api", "match-candidates", path])?;
    Ok(serde_json::from_str(&stdout)?)
}

pub(crate) fn backend_apply_match(
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
