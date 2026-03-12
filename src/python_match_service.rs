use crate::match_domain::{ApplyMatchResult, MatchCandidate, ReceiptMatchPlan};
use crate::match_service::{apply_receipt_match_service, plan_receipt_match, plan_receipt_matches};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3::wrap_pyfunction;

fn candidate_to_dict(py: Python<'_>, candidate: MatchCandidate) -> PyResult<Py<PyDict>> {
    let payload = PyDict::new(py);
    payload.set_item("file_path", candidate.candidate_ref.file_path)?;
    payload.set_item("line_number", candidate.candidate_ref.line_number)?;
    payload.set_item("confidence", candidate.confidence)?;
    payload.set_item("display", candidate.display)?;
    payload.set_item("payee", candidate.payee)?;
    payload.set_item("narration", candidate.narration)?;
    payload.set_item("date", candidate.date_iso)?;
    payload.set_item("amount", candidate.amount)?;
    payload.set_item("details", candidate.details)?;
    payload.set_item("strength", candidate.strength)?;
    Ok(payload.unbind())
}

fn plan_to_dict(py: Python<'_>, plan: ReceiptMatchPlan) -> PyResult<Py<PyDict>> {
    let payload = PyDict::new(py);
    payload.set_item("path", plan.receipt_path)?;
    payload.set_item("ledger_path", plan.ledger_path)?;
    payload.set_item("errors", plan.errors)?;
    payload.set_item("warning", plan.warning)?;
    payload.set_item("used_relaxed_threshold", plan.used_relaxed_threshold)?;
    let candidates = PyList::empty(py);
    for candidate in plan.candidates {
        candidates.append(candidate_to_dict(py, candidate)?)?;
    }
    payload.set_item("candidates", candidates)?;
    Ok(payload.unbind())
}

fn apply_result_to_dict(py: Python<'_>, result: ApplyMatchResult) -> PyResult<Py<PyDict>> {
    let payload = PyDict::new(py);
    payload.set_item("status", result.status)?;
    payload.set_item("ledger_path", result.ledger_path)?;
    payload.set_item("matched_receipt_path", result.matched_receipt_path)?;
    payload.set_item("enriched_path", result.enriched_path)?;
    payload.set_item("message", result.message)?;
    Ok(payload.unbind())
}

#[pyfunction]
fn match_service_plan_receipt(
    py: Python<'_>,
    approved_receipt_path: &str,
    ledger_path: Option<&str>,
) -> PyResult<Py<PyDict>> {
    plan_to_dict(
        py,
        plan_receipt_match(py, approved_receipt_path, ledger_path)?,
    )
}

#[pyfunction]
fn match_service_plan_receipts(
    py: Python<'_>,
    approved_receipt_paths: Vec<String>,
    ledger_path: Option<&str>,
) -> PyResult<Py<PyList>> {
    let plans = plan_receipt_matches(py, approved_receipt_paths, ledger_path)?;
    let payload = PyList::empty(py);
    for plan in plans {
        payload.append(plan_to_dict(py, plan)?)?;
    }
    Ok(payload.unbind())
}

#[pyfunction]
fn match_service_apply_match(
    py: Python<'_>,
    approved_receipt_path: &str,
    candidate_file_path: &str,
    candidate_line_number: i32,
    ledger_path: Option<&str>,
) -> PyResult<Py<PyDict>> {
    apply_result_to_dict(
        py,
        apply_receipt_match_service(
            py,
            approved_receipt_path,
            candidate_file_path,
            candidate_line_number,
            ledger_path,
        )?,
    )
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(match_service_plan_receipt, module)?)?;
    module.add_function(wrap_pyfunction!(match_service_plan_receipts, module)?)?;
    module.add_function(wrap_pyfunction!(match_service_apply_match, module)?)?;
    Ok(())
}
