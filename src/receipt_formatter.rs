#[derive(Clone, Debug)]
pub(crate) struct FormatterItemInput {
    pub(crate) description: String,
    pub(crate) price: String,
    pub(crate) quantity: i32,
    pub(crate) posting_account: String,
}

#[derive(Clone, Debug)]
pub(crate) struct FormatterWarningInput {
    pub(crate) message: String,
    pub(crate) after_item_index: Option<usize>,
}

#[derive(Clone, Debug)]
pub(crate) struct FormatterReceiptInput {
    pub(crate) merchant: String,
    pub(crate) date_iso: String,
    pub(crate) date_is_placeholder: bool,
    pub(crate) total: String,
    pub(crate) tax: Option<String>,
    pub(crate) image_filename: String,
    pub(crate) raw_text: String,
    pub(crate) items: Vec<FormatterItemInput>,
    pub(crate) warnings: Vec<FormatterWarningInput>,
}

#[derive(Clone, Debug)]
pub(crate) struct EnrichedPostingInput {
    pub(crate) account: String,
    pub(crate) number: Option<String>,
    pub(crate) currency: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct EnrichedMatchInput {
    pub(crate) transaction_date_iso: String,
    pub(crate) payee: String,
    pub(crate) narration: String,
    pub(crate) postings: Vec<EnrichedPostingInput>,
    pub(crate) file_path: String,
    pub(crate) line_number: i32,
    pub(crate) confidence: f64,
    pub(crate) match_details: String,
}

fn decimal_to_cents(value: &str) -> i64 {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return 0;
    }

    let negative = trimmed.starts_with('-');
    let unsigned = trimmed.trim_start_matches('-');
    let mut parts = unsigned.splitn(2, '.');
    let whole = parts.next().unwrap_or("0").parse::<i64>().unwrap_or(0);
    let frac_raw = parts.next().unwrap_or("0");
    let mut frac = frac_raw.chars().take(2).collect::<String>();
    while frac.len() < 2 {
        frac.push('0');
    }
    let frac_value = frac.parse::<i64>().unwrap_or(0);
    let value = whole * 100 + frac_value;
    if negative {
        -value
    } else {
        value
    }
}

fn cents_to_fixed(value: i64) -> String {
    let sign = if value < 0 { "-" } else { "" };
    let abs = value.abs();
    format!("{sign}{}.{:02}", abs / 100, abs % 100)
}

fn format_postings_aligned(
    postings: &[(String, String, Option<String>)],
    indent: &str,
) -> Vec<String> {
    if postings.is_empty() {
        return Vec::new();
    }

    let max_account_len = postings
        .iter()
        .map(|(account, _, _)| account.len())
        .max()
        .unwrap_or(0);
    let max_amount_len = postings
        .iter()
        .map(|(_, amount, _)| amount.len())
        .max()
        .unwrap_or(0);

    postings
        .iter()
        .map(|(account, amount, comment)| {
            let base = format!(
                "{indent}{account:<account_width$}  {amount:>amount_width$}",
                account_width = max_account_len,
                amount_width = max_amount_len,
            );
            match comment {
                Some(comment) if !comment.is_empty() => format!("{base}  ; {comment}"),
                _ => base,
            }
        })
        .collect()
}

fn extract_card_last4(raw_text: &str) -> Option<String> {
    for line in raw_text.lines() {
        if !line.contains('*') {
            continue;
        }
        let mut star_run = 0usize;
        let chars: Vec<char> = line.chars().collect();
        let mut idx = 0usize;
        while idx < chars.len() {
            if chars[idx] == '*' {
                star_run += 1;
                idx += 1;
                continue;
            }
            if star_run >= 2 {
                while idx < chars.len() && chars[idx].is_whitespace() {
                    idx += 1;
                }
                if idx + 4 <= chars.len() {
                    let candidate: String = chars[idx..idx + 4].iter().collect();
                    if candidate.chars().all(|ch| ch.is_ascii_digit()) {
                        let boundary_ok =
                            idx + 4 == chars.len() || !chars[idx + 4].is_ascii_digit();
                        if boundary_ok {
                            return Some(candidate);
                        }
                    }
                }
            }
            star_run = 0;
            idx += 1;
        }
    }
    None
}

