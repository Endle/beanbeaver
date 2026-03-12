use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
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
struct PyBuildRuleEntry {
    keywords: Vec<String>,
    target: Option<String>,
    tags: Vec<String>,
    priority: i32,
    exact_only: bool,
}

fn string_or_list(value: Option<&Bound<'_, PyAny>>) -> PyResult<Vec<String>> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    if let Ok(text) = value.extract::<String>() {
        let cleaned = text.trim();
        return Ok(if cleaned.is_empty() {
            Vec::new()
        } else {
            vec![cleaned.to_string()]
        });
    }
    if let Ok(values) = value.extract::<Vec<String>>() {
        return Ok(values
            .into_iter()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .collect());
    }
    Ok(Vec::new())
}

fn normalize_tags(values: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut normalized = Vec::new();
    for value in values {
        let cleaned = value.trim().to_ascii_lowercase();
        if cleaned.is_empty() || !seen.insert(cleaned.clone()) {
            continue;
        }
        normalized.push(cleaned);
    }
    normalized
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyBuildRuleEntry {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let dict = ob
            .cast::<PyDict>()
            .map_err(|_| PyTypeError::new_err("rule must be a dict"))?;
        let keywords = string_or_list(dict.get_item("keywords")?.as_ref())?;
        let target = dict
            .get_item("key")?
            .or(dict.get_item("category")?)
            .map(|value| value.extract::<String>())
            .transpose()?
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty());
        let tags = normalize_tags(string_or_list(dict.get_item("tags")?.as_ref())?);
        let priority = dict
            .get_item("priority")?
            .and_then(|value| value.extract::<i32>().ok())
            .unwrap_or(0);
        let exact_only = dict
            .get_item("exact_only")?
            .and_then(|value| value.extract::<bool>().ok())
            .unwrap_or(false);
        Ok(Self {
            keywords,
            target,
            tags,
            priority,
            exact_only,
        })
    }
}

#[derive(Clone, Debug)]
struct PyBuildClassifierConfig {
    exact_only_keywords: Vec<String>,
    rules: Vec<PyBuildRuleEntry>,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyBuildClassifierConfig {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let dict = ob
            .cast::<PyDict>()
            .map_err(|_| PyTypeError::new_err("classifier config must be a dict"))?;
        Ok(Self {
            exact_only_keywords: string_or_list(dict.get_item("exact_only_keywords")?.as_ref())?,
            rules: dict
                .get_item("rules")?
                .map(|value| value.extract::<Vec<PyBuildRuleEntry>>())
                .transpose()?
                .unwrap_or_default(),
        })
    }
}

#[derive(Clone, Debug)]
struct PyAccountConfig {
    accounts: HashMap<String, String>,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyAccountConfig {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let dict = ob
            .cast::<PyDict>()
            .map_err(|_| PyTypeError::new_err("account config must be a dict"))?;
        let accounts = dict
            .get_item("accounts")?
            .map(|value| value.extract::<HashMap<String, String>>())
            .transpose()?
            .unwrap_or_default();
        Ok(Self { accounts })
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
fn receipt_build_item_category_rule_layers(
    py: Python<'_>,
    default_account_mapping: HashMap<String, String>,
    classifier_configs: Vec<PyBuildClassifierConfig>,
    account_configs: Vec<PyAccountConfig>,
) -> PyResult<Py<PyDict>> {
    let built = receipt_categories::build_rule_layers(
        default_account_mapping,
        classifier_configs
            .into_iter()
            .map(|config| receipt_categories::BuildClassifierConfig {
                exact_only_keywords: config.exact_only_keywords,
                rules: config
                    .rules
                    .into_iter()
                    .map(|rule| receipt_categories::BuildRuleEntry {
                        keywords: rule.keywords,
                        target: rule.target,
                        tags: rule.tags,
                        priority: rule.priority,
                        exact_only: rule.exact_only,
                    })
                    .collect(),
            })
            .collect(),
        account_configs.into_iter().map(|config| config.accounts).collect(),
    );

    let result = PyDict::new(py);
    let rules = built
        .rules
        .into_iter()
        .map(|rule| (rule.keywords, rule.category, rule.tags, rule.priority))
        .collect::<Vec<_>>();
    result.set_item("rules", rules)?;
    result.set_item(
        "exact_only_keywords",
        built.exact_only_keywords.into_iter().collect::<Vec<_>>(),
    )?;
    result.set_item("account_mapping", built.account_mapping)?;
    Ok(result.unbind())
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

#[pyfunction]
fn receipt_account_for_category_key(
    category: Option<String>,
    account_mapping: HashMap<String, String>,
    default: Option<String>,
) -> Option<String> {
    receipt_categories::resolve_account_target(
        category.as_deref(),
        &account_mapping,
        default.as_deref(),
    )
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_build_item_category_rule_layers, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_classify_item_key, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_classify_item_tags, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_find_item_matches, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_list_item_categories, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_account_for_category_key, module)?)?;
    Ok(())
}
