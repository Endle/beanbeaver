//! Single high-level entry point for the on-device pipeline:
//! raw OCR detections -> structured receipt + beancount text, with no Python.
//!
//! Chains `ocr_transform::transform` -> `receipt_parser::parse_receipt` ->
//! `receipt_formatter::format_parsed_receipt`, loading rules from the bundled
//! defaults. This mirrors the desktop flow
//! (`ocr_helpers.transform_paddleocr_result` + `ocr_result_parser.parse_receipt`
//! + `formatter.format_parsed_receipt`).

use crate::ocr_transform::{transform, RawDetection};
use crate::receipt_categories::resolve_account_target;
use crate::receipt_formatter::{
    format_parsed_receipt, FormatterItemInput, FormatterReceiptInput, FormatterTenderInput,
    FormatterWarningInput,
};
use crate::receipt_parser::{parse_receipt, ParsedReceiptData, ParserRuleLayers};
use crate::rules::{default_known_merchants, default_parser_rule_layers};

const DEFAULT_ITEM_ACCOUNT: &str = "Expenses:FIXME";

/// Result of the full pipeline: the structured parse plus the rendered beancount.
#[derive(Clone, Debug)]
pub struct ProcessedReceipt {
    pub parsed: ParsedReceiptData,
    pub beancount: String,
}

/// Round a decimal string to 2 places using banker's rounding (round-half-even),
/// matching Python's `Decimal.__format__(".2f")` that the formatter glue applies
/// to item prices, total, and tax. Inputs are well-formed fixed-point strings
/// (e.g. "12.34" from cents, "1.2345" from the scaled spatial path).
fn to_fixed_2(value: &str) -> String {
    let negative = value.starts_with('-');
    let digits = value.trim_start_matches('-');
    let (int_part, frac_part) = match digits.split_once('.') {
        Some((i, f)) => (i, f),
        None => (digits, ""),
    };

    // Build an integer at the source scale, then round to scale 2.
    let int_value: i128 = int_part.parse().unwrap_or(0);
    let scale = frac_part.len();
    let frac_value: i128 = if frac_part.is_empty() {
        0
    } else {
        frac_part.parse().unwrap_or(0)
    };
    let scale_pow = 10_i128.pow(scale as u32);
    let total = int_value * scale_pow + frac_value;

    let rounded_hundredths: i128 = if scale <= 2 {
        total * 10_i128.pow((2 - scale) as u32)
    } else {
        let divisor = 10_i128.pow((scale - 2) as u32);
        let q = total / divisor;
        let r = total % divisor;
        let half = divisor / 2;
        if r > half || (r == half && q % 2 != 0) {
            q + 1
        } else {
            q
        }
    };

    let sign = if negative && rounded_hundredths != 0 { "-" } else { "" };
    format!("{sign}{}.{:02}", rounded_hundredths / 100, rounded_hundredths % 100)
}

fn date_iso(parsed: &ParsedReceiptData, today: (i32, u32, u32)) -> String {
    match parsed.date {
        Some((y, m, d)) => format!("{y:04}-{m:02}-{d:02}"),
        // Placeholder mirrors `date_utils.placeholder_receipt_date()`:
        // first day of the current (reference) month.
        None => format!("{:04}-{:02}-01", today.0, today.1),
    }
}

/// Run the full pipeline. `today` is the reference date (year, month, day) used
/// for date inference and the placeholder date. When `known_merchants` is `None`,
/// the bundled default merchant keywords are used.
#[allow(clippy::too_many_arguments)]
pub fn process_receipt(
    detections: Vec<RawDetection>,
    padded_width: i64,
    padded_height: i64,
    padding: i64,
    image_filename: &str,
    known_merchants: Option<Vec<String>>,
    today: (i32, u32, u32),
    credit_card_account: &str,
    image_sha256: Option<&str>,
) -> ProcessedReceipt {
    let rule_layers: ParserRuleLayers = default_parser_rule_layers();
    let merchants = known_merchants.unwrap_or_else(default_known_merchants);

    let ocr = transform(detections, padded_width, padded_height, padding);

    let parsed = parse_receipt(
        &ocr.full_text,
        &ocr.helper_pages,
        &ocr.spatial_pages,
        &rule_layers,
        image_filename,
        &merchants,
        today.0,
    );

    let item_accounts: Vec<String> = parsed
        .items
        .iter()
        .map(|item| {
            resolve_account_target(
                item.category.as_deref(),
                &rule_layers.category_rules.account_mapping,
                Some(DEFAULT_ITEM_ACCOUNT),
            )
            .unwrap_or_else(|| DEFAULT_ITEM_ACCOUNT.to_string())
        })
        .collect();

    let formatter_input = FormatterReceiptInput {
        merchant: parsed.merchant.clone(),
        date_iso: date_iso(&parsed, today),
        date_is_placeholder: parsed.date_is_placeholder,
        total: to_fixed_2(&parsed.total),
        tax: parsed.tax.as_deref().map(to_fixed_2),
        image_filename: parsed.image_filename.clone(),
        raw_text: parsed.raw_text.clone(),
        items: parsed
            .items
            .iter()
            .zip(&item_accounts)
            .map(|(item, account)| FormatterItemInput {
                description: item.description.clone(),
                price: to_fixed_2(&item.price),
                quantity: item.quantity,
                posting_account: account.clone(),
            })
            .collect(),
        warnings: parsed
            .warnings
            .iter()
            .map(|warning| FormatterWarningInput {
                message: warning.message.clone(),
                after_item_index: warning.after_item_index,
            })
            .collect(),
        tenders: parsed
            .tenders
            .iter()
            .map(|tender| FormatterTenderInput {
                amount: to_fixed_2(&tender.amount),
                account: tender.account.clone(),
                kind: tender.kind.clone(),
            })
            .collect(),
    };

    let beancount = format_parsed_receipt(&formatter_input, credit_card_account, image_sha256);

    ProcessedReceipt { parsed, beancount }
}

#[cfg(test)]
mod tests {
    use super::to_fixed_2;

    #[test]
    fn rounds_half_even_like_python_decimal() {
        assert_eq!(to_fixed_2("12.34"), "12.34");
        assert_eq!(to_fixed_2("1.2345"), "1.23"); // 4->5 at third place rounds down to even
        assert_eq!(to_fixed_2("1.2355"), "1.24"); // half rounds to even (4)
        assert_eq!(to_fixed_2("1.2350"), "1.24"); // exactly half -> even
        assert_eq!(to_fixed_2("1.2250"), "1.22"); // exactly half -> even
        assert_eq!(to_fixed_2("0.005"), "0.00"); // half -> even (0)
        assert_eq!(to_fixed_2("-5.00"), "-5.00");
        assert_eq!(to_fixed_2("3"), "3.00");
    }
}
