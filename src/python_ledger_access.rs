use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule, PyTuple};
use pyo3::wrap_pyfunction;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;

fn load_file_result<'py>(
    py: Python<'py>,
    ledger_path: &str,
) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    let loader = PyModule::import(py, "beancount.loader")?;
    let result = loader.getattr("load_file")?.call1((ledger_path,))?;
    let tuple = result.cast::<PyTuple>()?;
    Ok((tuple.get_item(0)?, tuple.get_item(1)?, tuple.get_item(2)?))
}

fn py_string(value: &Bound<'_, PyAny>) -> PyResult<String> {
    value.str()?.extract()
}

fn date_to_ordinal(value: &Bound<'_, PyAny>) -> PyResult<i32> {
    value.call_method0("toordinal")?.extract()
}

fn is_instance_of(entry: &Bound<'_, PyAny>, class_name: &str) -> PyResult<bool> {
    let py = entry.py();
    let data = PyModule::import(py, "beancount.core.data")?;
    let class_obj = data.getattr(class_name)?;
    entry.is_instance(&class_obj)
}

fn ledger_errors(py: Python<'_>, ledger_path: &str) -> PyResult<Vec<String>> {
    let (_, errors, _) = load_file_result(py, ledger_path)?;
    let mut rendered = Vec::new();
    for error in errors.try_iter()? {
        rendered.push(py_string(&error?)?);
    }
    Ok(rendered)
}

fn is_account_open_as_of(
    account: &str,
    as_of_ordinal: i32,
    last_open: &HashMap<String, i32>,
    last_close: &HashMap<String, i32>,
) -> bool {
    let Some(opened) = last_open.get(account) else {
        return false;
    };
    if *opened > as_of_ordinal {
        return false;
    }

    match last_close.get(account) {
        None => true,
        Some(closed) if *closed > as_of_ordinal => true,
        Some(closed) => *opened > *closed,
    }
}

fn parse_include_path(line: &str) -> Option<&str> {
    let stripped = line.trim_start();
    if stripped.starts_with(';') || !stripped.starts_with("include \"") {
        return None;
    }

    let rest = &stripped["include \"".len()..];
    let quote_end = rest.find('"')?;
    let include_path = &rest[..quote_end];
    let suffix = rest[quote_end + 1..].trim_start();
    if suffix.is_empty() || suffix.starts_with(';') {
        Some(include_path)
    } else {
        None
    }
}

fn is_transaction_start(line: &str) -> bool {
    let stripped = line.trim_start();
    if stripped.len() < 11 {
        return false;
    }

    let bytes = stripped.as_bytes();
    let is_digit = |idx: usize| bytes.get(idx).is_some_and(u8::is_ascii_digit);
    if !(is_digit(0)
        && is_digit(1)
        && is_digit(2)
        && is_digit(3)
        && bytes.get(4) == Some(&b'-')
        && is_digit(5)
        && is_digit(6)
        && bytes.get(7) == Some(&b'-')
        && is_digit(8)
        && is_digit(9)
        && bytes.get(10).is_some_and(u8::is_ascii_whitespace))
    {
        return false;
    }

    let Some(flag) = bytes.get(11).copied() else {
        return false;
    };
    if !(flag == b'*' || flag == b'!' || flag == b'?' || flag.is_ascii_alphabetic()) {
        return false;
    }

    bytes.get(12).is_none_or(|next| next.is_ascii_whitespace())
}

fn find_transaction_end(lines: &[String], start_idx: usize) -> usize {
    let mut idx = start_idx + 1;
    while idx < lines.len() {
        let line = &lines[idx];
        if line.trim().is_empty() {
            idx += 1;
            break;
        }
        if line.starts_with(' ') || line.starts_with('\t') {
            idx += 1;
            continue;
        }
        break;
    }
    idx
}