fn build_posting_warning_map(
    warnings: &[FormatterWarningInput],
    item_posting_indexes: &[usize],
) -> Vec<(usize, String)> {
    let mut mapped = Vec::new();
    for warning in warnings {
        if warning.message.is_empty() {
            continue;
        }
        let posting_idx = if item_posting_indexes.is_empty() {
            0
        } else {
            let target_item_idx = match warning.after_item_index {
                Some(index) => index.min(item_posting_indexes.len().saturating_sub(1)),
                None => item_posting_indexes.len().saturating_sub(1),
            };
            item_posting_indexes[target_item_idx]
        };
        mapped.push((posting_idx, warning.message.clone()));
    }
    mapped
}

fn inject_posting_warnings(
    formatted_postings: Vec<String>,
    posting_warnings: Vec<(usize, String)>,
) -> Vec<String> {
    if posting_warnings.is_empty() {
        return formatted_postings;
    }

    let mut output = Vec::new();
    for (idx, posting_line) in formatted_postings.into_iter().enumerate() {
        output.push(posting_line);
        for (warning_idx, message) in posting_warnings
            .iter()
            .filter(|(warning_idx, _)| *warning_idx == idx)
        {
            let _ = warning_idx;
            output.push(format!("; WARN:PARSER {message}"));
        }
    }
    output
}

pub(crate) fn format_parsed_receipt(
    receipt: &FormatterReceiptInput,
    credit_card_account: &str,
    image_sha256: Option<&str>,
) -> String {
    let total_cents = decimal_to_cents(&receipt.total);
    let tax_cents = receipt.tax.as_deref().map(decimal_to_cents);
    let mut lines = Vec::new();

    lines.push("; === PARSED RECEIPT - AWAITING CC MATCH ===".to_string());
    lines.push(format!("; @merchant: {}", receipt.merchant));
    if receipt.date_is_placeholder {
        lines.push("; @date: UNKNOWN".to_string());
        lines.push(format!(
            "; FIXME: unknown date (placeholder used: {})",
            receipt.date_iso
        ));
    } else {
        lines.push(format!("; @date: {}", receipt.date_iso));
    }
    lines.push(format!("; @total: {}", cents_to_fixed(total_cents)));
    lines.push(format!("; @items: {}", receipt.items.len()));
    if let Some(tax_cents) = tax_cents {
        if tax_cents != 0 {
            lines.push(format!("; @tax: {}", cents_to_fixed(tax_cents)));
        }
    }
    if !receipt.image_filename.is_empty() {
        lines.push(format!("; @image: {}", receipt.image_filename));
        lines.push(format!("; @image_filename: {}", receipt.image_filename));
    }
    if let Some(image_sha256) = image_sha256.filter(|value| !value.is_empty()) {
        lines.push(format!("; @image_sha256: {image_sha256}"));
    }
    lines.push(String::new());

    let merchant_clean = receipt.merchant.replace('"', "'");
    lines.push(format!(
        r#"{} * "{}" "Receipt scan""#,
        receipt.date_iso, merchant_clean
    ));

    let total_str = cents_to_fixed(-total_cents);
    let card_comment =
        extract_card_last4(&receipt.raw_text).map(|last4| format!("card ****{last4}"));
    let mut postings = vec![(
        credit_card_account.to_string(),
        format!("{total_str} CAD"),
        card_comment,
    )];

    let mut item_total_cents = 0i64;
    let mut item_posting_indexes = Vec::new();
    for item in &receipt.items {
        item_posting_indexes.push(postings.len());
        let desc_clean = item.description.replace('"', "'");
        let comment = if item.quantity > 1 {
            Some(format!("{desc_clean} (qty {})", item.quantity))
        } else {
            Some(desc_clean)
        };
        postings.push((
            item.posting_account.clone(),
            format!("{} CAD", cents_to_fixed(decimal_to_cents(&item.price))),
            comment,
        ));
        item_total_cents += decimal_to_cents(&item.price);
    }

    if let Some(tax_cents) = tax_cents {
        if tax_cents != 0 {
            postings.push((
                "Expenses:Tax:HST".to_string(),
                format!("{} CAD", cents_to_fixed(tax_cents)),
                None,
            ));
            item_total_cents += tax_cents;
        }
    }

    if total_cents > 0 && item_total_cents != total_cents {
        let diff = total_cents - item_total_cents;
        if diff > 0 {
            postings.push((
                "Expenses:FIXME".to_string(),
                format!("{} CAD", cents_to_fixed(diff)),
                Some("FIXME: unaccounted amount".to_string()),
            ));
        }
    }

    let formatted_postings = format_postings_aligned(&postings, "  ");
    let posting_warnings = build_posting_warning_map(&receipt.warnings, &item_posting_indexes);
    lines.extend(inject_posting_warnings(
        formatted_postings,
        posting_warnings,
    ));

    if !receipt.raw_text.is_empty() {
        lines.push(String::new());
        lines.push("; --- Raw OCR Text (for reference) ---".to_string());
        for ocr_line in receipt.raw_text.lines() {
            if !ocr_line.trim().is_empty() {
                lines.push(format!("; {ocr_line}"));
            }
        }
    }

    lines.push(String::new());
    lines.join("\n")
}

