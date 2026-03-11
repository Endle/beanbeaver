use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::wrap_pyfunction;
use std::collections::HashSet;

use crate::receipt_categories;
use crate::receipt_staged_json;

#[derive(Clone, Debug)]
struct PyRuleEntry {
    keywords: Vec<String>,
    category: Option<String>,
    tags: Vec<String>,
    priority: i32,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyRuleEntry {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        Ok(Self {
            keywords: ob.getattr("keywords")?.extract::<Vec<String>>()?,
            category: ob.getattr("category")?.extract::<Option<String>>()?,
            tags: ob.getattr("tags")?.extract::<Vec<String>>()?,
            priority: ob.getattr("priority")?.extract::<i32>()?,
        })
    }
}

#[derive(Clone, Debug)]
struct PyStageRuleLayersInput {
    rules: Vec<PyRuleEntry>,
    exact_only_keywords: HashSet<String>,
    account_mapping: Vec<(String, String)>,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyStageRuleLayersInput {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let rules = ob.getattr("rules")?.extract::<Vec<PyRuleEntry>>()?;

        let exact_obj = ob.getattr("exact_only_keywords")?;
        let mut exact_only_keywords = HashSet::new();
        for item in exact_obj.try_iter()? {
            exact_only_keywords.insert(item?.extract::<String>()?);
        }

        let account_mapping_obj = ob.getattr("account_mapping")?;
        let mapping_items = account_mapping_obj.call_method0("items")?;
        let mut account_mapping = Vec::new();
        for item in mapping_items.try_iter()? {
            let pair = item?.extract::<(String, String)>()?;
            account_mapping.push(pair);
        }

        Ok(Self {
            rules,
            exact_only_keywords,
            account_mapping,
        })
    }
}

fn to_stage_rule_layers(input: PyStageRuleLayersInput) -> receipt_staged_json::StageRuleLayers {
    receipt_staged_json::StageRuleLayers {
        category_rules: receipt_categories::CategoryRuleLayers {
            rules: input
                .rules
                .into_iter()
                .map(|rule| receipt_categories::CategoryRule {
                    keywords: rule.keywords,
                    category: rule.category,
                    tags: rule.tags,
                    priority: rule.priority,
                })
                .collect(),
            exact_only_keywords: input.exact_only_keywords,
        },
        account_mapping: input.account_mapping,
    }
}

fn fixed_decimal_string(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() {
        return Ok(None);
    }
    Ok(Some(value.call_method1("__format__", (".2f",))?.extract::<String>()?))
}

fn decimalish_to_string(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() {
        return Ok(None);
    }

    let decimal_cls = PyModule::import(py, "decimal")?.getattr("Decimal")?;

    if value.is_instance(&decimal_cls)? {
        return Ok(Some(value.str()?.extract::<String>()?));
    }

    if let Ok(int_value) = value.extract::<i64>() {
        let candidate = int_value.to_string();
        decimal_cls.call1((candidate.clone(),))?;
        return Ok(Some(candidate));
    }

    if let Ok(float_value) = value.extract::<f64>() {
        let candidate = float_value.to_string();
        if decimal_cls.call1((candidate.clone(),)).is_ok() {
            return Ok(Some(candidate));
        }
        return Ok(None);
    }

    if let Ok(text) = value.extract::<String>() {
        let stripped = text.trim();
        if stripped.is_empty() {
            return Ok(None);
        }
        if decimal_cls.call1((stripped,)).is_ok() {
            return Ok(Some(stripped.to_string()));
        }
    }

    Ok(None)
}

fn dateish_to_iso_string(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() {
        return Ok(None);
    }

    let date_cls = PyModule::import(py, "datetime")?.getattr("date")?;
    if value.is_instance(&date_cls)? {
        return Ok(Some(value.str()?.extract::<String>()?));
    }

    if let Ok(text) = value.extract::<String>() {
        let stripped = text.trim();
        if stripped.is_empty() {
            return Ok(None);
        }
        if date_cls
            .getattr("fromisoformat")?
            .call1((stripped,))
            .is_ok()
        {
            return Ok(Some(stripped.to_string()));
        }
    }

    Ok(None)
}

