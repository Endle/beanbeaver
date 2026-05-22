use std::error::Error;
use std::time::Duration;

type AppResult<T> = Result<T, Box<dyn Error>>;

const SERVE_HOST: &str = "0.0.0.0";
const SERVE_HEALTH_HOST: &str = "127.0.0.1";
const SERVE_PORT: u16 = 8080;
const FAVA_HOST: &str = "127.0.0.1";
const FAVA_PORT: u16 = 5000;
const OCR_CONTAINER_NAME: &str = "beanbeaver-ocr";
const OCR_IMAGE: &str = "ghcr.io/endle/beanbeaver-ocr:latest";
const AUTOSTART_DISABLE_ENV: &str = "BB_TUI_DISABLE_AUTOSTART";
const MAX_RUNTIME_LOG_LINES: usize = 400;
const RECEIPTS_REFRESH_INTERVAL: Duration = Duration::from_secs(3);
const SERVE_REFRESH_INTERVAL: Duration = Duration::from_secs(1);
const FAVA_REFRESH_INTERVAL: Duration = Duration::from_secs(1);
const OCR_REFRESH_INTERVAL: Duration = Duration::from_secs(2);

pub(crate) mod process_util;

mod app;
mod backend;
mod event_loop;
mod model;
mod render;
mod runtime;
mod state;

pub(crate) use app::*;
pub(crate) use backend::*;
pub(crate) use model::*;
pub(crate) use render::*;
pub(crate) use runtime::*;
pub(crate) use state::*;

pub use event_loop::run;

#[cfg(test)]
mod tests {
    use super::event_loop::autostart_disabled_from_env;
    use super::*;

    fn sample_review_detail() -> ShowReceiptResponse {
        ShowReceiptResponse {
            path: "/tmp/review_stage_1.receipt.json".to_string(),
            summary: ReceiptSummary {
                path: "/tmp/review_stage_1.receipt.json".to_string(),
                receipt_dir: "2026-03-07_costco_466_68_ad51".to_string(),
                stage_file: "review_stage_1.receipt.json".to_string(),
                merchant: Some("COSTCO".to_string()),
                date: Some("2026-03-07".to_string()),
                total: Some("466.68".to_string()),
            },
            document: serde_json::json!({
                "receipt": {
                    "merchant": "COSTCO",
                    "date": "2026-03-07",
                    "total": "466.68"
                },
                "items": [],
                "debug": {
                    "ocr_payload": {"detections": []}
                }
            }),
        }
    }

    #[test]
    fn autostart_disabled_from_env_recognises_truthy_values() {
        assert!(!autostart_disabled_from_env(None));
        assert!(!autostart_disabled_from_env(Some("")));
        assert!(!autostart_disabled_from_env(Some("0")));
        assert!(!autostart_disabled_from_env(Some("no")));
        assert!(!autostart_disabled_from_env(Some("false")));

        assert!(autostart_disabled_from_env(Some("1")));
        assert!(autostart_disabled_from_env(Some("true")));
        assert!(autostart_disabled_from_env(Some("TRUE")));
        assert!(autostart_disabled_from_env(Some("yes")));
        assert!(autostart_disabled_from_env(Some(" on ")));
    }

    #[test]
    fn select_ocr_runtime_prefers_podman_when_available() {
        assert_eq!(select_ocr_runtime(true), OcrContainerRuntime::Podman);
        assert_eq!(select_ocr_runtime(false), OcrContainerRuntime::Docker);
    }

    #[test]
    fn suggested_ocr_run_commands_start_detached() {
        let podman = OcrContainerRuntime::Podman.suggested_run_command();
        let docker = OcrContainerRuntime::Docker.suggested_run_command();

        assert!(podman.starts_with("podman run -d "));
        assert!(podman.contains("--replace"));
        assert!(podman.contains(OCR_IMAGE));

        assert!(docker.starts_with("docker run -d "));
        assert!(docker.contains("-p 8001:8000"));
        assert!(docker.contains(OCR_IMAGE));
    }