pub(crate) fn format_draft_beancount(
    receipt: &FormatterReceiptInput,
    credit_card_account: &str,
) -> String {
    let total_cents = decimal_to_cents(&receipt.total);
    let tax_cents = receipt.tax.as_deref().map(decimal_to_cents);
    let mut lines = Vec::new();

    lines.push("; === DRAFT - REVIEW NEEDED ===".to_string());
    lines.push(format!("; Source: {}", receipt.image_filename));
    lines.push("; Generated from OCR - please verify all values".to_string());
    lines.push(String::new());

    if receipt.date_is_placeholder {
        lines.push(format!(
            "; FIXME: unknown date (placeholder used: {})",
            receipt.date_iso
        ));
    }
    let merchant_clean = receipt.merchant.replace('"', "'");
    lines.push(format!(
        r#"{} * "{}" "FIXME: add description""#,
        receipt.date_iso, merchant_clean
    ));

    let total_str = cents_to_fixed(-total_cents);
    let card_comment =
        extract_card_last4(&receipt.raw_text).map(|last4| format!("card ****{last4}"));
    let mut postings = vec![(
        credit_card_account.to_string(),
        format!("{total_str} CAD"),
        card_comment,
    )];

    let mut item_total_cents = 0i64;
    let mut item_posting_indexes = Vec::new();
    for item in &receipt.items {
        item_posting_indexes.push(postings.len());
        let desc_clean = item.description.replace('"', "'");
        let comment = if item.quantity > 1 {
            Some(format!("{desc_clean} (qty {})", item.quantity))
        } else {
            Some(desc_clean)
        };
        postings.push((
            item.posting_account.clone(),
            format!("{} CAD", cents_to_fixed(decimal_to_cents(&item.price))),
            comment,
        ));
        item_total_cents += decimal_to_cents(&item.price);
    }

    if let Some(tax_cents) = tax_cents {
        if tax_cents != 0 {
            postings.push((
                "Expenses:Tax:HST".to_string(),
                format!("{} CAD", cents_to_fixed(tax_cents)),
                None,
            ));
            item_total_cents += tax_cents;
        }
    }

    if total_cents > 0 && item_total_cents != total_cents {
        let diff = total_cents - item_total_cents;
        if diff > 0 {
            postings.push((
                "Expenses:FIXME".to_string(),
                format!("{} CAD", cents_to_fixed(diff)),
                Some("FIXME: unaccounted amount".to_string()),
            ));
        } else if diff < 0 {
            lines.push(format!(
                "  ; WARNING: items total ({}) exceeds receipt total ({})",
                cents_to_fixed(item_total_cents),
                cents_to_fixed(total_cents)
            ));
        }
    }

    let formatted_postings = format_postings_aligned(&postings, "  ");
    let posting_warnings = build_posting_warning_map(&receipt.warnings, &item_posting_indexes);
    lines.extend(inject_posting_warnings(
        formatted_postings,
        posting_warnings,
    ));
    lines.push(String::new());
    lines.push("; --- Raw OCR Text (for reference) ---".to_string());
    for ocr_line in receipt.raw_text.lines() {
        if !ocr_line.trim().is_empty() {
            lines.push(format!("; {ocr_line}"));
        }
    }

    lines.join("\n")
}

pub(crate) fn generate_filename(
    date_iso: &str,
    date_is_placeholder: bool,
    merchant: &str,
) -> String {
    let date_str = if date_is_placeholder {
        "unknown-date"
    } else {
        date_iso
    };

    let mut merchant_clean = String::new();
    let mut previous_dash = false;
    for ch in merchant.to_ascii_lowercase().chars() {
        let normalized = if ch.is_ascii_alphanumeric() { ch } else { '-' };
        if normalized == '-' {
            if previous_dash {
                continue;
            }
            previous_dash = true;
        } else {
            previous_dash = false;
        }
        merchant_clean.push(normalized);
    }
    merchant_clean = merchant_clean.trim_matches('-').to_string();
    if merchant_clean.is_empty() {
        merchant_clean = "unknown".to_string();
    }

    format!("{date_str}-{merchant_clean}.beancount")
}