fn truthy_string(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() || !value.is_truthy()? {
        return Ok(None);
    }
    Ok(Some(value.str()?.extract::<String>()?))
}

fn optional_string_attr(obj: &Bound<'_, PyAny>, attr: &str) -> PyResult<Option<String>> {
    let value = obj.getattr(attr)?;
    if value.is_none() {
        return Ok(None);
    }
    Ok(Some(value.extract::<String>()?))
}

fn required_string_attr(obj: &Bound<'_, PyAny>, attr: &str) -> PyResult<String> {
    obj.getattr(attr)?.extract::<String>()
}

fn optional_usize_attr(obj: &Bound<'_, PyAny>, attr: &str) -> PyResult<Option<usize>> {
    let value = obj.getattr(attr)?;
    if value.is_none() {
        return Ok(None);
    }
    Ok(value.extract::<usize>().ok())
}

fn extract_receipt_input(receipt: &Bound<'_, PyAny>) -> PyResult<receipt_staged_json::ReceiptInput> {
    let items_any = receipt.getattr("items")?;
    let mut items = Vec::new();
    for item in items_any.try_iter()? {
        let item = item?;
        items.push(receipt_staged_json::ReceiptItemInput {
            description: required_string_attr(&item, "description")?,
            price: fixed_decimal_string(&item.getattr("price")?)?,
            quantity: item.getattr("quantity")?.extract::<i32>().unwrap_or(1),
            category: optional_string_attr(&item, "category")?,
        });
    }

    let warnings_any = receipt.getattr("warnings")?;
    let mut warnings = Vec::new();
    for warning in warnings_any.try_iter()? {
        let warning = warning?;
        warnings.push(receipt_staged_json::ReceiptWarningInput {
            message: required_string_attr(&warning, "message")?,
            after_item_index: optional_usize_attr(&warning, "after_item_index")?,
        });
    }

    Ok(receipt_staged_json::ReceiptInput {
        merchant: required_string_attr(receipt, "merchant")?,
        date_iso: receipt.getattr("date")?.str()?.extract::<String>()?,
        total: fixed_decimal_string(&receipt.getattr("total")?)?.unwrap_or_else(|| "0.00".to_string()),
        date_is_placeholder: receipt.getattr("date_is_placeholder")?.extract::<bool>()?,
        items,
        tax: fixed_decimal_string(&receipt.getattr("tax")?)?,
        subtotal: fixed_decimal_string(&receipt.getattr("subtotal")?)?,
        raw_text: required_string_attr(receipt, "raw_text")?,
        image_filename: required_string_attr(receipt, "image_filename")?,
        warnings,
    })
}

fn effective_receipt_value<'py>(
    document: &'py Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if let Some(review_any) = document.get_item("review")? {
        if let Ok(review) = review_any.cast::<PyDict>() {
            if let Some(value) = review.get_item(key)? {
                if !value.is_none() {
                    return Ok(Some(value));
                }
            }
        }
    }

    if let Some(receipt_any) = document.get_item("receipt")? {
        if let Ok(receipt) = receipt_any.cast::<PyDict>() {
            return receipt.get_item(key);
        }
    }

    Ok(None)
}

fn effective_item_value<'py>(
    item: &'py Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if let Some(review_any) = item.get_item("review")? {
        if let Ok(review) = review_any.cast::<PyDict>() {
            if let Some(value) = review.get_item(key)? {
                if !value.is_none() {
                    return Ok(Some(value));
                }
            }
        }
    }
    item.get_item(key)
}