fn comment_block(lines: &[String]) -> Vec<String> {
    let mut out = Vec::with_capacity(lines.len());
    for line in lines {
        if line.trim().is_empty() || line.trim_start().starts_with(';') {
            out.push(line.clone());
        } else {
            out.push(format!("; {line}"));
        }
    }
    out
}

fn split_lines_keepends(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    text.split_inclusive('\n').map(str::to_owned).collect()
}

fn today_iso(py: Python<'_>) -> PyResult<String> {
    let datetime = PyModule::import(py, "datetime")?;
    let date = datetime.getattr("date")?;
    let today = date.getattr("today")?.call0()?;
    today.call_method0("isoformat")?.extract()
}

fn replace_transaction_with_include_impl(
    py: Python<'_>,
    statement_path: &str,
    line_number: usize,
    include_rel_path: &str,
    receipt_name: &str,
) -> PyResult<String> {
    let statement_text = fs::read_to_string(statement_path)?;
    let mut lines = split_lines_keepends(&statement_text);
    let include_prefix = format!("include \"{include_rel_path}\"");

    for line in &lines {
        if parse_include_path(line).is_some_and(|candidate| candidate == include_rel_path) {
            return Ok("already_applied".to_string());
        }
    }

    if line_number == 0 {
        return Err(PyValueError::new_err(format!(
            "Invalid line number {line_number} for {statement_path}"
        )));
    }

    let start_idx = line_number - 1;
    let Some(start_line) = lines.get(start_idx) else {
        return Err(PyValueError::new_err(format!(
            "Invalid line number {line_number} for {statement_path}"
        )));
    };
    if !is_transaction_start(start_line) {
        return Err(PyValueError::new_err(format!(
            "Line {line_number} in {statement_path} is not a transaction start: {}",
            start_line.trim_end()
        )));
    }

    let end_idx = find_transaction_end(&lines, start_idx);
    let original_block = &lines[start_idx..end_idx];
    if original_block.is_empty() {
        return Err(PyValueError::new_err(format!(
            "Empty transaction block at {statement_path}:{line_number}"
        )));
    }

    let stamp = today_iso(py)?;
    let mut replacement = Vec::new();
    replacement.push(format!(
        "; bb-match replaced from receipt {receipt_name} on {stamp}\n"
    ));
    replacement.extend(comment_block(original_block));
    if replacement
        .last()
        .is_some_and(|line| !line.trim().is_empty())
    {
        replacement.push("\n".to_string());
    }
    replacement.push(format!("{include_prefix}  ; bb-match: {receipt_name}\n"));
    replacement.push("\n".to_string());

    let mut new_lines = Vec::with_capacity(lines.len() - original_block.len() + replacement.len());
    new_lines.extend_from_slice(&lines[..start_idx]);
    new_lines.extend(replacement);
    new_lines.extend(lines.drain(end_idx..));
    fs::write(statement_path, new_lines.concat())?;
    Ok("applied".to_string())
}

