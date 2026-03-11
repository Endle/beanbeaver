use crate::receipt_categories;

const SCHEMA_VERSION: &str = "1";
const STAGE_PARSED: &str = "parsed";

#[derive(Clone, Debug)]
pub(crate) struct StageRuleLayers {
    pub(crate) category_rules: receipt_categories::CategoryRuleLayers,
    pub(crate) account_mapping: Vec<(String, String)>,
}

#[derive(Clone, Debug)]
pub(crate) struct ReceiptItemInput {
    pub(crate) description: String,
    pub(crate) price: Option<String>,
    pub(crate) quantity: i32,
    pub(crate) category: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct ReceiptWarningInput {
    pub(crate) message: String,
    pub(crate) after_item_index: Option<usize>,
}

#[derive(Clone, Debug)]
pub(crate) struct ReceiptInput {
    pub(crate) merchant: String,
    pub(crate) date_iso: String,
    pub(crate) total: String,
    pub(crate) date_is_placeholder: bool,
    pub(crate) items: Vec<ReceiptItemInput>,
    pub(crate) tax: Option<String>,
    pub(crate) subtotal: Option<String>,
    pub(crate) raw_text: String,
    pub(crate) image_filename: String,
    pub(crate) warnings: Vec<ReceiptWarningInput>,
}

#[derive(Clone, Debug)]
pub(crate) struct ClassificationData {
    pub(crate) category: Option<String>,
    pub(crate) tags: Vec<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct StructuredWarning {
    pub(crate) message: String,
    pub(crate) source: String,
    pub(crate) stage: String,
}

#[derive(Clone, Debug)]
pub(crate) struct BuiltStageItem {
    pub(crate) id: String,
    pub(crate) description: String,
    pub(crate) price: Option<String>,
    pub(crate) quantity: i32,
    pub(crate) classification: Option<ClassificationData>,
    pub(crate) warnings: Vec<StructuredWarning>,
    pub(crate) source: String,
}

#[derive(Clone, Debug)]
pub(crate) struct BuiltStageMeta {
    pub(crate) schema_version: String,
    pub(crate) receipt_id: String,
    pub(crate) stage: String,
    pub(crate) stage_index: i32,
    pub(crate) created_at: String,
    pub(crate) created_by: String,
    pub(crate) pass_name: String,
    pub(crate) image_filename: Option<String>,
    pub(crate) image_sha256: Option<String>,
    pub(crate) ocr_json_path: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct BuiltStageReceipt {
    pub(crate) merchant: Option<String>,
    pub(crate) date: Option<String>,
    pub(crate) currency: String,
    pub(crate) subtotal: Option<String>,
    pub(crate) tax: Option<String>,
    pub(crate) total: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct BuiltStageDocument {
    pub(crate) meta: BuiltStageMeta,
    pub(crate) receipt: BuiltStageReceipt,
    pub(crate) items: Vec<BuiltStageItem>,
    pub(crate) warnings: Vec<StructuredWarning>,
    pub(crate) raw_text: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct StageDocumentItemInput {
    pub(crate) removed: bool,
    pub(crate) description: Option<String>,
    pub(crate) price: Option<String>,
    pub(crate) quantity: Option<i32>,
    pub(crate) classification: Option<ClassificationData>,
    pub(crate) warning_messages: Vec<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct StageDocumentInput {
    pub(crate) merchant: Option<String>,
    pub(crate) date_iso: Option<String>,
    pub(crate) total: Option<String>,
    pub(crate) tax: Option<String>,
    pub(crate) subtotal: Option<String>,
    pub(crate) raw_text: String,
    pub(crate) image_filename: String,
    pub(crate) items: Vec<StageDocumentItemInput>,
    pub(crate) top_level_warning_messages: Vec<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct ResolvedReceiptItem {
    pub(crate) description: String,
    pub(crate) price: String,
    pub(crate) quantity: i32,
    pub(crate) category: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct ResolvedReceiptWarning {
    pub(crate) message: String,
    pub(crate) after_item_index: Option<usize>,
}

#[derive(Clone, Debug)]
pub(crate) struct ResolvedReceiptData {
    pub(crate) merchant: String,
    pub(crate) date_iso: Option<String>,
    pub(crate) date_is_placeholder: bool,
    pub(crate) total: String,
    pub(crate) tax: Option<String>,
    pub(crate) subtotal: Option<String>,
    pub(crate) raw_text: String,
    pub(crate) image_filename: String,
    pub(crate) items: Vec<ResolvedReceiptItem>,
    pub(crate) warnings: Vec<ResolvedReceiptWarning>,
}

fn legacy_account_alias(target: &str) -> Option<&'static str> {
    match target {
        "Expenses:Food:Grocery:IceCream" => Some("Expenses:Food:Grocery:Frozen:IceCream"),
        _ => None,
    }
}

fn normalize_legacy_account_target(target: &str) -> String {
    legacy_account_alias(target).unwrap_or(target).to_string()
}

fn make_warning(message: &str, source: &str, stage: &str) -> StructuredWarning {
    StructuredWarning {
        message: message.to_string(),
        source: source.to_string(),
        stage: stage.to_string(),
    }
}

fn semantic_category_from_legacy_target(
    target: Option<&str>,
    rule_layers: &StageRuleLayers,
) -> Option<String> {
    let cleaned = target.map(str::trim).filter(|value| !value.is_empty())?;
    if rule_layers
        .account_mapping
        .iter()
        .any(|(key, _)| key == cleaned)
    {
        return Some(cleaned.to_string());
    }
    for (key, account) in &rule_layers.account_mapping {
        if account == cleaned {
            return Some(key.clone());
        }
    }
    None
}

fn resolve_account_target(
    target: Option<&str>,
    rule_layers: &StageRuleLayers,
    default: Option<&str>,
) -> Option<String> {
    match target {
        None => default.map(str::to_string),
        Some(raw) => {
            let cleaned = raw.trim();
            if cleaned.is_empty() {
                return default.map(str::to_string);
            }
            if cleaned.starts_with("Expenses:") {
                return Some(normalize_legacy_account_target(cleaned));
            }
            for (key, mapped) in &rule_layers.account_mapping {
                if key == cleaned {
                    return Some(normalize_legacy_account_target(mapped));
                }
            }
            default.map(str::to_string)
        }
    }
}

pub(crate) fn classify_item_semantic(
    description: &str,
    rule_layers: &StageRuleLayers,
    default_category: Option<String>,
) -> Option<ClassificationData> {
    let category = receipt_categories::classify_item_key(
        description,
        &rule_layers.category_rules,
        default_category,
    );
    let tags = receipt_categories::classify_item_tags(description, &rule_layers.category_rules);
    if category.is_none() && tags.is_empty() {
        return None;
    }
    Some(ClassificationData { category, tags })
}

pub(crate) fn build_parsed_receipt_stage(
    receipt: &ReceiptInput,
    rule_layers: &StageRuleLayers,
    receipt_id: &str,
    created_at: &str,
    ocr_json_path: Option<String>,
    image_sha256: Option<String>,
    created_by: &str,
    pass_name: &str,
) -> BuiltStageDocument {
    let mut item_docs = Vec::with_capacity(receipt.items.len());
    let mut top_level_warnings = Vec::new();

    for (idx, item) in receipt.items.iter().enumerate() {
        let semantic_category =
            semantic_category_from_legacy_target(item.category.as_deref(), rule_layers);
        item_docs.push(BuiltStageItem {
            id: format!("item-{:04}", idx + 1),
            description: item.description.clone(),
            price: item.price.clone(),
            quantity: item.quantity,
            classification: classify_item_semantic(
                &item.description,
                rule_layers,
                semantic_category,
            ),
            warnings: Vec::new(),
            source: "parser".to_string(),
        });
    }

    for warning in &receipt.warnings {
        let structured = make_warning(&warning.message, "parser", STAGE_PARSED);
        if let Some(index) = warning.after_item_index {
            if index < item_docs.len() {
                item_docs[index].warnings.push(structured);
                continue;
            }
        }
        top_level_warnings.push(structured);
    }

    BuiltStageDocument {
        meta: BuiltStageMeta {
            schema_version: SCHEMA_VERSION.to_string(),
            receipt_id: receipt_id.to_string(),
            stage: STAGE_PARSED.to_string(),
            stage_index: 0,
            created_at: created_at.to_string(),
            created_by: created_by.to_string(),
            pass_name: pass_name.to_string(),
            image_filename: (!receipt.image_filename.is_empty())
                .then(|| receipt.image_filename.clone()),
            image_sha256,
            ocr_json_path,
        },
        receipt: BuiltStageReceipt {
            merchant: (!receipt.merchant.is_empty()).then(|| receipt.merchant.clone()),
            date: if receipt.date_is_placeholder {
                None
            } else {
                Some(receipt.date_iso.clone())
            },
            currency: "CAD".to_string(),
            subtotal: receipt.subtotal.clone(),
            tax: receipt.tax.clone(),
            total: Some(receipt.total.clone()),
        },
        items: item_docs,
        warnings: top_level_warnings,
        raw_text: (!receipt.raw_text.is_empty()).then(|| receipt.raw_text.clone()),
    }
}

pub(crate) fn get_stage_summary(
    document: &StageDocumentInput,
) -> (Option<String>, Option<String>, Option<String>) {
    (
        document.merchant.clone(),
        document.date_iso.clone(),
        document.total.clone(),
    )
}

pub(crate) fn account_from_classification(
    classification: Option<&ClassificationData>,
    rule_layers: &StageRuleLayers,
) -> Option<String> {
    let classification = classification?;

    if let Some(category) = classification.category.as_deref() {
        if let Some(mapped) = resolve_account_target(Some(category), rule_layers, None) {
            return Some(mapped);
        }
    }

    for tag in &classification.tags {
        if tag.is_empty() {
            continue;
        }
        for (key, mapped) in &rule_layers.account_mapping {
            if key.split('_').any(|part| part == tag) {
                return Some(normalize_legacy_account_target(mapped));
            }
        }
    }

    None
}

pub(crate) fn resolve_stage_document(
    document: &StageDocumentInput,
    rule_layers: &StageRuleLayers,
) -> ResolvedReceiptData {
    let mut items = Vec::new();
    let mut warnings = Vec::new();
    let mut active_item_index: isize = -1;

    for item in &document.items {
        if item.removed {
            continue;
        }

        let description = item
            .description
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("UNKNOWN_ITEM")
            .to_string();
        let price = item.price.clone().unwrap_or_else(|| "0".to_string());
        let quantity = item.quantity.unwrap_or(1);
        let category = account_from_classification(item.classification.as_ref(), rule_layers);

        items.push(ResolvedReceiptItem {
            description,
            price,
            quantity,
            category,
        });
        active_item_index += 1;

        for message in &item.warning_messages {
            warnings.push(ResolvedReceiptWarning {
                message: message.clone(),
                after_item_index: Some(active_item_index as usize),
            });
        }
    }

    for message in &document.top_level_warning_messages {
        warnings.push(ResolvedReceiptWarning {
            message: message.clone(),
            after_item_index: None,
        });
    }

    ResolvedReceiptData {
        merchant: document
            .merchant
            .clone()
            .unwrap_or_else(|| "UNKNOWN_MERCHANT".to_string()),
        date_iso: document.date_iso.clone(),
        date_is_placeholder: document.date_iso.is_none(),
        total: document.total.clone().unwrap_or_else(|| "0".to_string()),
        tax: document.tax.clone(),
        subtotal: document.subtotal.clone(),
        raw_text: document.raw_text.clone(),
        image_filename: document.image_filename.clone(),
        items,
        warnings,
    }
}
