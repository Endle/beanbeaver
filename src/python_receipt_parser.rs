use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3::wrap_pyfunction;
use std::collections::HashSet;

use receipt_core::ocr_transform::{self, RawDetection};
use receipt_core::receipt_categories;
use receipt_core::receipt_parse_helpers;
use receipt_core::receipt_parser;
use receipt_core::receipt_spatial;

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
    account_mapping: Vec<(String, String)>,
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

        let mapping_items = ob.getattr("account_mapping")?.call_method0("items")?;
        let mut account_mapping = Vec::new();
        for item in mapping_items.try_iter()? {
            account_mapping.push(item?.extract::<(String, String)>()?);
        }

        Ok(Self {
            rules,
            exact_only_keywords,
            account_mapping,
        })
    }
}

fn to_rule_layers(input: PyRuleLayersInput) -> receipt_parser::ParserRuleLayers {
    let account_mapping = input.account_mapping;
    let category_account_mapping = account_mapping.iter().cloned().collect();

    receipt_parser::ParserRuleLayers {
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
            account_mapping: category_account_mapping,
        },
        account_mapping,
    }
}

#[derive(Clone, Debug)]
struct PyBboxInput {
    left: f64,
    top: f64,
    right: f64,
    bottom: f64,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyBboxInput {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        if let Ok(dict) = ob.cast::<PyDict>() {
            let left = dict
                .get_item("left")?
                .and_then(|value| value.extract::<f64>().ok())
                .unwrap_or(0.0);
            let top = dict
                .get_item("top")?
                .and_then(|value| value.extract::<f64>().ok())
                .unwrap_or(0.0);
            let right = dict
                .get_item("right")?
                .and_then(|value| value.extract::<f64>().ok())
                .unwrap_or(left);
            let bottom = dict
                .get_item("bottom")?
                .and_then(|value| value.extract::<f64>().ok())
                .unwrap_or(top);
            return Ok(Self {
                left,
                top,
                right,
                bottom,
            });
        }

        if let Ok(points) = ob.extract::<Vec<(f64, f64)>>() {
            if points.len() >= 2 {
                return Ok(Self {
                    left: points[0].0,
                    top: points[0].1,
                    right: points[1].0,
                    bottom: points[1].1,
                });
            }
        }

        Err(PyTypeError::new_err("unsupported bbox shape"))
    }
}

#[derive(Clone, Debug)]
struct GenericWordInput {
    text: String,
    bbox: Option<PyBboxInput>,
    confidence: f64,
}

impl<'a, 'py> FromPyObject<'a, 'py> for GenericWordInput {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let dict = ob.cast::<PyDict>()?;
        let text = dict
            .get_item("text")?
            .ok_or_else(|| PyTypeError::new_err("word.text missing"))?
            .extract::<String>()?;
        let bbox = match dict.get_item("bbox")? {
            Some(value) => Some(value.extract::<PyBboxInput>()?),
            None => None,
        };
        let confidence = dict
            .get_item("confidence")?
            .and_then(|value| value.extract::<f64>().ok())
            .unwrap_or(0.0);
        Ok(Self {
            text,
            bbox,
            confidence,
        })
    }
}

#[derive(Clone, FromPyObject)]
struct GenericLineInput {
    #[pyo3(item("text"))]
    text: String,
    #[pyo3(item("words"))]
    words: Vec<GenericWordInput>,
}

#[derive(Clone, FromPyObject)]
struct GenericPageInput {
    #[pyo3(item("lines"))]
    lines: Vec<GenericLineInput>,
}

fn to_helper_pages(pages: Vec<GenericPageInput>) -> Vec<receipt_parse_helpers::MerchantPageInput> {
    pages
        .into_iter()
        .map(|page| receipt_parse_helpers::MerchantPageInput {
            lines: page
                .lines
                .into_iter()
                .map(|line| receipt_parse_helpers::MerchantLineInput {
                    text: line.text,
                    words: line
                        .words
                        .into_iter()
                        .map(|word| receipt_parse_helpers::MerchantWordInput {
                            confidence: word.confidence,
                            has_bbox: word.bbox.is_some(),
                        })
                        .collect(),
                })
                .collect(),
        })
        .collect()
}

fn to_spatial_pages(pages: Vec<GenericPageInput>) -> Vec<receipt_spatial::PageInput> {
    pages
        .into_iter()
        .map(|page| receipt_spatial::PageInput {
            lines: page
                .lines
                .into_iter()
                .map(|line| receipt_spatial::LineInput {
                    text: line.text,
                    words: line
                        .words
                        .into_iter()
                        .map(|word| {
                            let bbox = word.bbox.unwrap_or(PyBboxInput {
                                left: 0.0,
                                top: 0.0,
                                right: 0.0,
                                bottom: 0.0,
                            });
                            receipt_spatial::WordInput {
                                text: word.text,
                                bbox: receipt_spatial::BboxInput {
                                    left: bbox.left,
                                    top: bbox.top,
                                    right: bbox.right,
                                    bottom: bbox.bottom,
                                },
                                confidence: word.confidence,
                            }
                        })
                        .collect(),
                })
                .collect(),
        })
        .collect()
}