#[pyfunction]
fn ledger_access_list_transactions(
    py: Python<'_>,
    ledger_path: &str,
) -> PyResult<(String, Vec<Py<PyDict>>, Vec<String>, Py<PyDict>)> {
    let (entries, errors, options) = load_file_result(py, ledger_path)?;
    let mut transactions = Vec::new();

    for entry in entries.try_iter()? {
        let entry = entry?;
        if !is_instance_of(&entry, "Transaction")? {
            continue;
        }

        let txn = PyDict::new(py);
        txn.set_item("date_ordinal", date_to_ordinal(&entry.getattr("date")?)?)?;
        txn.set_item("payee", entry.getattr("payee")?)?;
        txn.set_item("narration", entry.getattr("narration")?)?;

        let meta = entry.getattr("meta")?;
        let file_path = if meta.is_none() {
            "unknown".to_string()
        } else {
            py_string(&meta.call_method1("get", ("filename", "unknown"))?)?
        };
        let line_number = if meta.is_none() {
            0
        } else {
            meta.call_method1("get", ("lineno", 0))?
                .extract::<i64>()
                .unwrap_or(0)
        };
        txn.set_item("file_path", file_path)?;
        txn.set_item("line_number", line_number)?;

        let mut postings = Vec::new();
        for posting in entry.getattr("postings")?.try_iter()? {
            let posting = posting?;
            let payload = PyDict::new(py);
            payload.set_item("account", posting.getattr("account")?)?;

            let units = posting.getattr("units")?;
            if units.is_none() {
                payload.set_item("number_str", py.None())?;
                payload.set_item("currency", py.None())?;
            } else {
                let number = units.getattr("number")?;
                let currency = units.getattr("currency")?;
                if number.is_none() || currency.is_none() {
                    payload.set_item("number_str", py.None())?;
                    payload.set_item("currency", py.None())?;
                } else {
                    payload.set_item("number_str", py_string(&number)?)?;
                    payload.set_item("currency", py_string(&currency)?)?;
                }
            }
            postings.push(payload.unbind());
        }
        txn.set_item("postings", postings)?;
        transactions.push(txn.unbind());
    }

    let mut rendered_errors = Vec::new();
    for error in errors.try_iter()? {
        rendered_errors.push(py_string(&error?)?);
    }

    Ok((
        ledger_path.to_string(),
        transactions,
        rendered_errors,
        options.cast::<PyDict>()?.clone().unbind(),
    ))
}

#[pyfunction]
fn ledger_access_open_accounts(
    py: Python<'_>,
    ledger_path: &str,
    patterns: Vec<String>,
    as_of_ordinal: i32,
) -> PyResult<Vec<String>> {
    if patterns.is_empty() {
        return Ok(Vec::new());
    }

    let (entries, _, _) = load_file_result(py, ledger_path)?;
    let mut last_open: HashMap<String, i32> = HashMap::new();
    let mut last_close: HashMap<String, i32> = HashMap::new();
    for entry in entries.try_iter()? {
        let entry = entry?;
        if is_instance_of(&entry, "Open")? {
            let account: String = entry.getattr("account")?.extract()?;
            let opened = date_to_ordinal(&entry.getattr("date")?)?;
            last_open
                .entry(account)
                .and_modify(|existing| {
                    if opened > *existing {
                        *existing = opened;
                    }
                })
                .or_insert(opened);
        } else if is_instance_of(&entry, "Close")? {
            let account: String = entry.getattr("account")?.extract()?;
            let closed = date_to_ordinal(&entry.getattr("date")?)?;
            last_close
                .entry(account)
                .and_modify(|existing| {
                    if closed > *existing {
                        *existing = closed;
                    }
                })
                .or_insert(closed);
        }
    }

    let fnmatch = PyModule::import(py, "fnmatch")?;
    let matcher = fnmatch.getattr("fnmatch")?;
    let mut matches = Vec::new();
    for account in last_open.keys() {
        if !is_account_open_as_of(account, as_of_ordinal, &last_open, &last_close) {
            continue;
        }
        for pattern in &patterns {
            if matcher.call1((account, pattern))?.extract::<bool>()? {
                matches.push(account.clone());
                break;
            }
        }
    }

    matches.sort();
    Ok(matches)
}

#[pyfunction]
fn ledger_access_transaction_dates_for_account(
    py: Python<'_>,
    ledger_path: &str,
    account: &str,
) -> PyResult<Vec<i32>> {
    let (entries, _, _) = load_file_result(py, ledger_path)?;
    let mut dates = HashSet::new();

    for entry in entries.try_iter()? {
        let entry = entry?;
        if !is_instance_of(&entry, "Transaction")? {
            continue;
        }

        let mut found = false;
        for posting in entry.getattr("postings")?.try_iter()? {
            let posting = posting?;
            if posting.getattr("account")?.extract::<String>()? == account {
                found = true;
                break;
            }
        }

        if found {
            dates.insert(date_to_ordinal(&entry.getattr("date")?)?);
        }
    }

    let mut collected: Vec<i32> = dates.into_iter().collect();
    collected.sort();
    Ok(collected)
}

