use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use std::collections::{HashMap, HashSet};

use crate::receipt_categories;

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
struct PyRuleLayersInput {
    rules: Vec<PyRuleEntry>,
    exact_only_keywords: HashSet<String>,
    account_mapping: HashMap<String, String>,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyRuleLayersInput {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let rules = ob.getattr("rules")?.extract::<Vec<PyRuleEntry>>()?;
        let exact_obj = ob.getattr("exact_only_keywords")?;
        let mut exact_only_keywords = HashSet::new();
        for item in exact_obj.try_iter()? {
            exact_only_keywords.insert(item?.extract::<String>()?);
        }
        let account_mapping = ob
            .getattr("account_mapping")?
            .extract::<HashMap<String, String>>()?;
        if rules.is_empty() && exact_only_keywords.is_empty() && account_mapping.is_empty() {
            return Err(PyTypeError::new_err("invalid item category rule layers"));
        }
        Ok(Self {
            rules,
            exact_only_keywords,
            account_mapping,
        })
    }
}

fn to_rule_layers(input: PyRuleLayersInput) -> receipt_categories::CategoryRuleLayers {
    receipt_categories::CategoryRuleLayers {
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
        account_mapping: input.account_mapping,
    }
}

#[pyfunction]
fn receipt_classify_item_key(
    description: &str,
    rule_layers: PyRuleLayersInput,
    default: Option<String>,
) -> Option<String> {
    receipt_categories::classify_item_key(description, &to_rule_layers(rule_layers), default)
}

#[pyfunction]
fn receipt_classify_item_tags(description: &str, rule_layers: PyRuleLayersInput) -> Vec<String> {
    receipt_categories::classify_item_tags(description, &to_rule_layers(rule_layers))
}

#[pyfunction]
fn receipt_find_item_matches(
    description: &str,
    rule_layers: PyRuleLayersInput,
) -> Vec<(Option<String>, String, i32, usize, bool, usize)> {
    receipt_categories::sorted_matches_for_debug(description, &to_rule_layers(rule_layers))
        .into_iter()
        .map(|matched| {
            (
                matched.category,
                matched.matched_keyword,
                matched.priority,
                matched.keyword_length,
                matched.is_exact,
                matched.rule_index,
            )
        })
        .collect()
}

#[pyfunction]
fn receipt_list_item_categories(rule_layers: PyRuleLayersInput) -> Vec<(String, String)> {
    receipt_categories::list_item_categories(&to_rule_layers(rule_layers))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_classify_item_key, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_classify_item_tags, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_find_item_matches, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_list_item_categories, module)?)?;
    Ok(())
}