    #[test]
    fn ocr_action_renders_expected_commands_and_messages() {
        assert_eq!(
            OcrAction::Start.rendered_command(OcrContainerRuntime::Docker),
            "docker start beanbeaver-ocr"
        );
        assert_eq!(
            OcrAction::Restart.rendered_command(OcrContainerRuntime::Podman),
            "podman restart beanbeaver-ocr"
        );
        assert_eq!(
            OcrAction::CreateAndStart.rendered_command(OcrContainerRuntime::Docker),
            OcrContainerRuntime::Docker.suggested_run_command()
        );
        assert_eq!(
            OcrAction::CreateAndStart.success_message(OcrContainerRuntime::Docker),
            "Created and started Docker container `beanbeaver-ocr`"
        );
    }

    #[test]
    fn error_mentions_missing_container_handles_podman_and_docker_messages() {
        assert!(error_mentions_missing_container(
            "",
            "Error: no such container beanbeaver-ocr"
        ));
        assert!(error_mentions_missing_container(
            "",
            "Error response from daemon: No such container: beanbeaver-ocr"
        ));
        assert!(!error_mentions_missing_container(
            "",
            "permission denied while trying to connect to the Docker daemon socket"
        ));
    }

    #[test]
    fn ocr_summary_lines_supports_docker_inspect_shape() {
        let raw = serde_json::json!([
            {
                "Name": "/beanbeaver-ocr",
                "State": {
                    "Status": "running",
                    "Running": true,
                    "ExitCode": 0,
                    "StartedAt": "2026-03-12T10:00:00Z",
                    "FinishedAt": "0001-01-01T00:00:00Z"
                },
                "Config": {
                    "Image": "ghcr.io/endle/beanbeaver-ocr:latest"
                },
                "Created": "2026-03-12T09:59:00Z",
                "Path": "python",
                "Args": ["-m", "app"],
                "NetworkSettings": {
                    "Ports": {
                        "8000/tcp": [
                            {
                                "HostIp": "0.0.0.0",
                                "HostPort": "8001"
                            }
                        ]
                    }
                }
            }
        ]);

        let summary = ocr_summary_lines(&raw.to_string()).expect("docker inspect summary");

        assert!(summary.contains(&"Container: beanbeaver-ocr".to_string()));
        assert!(summary.contains(&"Status: running (running)".to_string()));
        assert!(summary.contains(&"Image: ghcr.io/endle/beanbeaver-ocr:latest".to_string()));
        assert!(summary.contains(&"Ports: 0.0.0.0:8001 -> 8000/tcp".to_string()));
        assert!(summary.contains(&"Command: python -m app".to_string()));
    }

    #[test]
    fn render_detail_lines_scanned_shows_human_readable_summary() {
        let detail = ShowReceiptResponse {
            path: "/tmp/scanned.receipt.json".to_string(),
            summary: ReceiptSummary {
                path: "/tmp/scanned.receipt.json".to_string(),
                receipt_dir: "2026-03-07_costco_466_68_ad51".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("COSTCO".to_string()),
                date: Some("2026-03-07".to_string()),
                total: Some("466.68".to_string()),
            },
            document: serde_json::json!({
                "receipt": {
                    "merchant": "COSTCO",
                    "date": "2026-03-07",
                    "currency": "CAD",
                    "subtotal": "460.96",
                    "tax": "5.72",
                    "total": "466.68"
                },
                "items": [
                    {
                        "description": "810 LCBO CARD",
                        "price": "400.00",
                        "quantity": 1,
                        "classification": {"category": "alcohol"},
                        "warnings": []
                    }
                ],
                "debug": {
                    "ocr_payload": {"detections": []}
                }
            }),
        };

        let lines = render_detail_lines(Queue::Scanned, &detail);
        let rendered = lines.join("\n");

        assert!(rendered.contains("Parsed Receipt"));
        assert!(rendered.contains("Receipt"));
        assert!(rendered.contains("Items (1)"));
        assert!(rendered.contains("810 LCBO CARD  |  $400.00  |  alcohol"));
        assert!(!rendered.contains("Stage JSON"));
        assert!(!rendered.contains("\"debug\""));
    }