pub(crate) fn format_enriched_transaction(
    receipt: &FormatterReceiptInput,
    match_input: &EnrichedMatchInput,
    default_expense: &str,
) -> String {
    let receipt_total_cents = decimal_to_cents(&receipt.total);
    let tax_cents = receipt.tax.as_deref().map(decimal_to_cents);
    let mut lines = Vec::new();

    lines.push("; === ENRICHED TRANSACTION - REVIEW NEEDED ===".to_string());
    lines.push(format!("; Receipt: {}", receipt.image_filename));
    lines.push(format!(
        "; Matched: {}:{}",
        match_input.file_path, match_input.line_number
    ));
    lines.push(format!(
        "; Confidence: {:.0}% ({})",
        match_input.confidence * 100.0,
        match_input.match_details
    ));
    lines.push(String::new());

    let payee_clean = match_input.payee.replace('"', "'");
    let narration_clean = match_input.narration.replace('"', "'");
    lines.push(format!(
        r#"{} * "{}" "{}""#,
        match_input.transaction_date_iso, payee_clean, narration_clean
    ));

    let mut cc_account: Option<String> = None;
    let mut cc_amount_cents: Option<i64> = None;
    let mut original_expense: Option<String> = None;

    for posting in &match_input.postings {
        let Some(number) = posting.number.as_deref() else {
            continue;
        };
        let number_cents = decimal_to_cents(number);
        if number_cents < 0 {
            cc_account = Some(posting.account.clone());
            cc_amount_cents = Some(number_cents);
        } else if number_cents > 0 {
            original_expense = Some(posting.account.clone());
        }
    }

    let expense_base = original_expense.unwrap_or_else(|| default_expense.to_string());
    let mut postings = Vec::new();
    if let (Some(cc_account), Some(cc_amount_cents)) = (cc_account.clone(), cc_amount_cents) {
        postings.push((
            cc_account,
            format!("{} CAD", cents_to_fixed(cc_amount_cents)),
            None,
        ));
    } else {
        postings.push((
            "Liabilities:CreditCard:FIXME".to_string(),
            format!("{} CAD", cents_to_fixed(-receipt_total_cents)),
            None,
        ));
    }

    let mut items_total_cents = 0i64;
    for item in &receipt.items {
        let desc_clean = item.description.replace('"', "'");
        let comment = if item.quantity > 1 {
            Some(format!("{desc_clean} (qty {})", item.quantity))
        } else {
            Some(desc_clean)
        };
        postings.push((
            item.posting_account.clone(),
            format!("{} CAD", cents_to_fixed(decimal_to_cents(&item.price))),
            comment,
        ));
        items_total_cents += decimal_to_cents(&item.price);
    }

    if let Some(tax_cents) = tax_cents {
        if tax_cents != 0 {
            postings.push((
                "Expenses:Tax:HST".to_string(),
                format!("{} CAD", cents_to_fixed(tax_cents)),
                None,
            ));
            items_total_cents += tax_cents;
        }
    }

    let expected_total_cents = cc_amount_cents
        .map(|value| value.abs())
        .unwrap_or(receipt_total_cents);
    if expected_total_cents > 0 && items_total_cents != expected_total_cents {
        let diff = expected_total_cents - items_total_cents;
        if diff > 1 {
            postings.push((
                expense_base.clone(),
                format!("{} CAD", cents_to_fixed(diff)),
                Some("remaining/unitemized".to_string()),
            ));
        } else if diff < -1 {
            lines.push(format!(
                "  ; WARNING: items total ({}) exceeds transaction ({})",
                cents_to_fixed(items_total_cents),
                cents_to_fixed(expected_total_cents)
            ));
        }
    }

    lines.extend(format_postings_aligned(&postings, "  "));
    lines.push(String::new());
    lines.push("; --- Original Transaction (to be replaced) ---".to_string());
    lines.push(format!(
        r#"; {} * "{}" "{}""#,
        match_input.transaction_date_iso, payee_clean, narration_clean
    ));
    for posting in &match_input.postings {
        if let (Some(number), Some(currency)) = (&posting.number, &posting.currency) {
            lines.push(format!(";   {}  {} {}", posting.account, number, currency));
        }
    }

    lines.join("\n")
}