fn extract_tags_from_dict(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<Vec<String>>> {
    let Some(tags_any) = dict.get_item(key)? else {
        return Ok(None);
    };

    let Ok(iter) = tags_any.try_iter() else {
        return Ok(Some(Vec::new()));
    };
    let mut tags = Vec::new();
    for tag in iter {
        let normalized = tag?.str()?.extract::<String>()?.trim().to_lowercase();
        tags.push(normalized);
    }
    Ok(Some(tags))
}

fn merged_item_classification(
    item: &Bound<'_, PyDict>,
) -> PyResult<Option<receipt_staged_json::ClassificationData>> {
    let mut any = false;
    let mut category: Option<String> = None;
    let mut tags: Vec<String> = Vec::new();
    let mut category_present = false;
    let mut tags_present = false;

    if let Some(classification_any) = item.get_item("classification")? {
        if let Ok(classification) = classification_any.cast::<PyDict>() {
            any |= !classification.is_empty();
            if classification.contains("category")? {
                category_present = true;
                if let Some(value) = classification.get_item("category")? {
                    if !value.is_none() {
                        category = Some(value.extract::<String>()?);
                    }
                }
            }
            if let Some(extracted_tags) = extract_tags_from_dict(classification, "tags")? {
                tags_present = true;
                tags = extracted_tags;
            }
        }
    }

    if let Some(review_any) = item.get_item("review")? {
        if let Ok(review) = review_any.cast::<PyDict>() {
            if let Some(review_classification_any) = review.get_item("classification")? {
                if let Ok(review_classification) = review_classification_any.cast::<PyDict>() {
                    any |= !review_classification.is_empty();
                    if review_classification.contains("category")? {
                        category_present = true;
                        category = None;
                        if let Some(value) = review_classification.get_item("category")? {
                            if !value.is_none() {
                                category = Some(value.extract::<String>()?);
                            }
                        }
                    }
                    if let Some(extracted_tags) = extract_tags_from_dict(review_classification, "tags")? {
                        tags_present = true;
                        tags = extracted_tags;
                    }
                }
            }
        }
    }

    if any || category_present || tags_present {
        return Ok(Some(receipt_staged_json::ClassificationData { category, tags }));
    }

    Ok(None)
}

fn extract_warning_messages(value: Option<Bound<'_, PyAny>>) -> PyResult<Vec<String>> {
    let Some(warnings_any) = value else {
        return Ok(Vec::new());
    };

    let Ok(iter) = warnings_any.try_iter() else {
        return Ok(Vec::new());
    };

    let mut messages = Vec::new();
    for warning in iter {
        let warning = warning?;
        let Ok(warning_dict) = warning.cast::<PyDict>() else {
            continue;
        };
        let Some(message_any) = warning_dict.get_item("message")? else {
            continue;
        };
        if !message_any.is_truthy()? {
            continue;
        }
        messages.push(message_any.str()?.extract::<String>()?);
    }
    Ok(messages)
}

fn extract_stage_document_input(
    py: Python<'_>,
    document: &Bound<'_, PyAny>,
) -> PyResult<receipt_staged_json::StageDocumentInput> {
    let document = document
        .cast::<PyDict>()
        .map_err(|_| PyTypeError::new_err("stage document must be a dict"))?;

    let merchant = match effective_receipt_value(document, "merchant")? {
        Some(value) => truthy_string(&value)?,
        None => None,
    };
    let date_iso = match effective_receipt_value(document, "date")? {
        Some(value) => dateish_to_iso_string(py, &value)?,
        None => None,
    };
    let total = match effective_receipt_value(document, "total")? {
        Some(value) => decimalish_to_string(py, &value)?,
        None => None,
    };
    let tax = match effective_receipt_value(document, "tax")? {
        Some(value) => decimalish_to_string(py, &value)?,
        None => None,
    };
    let subtotal = match effective_receipt_value(document, "subtotal")? {
        Some(value) => decimalish_to_string(py, &value)?,
        None => None,
    };

    let raw_text = match document.get_item("raw_text")? {
        Some(value) if !value.is_none() => value.str()?.extract::<String>()?,
        _ => String::new(),
    };

    let image_filename = match document.get_item("meta")? {
        Some(meta_any) => {
            if let Ok(meta) = meta_any.cast::<PyDict>() {
                match meta.get_item("image_filename")? {
                    Some(value) if !value.is_none() => value.str()?.extract::<String>()?,
                    _ => String::new(),
                }
            } else {
                String::new()
            }
        }
        None => String::new(),
    };

    let mut items = Vec::new();
    if let Some(items_any) = document.get_item("items")? {
        if let Ok(iter) = items_any.try_iter() {
            for item in iter {
                let item = item?;
                let Ok(item_dict) = item.cast::<PyDict>() else {
                    continue;
                };

                let removed = if let Some(review_any) = item_dict.get_item("review")? {
                    if let Ok(review) = review_any.cast::<PyDict>() {
                        match review.get_item("removed")? {
                            Some(value) => value.is_truthy()?,
                            None => false,
                        }
                    } else {
                        false
                    }
                } else {
                    false
                };

                let description = match effective_item_value(item_dict, "description")? {
                    Some(value) => {
                        if value.is_none() {
                            None
                        } else {
                            Some(value.str()?.extract::<String>()?)
                        }
                    }
                    None => None,
                };
                let price = match effective_item_value(item_dict, "price")? {
                    Some(value) => decimalish_to_string(py, &value)?,
                    None => None,
                };
                let quantity = match effective_item_value(item_dict, "quantity")? {
                    Some(value) if !value.is_none() => value.extract::<i32>().ok(),
                    _ => None,
                };

                let classification = merged_item_classification(item_dict)?;
                let warning_messages = extract_warning_messages(item_dict.get_item("warnings")?)?;

                items.push(receipt_staged_json::StageDocumentItemInput {
                    removed,
                    description,
                    price,
                    quantity,
                    classification,
                    warning_messages,
                });
            }
        }
    }

    let top_level_warning_messages = extract_warning_messages(document.get_item("warnings")?)?;

    Ok(receipt_staged_json::StageDocumentInput {
        merchant,
        date_iso,
        total,
        tax,
        subtotal,
        raw_text,
        image_filename,
        items,
        top_level_warning_messages,
    })
}

fn structured_warning_dict<'py>(
    py: Python<'py>,
    warning: &receipt_staged_json::StructuredWarning,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("message", &warning.message)?;
    dict.set_item("source", &warning.source)?;
    dict.set_item("stage", &warning.stage)?;
    Ok(dict)
}