    #[test]
    fn render_detail_lines_approved_applies_review_overrides() {
        let detail = ShowReceiptResponse {
            path: "/tmp/review_stage_1.receipt.json".to_string(),
            summary: ReceiptSummary {
                path: "/tmp/review_stage_1.receipt.json".to_string(),
                receipt_dir: "2026-03-07_costco_466_68_ad51".to_string(),
                stage_file: "review_stage_1.receipt.json".to_string(),
                merchant: Some("COSTCO".to_string()),
                date: Some("2026-03-07".to_string()),
                total: Some("466.68".to_string()),
            },
            document: serde_json::json!({
                "receipt": {
                    "merchant": "COSTCO",
                    "date": "2026-03-07",
                    "total": "466.68"
                },
                "review": {
                    "notes": "manual review"
                },
                "items": [
                    {
                        "description": "810 LCBO CARD",
                        "price": "400.00",
                        "classification": {"category": "uncategorized"},
                        "review": {
                            "description": "LCBO",
                            "classification": {"category": "alcohol"},
                            "notes": "gift"
                        },
                        "warnings": []
                    },
                    {
                        "description": "REMOVE ME",
                        "price": "1.00",
                        "review": {"removed": true},
                        "warnings": []
                    }
                ],
                "debug": {
                    "ocr_payload": {"detections": []}
                }
            }),
        };

        let lines = render_detail_lines(Queue::Approved, &detail);
        let rendered = lines.join("\n");

        assert!(rendered.contains("Reviewed Receipt"));
        assert!(rendered.contains("Notes: manual review"));
        assert!(rendered.contains("LCBO  |  $400.00  |  alcohol"));
        assert!(rendered.contains("    Notes: gift"));
        assert!(!rendered.contains("REMOVE ME"));
        assert!(!rendered.contains("\"debug\""));
    }

    #[test]
    fn review_payload_includes_create_patch_for_added_items() {
        let detail = sample_review_detail();
        let mut review_state = ReviewState::from_detail(Queue::Approved, &detail, Vec::new());

        review_state.add_item();
        let item = review_state.items.get_mut(0).expect("new item");
        item.description = "BANANAS".to_string();
        item.price = "3.99".to_string();
        item.category = "Expenses:Food:Grocery:Fruit".to_string();
        item.notes = "manual add".to_string();

        assert_eq!(
            review_state.payload(),
            serde_json::json!({
                "review": {},
                "items": [
                    {
                        "id": "item-added-0001",
                        "create": true,
                        "review": {
                            "description": "BANANAS",
                            "price": "3.99",
                            "category": "Expenses:Food:Grocery:Fruit",
                            "notes": "manual add",
                        }
                    }
                ]
            })
        );
    }

    #[test]
    fn review_payload_ignores_blank_added_item_drafts() {
        let detail = sample_review_detail();
        let mut review_state = ReviewState::from_detail(Queue::Approved, &detail, Vec::new());

        review_state.add_item();

        assert_eq!(
            review_state.payload(),
            serde_json::json!({
                "review": {},
                "items": []
            })
        );
    }

    #[test]
    fn effective_preview_includes_itemized_total() {
        let detail = sample_review_detail();
        let mut review_state = ReviewState::from_detail(Queue::Approved, &detail, Vec::new());

        let tax_field = review_state
            .fields
            .iter_mut()
            .find(|field| field.field == ReceiptReviewField::Tax)
            .expect("tax field");
        tax_field.value = "0.31".to_string();
        review_state.add_item();
        let item = review_state.items.get_mut(0).expect("new item");
        item.description = "BANANAS".to_string();
        item.price = "3.99".to_string();

        let rendered = review_state.effective_preview_lines().join("\n");

        assert!(rendered.contains("Itemized Total: $4.30"));
    }

    #[test]
    fn effective_preview_itemized_total_updates_from_pending_text_input() {
        let detail = sample_review_detail();
        let mut review_state = ReviewState::from_detail(Queue::Approved, &detail, Vec::new());

        review_state.add_item();
        let item = review_state.items.get_mut(0).expect("new item");
        item.description = "BANANAS".to_string();
        item.price = "3.99".to_string();

        review_state.text_input = Some(TextInputState::with_value(
            ReviewEditTarget::ItemPrice(0),
            "Item Price".to_string(),
            "4.25".to_string(),
        ));

        let rendered = review_state.effective_preview_lines().join("\n");

        assert!(rendered.contains("Itemized Total: $4.25"));
        assert!(rendered.contains(" 1. BANANAS [new]  x1  $4.25  [<uncategorized>]"));
    }