/// Parse the raw PaddleOCR `detections` list (`[[points], [text, conf]]`) into
/// `receipt-core`'s `RawDetection`. Points arrive as `[x, y]` lists in
/// padded-image pixel coordinates.
fn extract_raw_detections(detections: &Bound<'_, PyAny>) -> PyResult<Vec<RawDetection>> {
    let list = detections.cast::<PyList>()?;
    let mut out = Vec::with_capacity(list.len());
    for detection in list.try_iter()? {
        let detection = detection?;
        let points = detection
            .get_item(0)?
            .extract::<Vec<Vec<f64>>>()?
            .into_iter()
            .filter(|p| p.len() >= 2)
            .map(|p| (p[0], p[1]))
            .collect::<Vec<_>>();
        let text_conf = detection.get_item(1)?;
        let text = text_conf.get_item(0)?.extract::<String>()?;
        let confidence = text_conf.get_item(1)?.extract::<f64>().unwrap_or(0.0);
        out.push(RawDetection {
            points,
            text,
            confidence,
        });
    }
    Ok(out)
}

/// Serialize a parsed receipt into the dict schema the Python `parse_receipt`
/// mapping consumes. Shared by the transformed-input and raw-input bindings.
fn parsed_to_pydict(py: Python<'_>, parsed: receipt_parser::ParsedReceiptData) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("merchant", parsed.merchant)?;
    dict.set_item("date", parsed.date)?;
    dict.set_item("date_is_placeholder", parsed.date_is_placeholder)?;
    dict.set_item("total", parsed.total)?;
    dict.set_item(
        "items",
        parsed
            .items
            .into_iter()
            .map(|item| (item.description, item.price, item.quantity, item.category))
            .collect::<Vec<_>>(),
    )?;
    dict.set_item("tax", parsed.tax)?;
    dict.set_item("subtotal", parsed.subtotal)?;
    dict.set_item("raw_text", parsed.raw_text)?;
    dict.set_item("image_filename", parsed.image_filename)?;
    dict.set_item(
        "warnings",
        parsed
            .warnings
            .into_iter()
            .map(|warning| (warning.message, warning.after_item_index))
            .collect::<Vec<_>>(),
    )?;
    dict.set_item(
        "tenders",
        parsed
            .tenders
            .into_iter()
            .map(|tender| (tender.amount, tender.account, tender.kind, tender.raw_label))
            .collect::<Vec<_>>(),
    )?;
    Ok(dict.unbind())
}

#[pyfunction]
fn receipt_parse_receipt(
    py: Python<'_>,
    ocr_result: &Bound<'_, PyAny>,
    rule_layers: PyRuleLayersInput,
    image_filename: String,
    known_merchants: Option<Vec<String>>,
    current_year: i32,
) -> PyResult<Py<PyDict>> {
    let ocr_dict = ocr_result
        .cast::<PyDict>()
        .map_err(|_| PyTypeError::new_err("ocr_result must be a dict"))?;
    let full_text = match ocr_dict.get_item("full_text")? {
        Some(value) => value.extract::<String>()?,
        None => String::new(),
    };
    let pages = match ocr_dict.get_item("pages")? {
        Some(value) => value.extract::<Vec<GenericPageInput>>()?,
        None => Vec::new(),
    };

    let parsed = receipt_parser::parse_receipt(
        &full_text,
        &to_helper_pages(pages.clone()),
        &to_spatial_pages(pages),
        &to_rule_layers(rule_layers),
        &image_filename,
        &known_merchants.unwrap_or_default(),
        current_year,
    );

    parsed_to_pydict(py, parsed)
}

/// Like [`receipt_parse_receipt`] but takes the *raw* OCR result
/// (`{image_width, image_height, detections}`) and runs the detection→parser
/// transform in Rust (`receipt-core`), so no Python `transform_paddleocr_result`
/// is needed. This is the desktop live path, unified with the iOS pipeline.
#[pyfunction]
#[pyo3(signature = (raw_result, rule_layers, image_filename, known_merchants, current_year, padding = 50))]
fn receipt_parse_receipt_from_raw(
    py: Python<'_>,
    raw_result: &Bound<'_, PyAny>,
    rule_layers: PyRuleLayersInput,
    image_filename: String,
    known_merchants: Option<Vec<String>>,
    current_year: i32,
    padding: i64,
) -> PyResult<Py<PyDict>> {
    let dict = raw_result
        .cast::<PyDict>()
        .map_err(|_| PyTypeError::new_err("raw_result must be a dict"))?;
    let padded_width = dict
        .get_item("image_width")?
        .ok_or_else(|| PyTypeError::new_err("raw_result.image_width missing"))?
        .extract::<i64>()?;
    let padded_height = dict
        .get_item("image_height")?
        .ok_or_else(|| PyTypeError::new_err("raw_result.image_height missing"))?
        .extract::<i64>()?;
    let detections = match dict.get_item("detections")? {
        Some(value) => extract_raw_detections(&value)?,
        None => Vec::new(),
    };

    let transformed = ocr_transform::transform(detections, padded_width, padded_height, padding);
    let parsed = receipt_parser::parse_receipt(
        &transformed.full_text,
        &transformed.helper_pages,
        &transformed.spatial_pages,
        &to_rule_layers(rule_layers),
        &image_filename,
        &known_merchants.unwrap_or_default(),
        current_year,
    );

    parsed_to_pydict(py, parsed)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_parse_receipt, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_parse_receipt_from_raw, module)?)?;
    Ok(())
}