#[pyfunction]
fn receipt_build_parsed_receipt_stage(
    py: Python<'_>,
    receipt: &Bound<'_, PyAny>,
    rule_layers: PyStageRuleLayersInput,
    raw_ocr_payload: Option<Py<PyAny>>,
    ocr_json_path: Option<String>,
    image_sha256: Option<String>,
    created_by: String,
    pass_name: String,
    created_at: String,
    receipt_id: String,
) -> PyResult<Py<PyDict>> {
    let receipt_input = extract_receipt_input(receipt)?;
    let stage = receipt_staged_json::build_parsed_receipt_stage(
        &receipt_input,
        &to_stage_rule_layers(rule_layers),
        &receipt_id,
        &created_at,
        ocr_json_path,
        image_sha256,
        &created_by,
        &pass_name,
    );

    let meta = PyDict::new(py);
    meta.set_item("schema_version", stage.meta.schema_version)?;
    meta.set_item("receipt_id", stage.meta.receipt_id)?;
    meta.set_item("stage", stage.meta.stage)?;
    meta.set_item("stage_index", stage.meta.stage_index)?;
    meta.set_item("created_at", stage.meta.created_at)?;
    meta.set_item("created_by", stage.meta.created_by)?;
    meta.set_item("pass_name", stage.meta.pass_name)?;
    meta.set_item("image_filename", stage.meta.image_filename)?;
    meta.set_item("image_sha256", stage.meta.image_sha256)?;
    meta.set_item("ocr_json_path", stage.meta.ocr_json_path)?;

    let receipt_dict = PyDict::new(py);
    receipt_dict.set_item("merchant", stage.receipt.merchant)?;
    receipt_dict.set_item("date", stage.receipt.date)?;
    receipt_dict.set_item("currency", stage.receipt.currency)?;
    receipt_dict.set_item("subtotal", stage.receipt.subtotal)?;
    receipt_dict.set_item("tax", stage.receipt.tax)?;
    receipt_dict.set_item("total", stage.receipt.total)?;

    let items = Vec::from_iter(stage.items.iter().map(|item| -> PyResult<Py<PyDict>> {
        let item_dict = PyDict::new(py);
        item_dict.set_item("id", &item.id)?;
        item_dict.set_item("description", &item.description)?;
        item_dict.set_item("price", &item.price)?;
        item_dict.set_item("quantity", item.quantity)?;
        if let Some(classification) = &item.classification {
            let classification_dict = PyDict::new(py);
            classification_dict.set_item("category", &classification.category)?;
            classification_dict.set_item("tags", &classification.tags)?;
            classification_dict.set_item("confidence", 1.0)?;
            classification_dict.set_item("source", "rule_engine")?;
            item_dict.set_item("classification", classification_dict)?;
        } else {
            item_dict.set_item("classification", py.None())?;
        }
        let warning_dicts =
            Vec::from_iter(item.warnings.iter().map(|warning| structured_warning_dict(py, warning).map(Bound::unbind)))
                .into_iter()
                .collect::<PyResult<Vec<_>>>()?;
        item_dict.set_item("warnings", warning_dicts)?;
        let meta_dict = PyDict::new(py);
        meta_dict.set_item("source", &item.source)?;
        item_dict.set_item("meta", meta_dict)?;
        Ok(item_dict.unbind())
    }))
    .into_iter()
    .collect::<PyResult<Vec<_>>>()?;

    let warnings =
        Vec::from_iter(stage.warnings.iter().map(|warning| structured_warning_dict(py, warning).map(Bound::unbind)))
            .into_iter()
            .collect::<PyResult<Vec<_>>>()?;

    let debug = if let Some(payload) = raw_ocr_payload {
        let debug_dict = PyDict::new(py);
        debug_dict.set_item("ocr_payload", payload.bind(py))?;
        Some(debug_dict.unbind())
    } else {
        None
    };

    let document = PyDict::new(py);
    document.set_item("meta", meta)?;
    document.set_item("receipt", receipt_dict)?;
    document.set_item("items", items)?;
    document.set_item("warnings", warnings)?;
    document.set_item("raw_text", stage.raw_text)?;
    document.set_item("debug", debug)?;
    Ok(document.unbind())
}