    #[test]
    fn sync_selection_to_path_preserves_selected_receipt_across_insertions() {
        let mut app = App::new();
        app.scanned = vec![
            ReceiptSummary {
                path: "/tmp/older.receipt.json".to_string(),
                receipt_dir: "older".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("OLDER".to_string()),
                date: Some("2026-03-09".to_string()),
                total: Some("10.00".to_string()),
            },
            ReceiptSummary {
                path: "/tmp/current.receipt.json".to_string(),
                receipt_dir: "current".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("CURRENT".to_string()),
                date: Some("2026-03-10".to_string()),
                total: Some("20.00".to_string()),
            },
        ];
        app.scanned_state.select(Some(1));

        let selected_path = app.selected_path_for_queue(Queue::Scanned);

        app.scanned = vec![
            ReceiptSummary {
                path: "/tmp/newest.receipt.json".to_string(),
                receipt_dir: "newest".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("NEWEST".to_string()),
                date: Some("2026-03-11".to_string()),
                total: Some("30.00".to_string()),
            },
            ReceiptSummary {
                path: "/tmp/older.receipt.json".to_string(),
                receipt_dir: "older".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("OLDER".to_string()),
                date: Some("2026-03-09".to_string()),
                total: Some("10.00".to_string()),
            },
            ReceiptSummary {
                path: "/tmp/current.receipt.json".to_string(),
                receipt_dir: "current".to_string(),
                stage_file: "parsed.receipt.json".to_string(),
                merchant: Some("CURRENT".to_string()),
                date: Some("2026-03-10".to_string()),
                total: Some("20.00".to_string()),
            },
        ];

        app.sync_selection_to_path(Queue::Scanned, selected_path.as_deref());

        assert_eq!(app.scanned_state.selected(), Some(2));
        assert_eq!(
            app.selected_path_for_queue(Queue::Scanned).as_deref(),
            Some("/tmp/current.receipt.json")
        );
    }

    #[test]
    fn import_page_state_preserves_selected_route_by_source_path() {
        let mut state = ImportPageState::new();
        state.set_routes(
            vec![
                ImportRouteOption {
                    csv_file: "statement.csv".to_string(),
                    source_path: "/tmp/statement.csv".to_string(),
                    import_type: "cc".to_string(),
                    importer_id: "bmo".to_string(),
                    rule_id: "cc-bmo-statement".to_string(),
                    stage: 1,
                },
                ImportRouteOption {
                    csv_file: "Preferred_Package_foo.csv".to_string(),
                    source_path: "/tmp/Preferred_Package_foo.csv".to_string(),
                    import_type: "chequing".to_string(),
                    importer_id: "scotia_chequing".to_string(),
                    rule_id: "chequing-scotia".to_string(),
                    stage: 1,
                },
            ],
            None,
        );
        state.route_state.select(Some(1));

        state.set_routes(
            vec![
                ImportRouteOption {
                    csv_file: "new.csv".to_string(),
                    source_path: "/tmp/new.csv".to_string(),
                    import_type: "cc".to_string(),
                    importer_id: "rogers".to_string(),
                    rule_id: "cc-rogers".to_string(),
                    stage: 1,
                },
                ImportRouteOption {
                    csv_file: "Preferred_Package_foo.csv".to_string(),
                    source_path: "/tmp/Preferred_Package_foo.csv".to_string(),
                    import_type: "chequing".to_string(),
                    importer_id: "scotia_chequing".to_string(),
                    rule_id: "chequing-scotia".to_string(),
                    stage: 1,
                },
            ],
            None,
        );

        assert_eq!(state.route_state.selected(), Some(1));
        assert_eq!(
            state
                .selected_route()
                .map(|route| route.source_path.as_str()),
            Some("/tmp/Preferred_Package_foo.csv")
        );
    }
}