#[pyfunction]
fn ledger_access_validate_ledger(py: Python<'_>, ledger_path: &str) -> PyResult<Vec<String>> {
    ledger_errors(py, ledger_path)
}

#[pyfunction]
fn ledger_access_snapshot_receipt_match_files(
    statement_path: &str,
    enriched_path: &str,
) -> PyResult<(String, bool, Option<String>)> {
    let statement_original = fs::read_to_string(statement_path)?;
    let enriched_original = match fs::read_to_string(enriched_path) {
        Ok(content) => Some(content),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => None,
        Err(err) => return Err(err.into()),
    };
    Ok((
        statement_original,
        enriched_original.is_some(),
        enriched_original,
    ))
}

#[pyfunction]
fn ledger_access_restore_receipt_match_files(
    statement_path: &str,
    statement_original: &str,
    enriched_path: &str,
    enriched_existed: bool,
    enriched_original: Option<&str>,
) -> PyResult<()> {
    fs::write(statement_path, statement_original)?;
    if enriched_existed {
        if let Some(content) = enriched_original {
            if let Some(parent) = Path::new(enriched_path).parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(enriched_path, content)?;
        }
    } else if Path::new(enriched_path).exists() {
        fs::remove_file(enriched_path)?;
    }
    Ok(())
}

#[pyfunction]
fn ledger_access_replace_transaction_with_include(
    py: Python<'_>,
    statement_path: &str,
    line_number: usize,
    include_rel_path: &str,
    receipt_name: &str,
) -> PyResult<String> {
    replace_transaction_with_include_impl(
        py,
        statement_path,
        line_number,
        include_rel_path,
        receipt_name,
    )
}

#[pyfunction]
fn ledger_access_apply_receipt_match(
    py: Python<'_>,
    ledger_path: &str,
    statement_path: &str,
    line_number: usize,
    include_rel_path: &str,
    receipt_name: &str,
    enriched_path: &str,
    enriched_content: &str,
) -> PyResult<String> {
    let original_statement = fs::read_to_string(statement_path)?;
    let original_enriched = match fs::read_to_string(enriched_path) {
        Ok(content) => Some(content),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => None,
        Err(err) => return Err(err.into()),
    };

    let result = (|| -> PyResult<String> {
        let status = replace_transaction_with_include_impl(
            py,
            statement_path,
            line_number,
            include_rel_path,
            receipt_name,
        )?;
        if status == "already_applied" {
            return Ok(status);
        }

        if let Some(parent) = Path::new(enriched_path).parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(enriched_path, enriched_content)?;

        let apply_errors = ledger_errors(py, ledger_path)?;
        if !apply_errors.is_empty() {
            let preview = apply_errors
                .iter()
                .take(2)
                .cloned()
                .collect::<Vec<_>>()
                .join("; ");
            return Err(PyRuntimeError::new_err(format!(
                "ledger validation failed after replacement: {preview}"
            )));
        }

        Ok(status)
    })();

    if result.is_err() {
        fs::write(statement_path, original_statement)?;
        match original_enriched {
            Some(content) => {
                if let Some(parent) = Path::new(enriched_path).parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::write(enriched_path, content)?;
            }
            None => {
                if Path::new(enriched_path).exists() {
                    fs::remove_file(enriched_path)?;
                }
            }
        }
    }

    result
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(ledger_access_list_transactions, module)?)?;
    module.add_function(wrap_pyfunction!(ledger_access_open_accounts, module)?)?;
    module.add_function(wrap_pyfunction!(
        ledger_access_transaction_dates_for_account,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(ledger_access_validate_ledger, module)?)?;
    module.add_function(wrap_pyfunction!(
        ledger_access_snapshot_receipt_match_files,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        ledger_access_restore_receipt_match_files,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        ledger_access_replace_transaction_with_include,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(ledger_access_apply_receipt_match, module)?)?;
    Ok(())
}