#[pyfunction]
fn receipt_get_stage_summary(
    py: Python<'_>,
    document: &Bound<'_, PyAny>,
) -> PyResult<(Option<String>, Option<String>, Option<String>)> {
    let stage_input = extract_stage_document_input(py, document)?;
    Ok(receipt_staged_json::get_stage_summary(&stage_input))
}

#[pyfunction]
fn receipt_resolve_stage_document(
    py: Python<'_>,
    document: &Bound<'_, PyAny>,
    rule_layers: PyStageRuleLayersInput,
) -> PyResult<Py<PyDict>> {
    let stage_input = extract_stage_document_input(py, document)?;
    let resolved = receipt_staged_json::resolve_stage_document(&stage_input, &to_stage_rule_layers(rule_layers));

    let items = resolved
        .items
        .iter()
        .map(|item| (item.description.clone(), item.price.clone(), item.quantity, item.category.clone()))
        .collect::<Vec<_>>();
    let warnings = resolved
        .warnings
        .iter()
        .map(|warning| (warning.message.clone(), warning.after_item_index))
        .collect::<Vec<_>>();

    let document = PyDict::new(py);
    document.set_item("merchant", resolved.merchant)?;
    document.set_item("date", resolved.date_iso)?;
    document.set_item("date_is_placeholder", resolved.date_is_placeholder)?;
    document.set_item("total", resolved.total)?;
    document.set_item("tax", resolved.tax)?;
    document.set_item("subtotal", resolved.subtotal)?;
    document.set_item("raw_text", resolved.raw_text)?;
    document.set_item("image_filename", resolved.image_filename)?;
    document.set_item("items", items)?;
    document.set_item("warnings", warnings)?;
    Ok(document.unbind())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_build_parsed_receipt_stage, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_get_stage_summary, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_resolve_stage_document, module)?)?;
    Ok(())
}
