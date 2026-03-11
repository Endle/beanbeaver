"""Typed service layer for machine-readable statement import flows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from beanbeaver.application.imports import chequing as chequing_import
from beanbeaver.application.imports import credit_card as credit_card_import
from beanbeaver.application.imports.csv_routing import (
    CsvRoute,
    ImportType,
    find_download_routes,
    route_csv,
)
from beanbeaver.application.imports.shared import check_uncommitted_changes, downloads_display_path


@dataclass(frozen=True)
class ImportRouteOption:
    csv_file: str
    source_path: Path
    import_type: ImportType
    importer_id: str
    rule_id: str
    stage: int


@dataclass(frozen=True)
class PlanImportResult:
    status: Literal["ready", "needs_selection", "error"]
    has_uncommitted_changes: bool
    route: ImportRouteOption | None = None
    route_options: list[ImportRouteOption] | None = None
    error: str | None = None


@dataclass(frozen=True)
class ResolveImportAccountsResult:
    status: Literal["ready", "error"]
    import_type: ImportType
    csv_file: str
    importer_id: str
    account_label: str | None = None
    account_options: list[str] | None = None
    as_of: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ApplyImportRequest:
    import_type: ImportType
    csv_file: str
    importer_id: str | None = None
    selected_account: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    allow_uncommitted: bool | None = None


@dataclass(frozen=True)
class ApplyImportResult:
    status: str
    import_type: ImportType
    result_file_path: Path | None = None
    result_file_name: str | None = None
    account: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    error: str | None = None


def _resolve_source_path(csv_file: str) -> Path:
    downloads_candidate = Path(downloads_display_path()) / csv_file
    if downloads_candidate.exists():
        return downloads_candidate.resolve()
    candidate = Path(csv_file).expanduser()
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(csv_file)


def _route_option(route: CsvRoute, *, source_path: Path | None = None) -> ImportRouteOption:
    resolved_source = source_path or _resolve_source_path(route.file_name)
    return ImportRouteOption(
        csv_file=route.file_name,
        source_path=resolved_source,
        import_type=route.import_type,
        importer_id=route.importer_id,
        rule_id=route.rule_id,
        stage=route.stage,
    )


def plan_import(*, import_type: ImportType | None = None, csv_file: str | None = None) -> PlanImportResult:
    has_uncommitted_changes = check_uncommitted_changes()
    if csv_file is None:
        routes = find_download_routes()
        if import_type is not None:
            routes = [route for route in routes if route.import_type == import_type]
        if not routes:
            return PlanImportResult(
                status="error",
                has_uncommitted_changes=has_uncommitted_changes,
                error=(
                    f"No matching CSV files found in {downloads_display_path()}. "
                    "Expected patterns: credit card or chequing CSVs."
                ),
            )
        options = [_route_option(route) for route in routes]
        if len(options) == 1:
            return PlanImportResult(
                status="ready",
                has_uncommitted_changes=has_uncommitted_changes,
                route=options[0],
            )
        return PlanImportResult(
            status="needs_selection",
            has_uncommitted_changes=has_uncommitted_changes,
            route_options=options,
        )

    try:
        source_path = _resolve_source_path(csv_file)
    except FileNotFoundError:
        return PlanImportResult(
            status="error",
            has_uncommitted_changes=has_uncommitted_changes,
            error=f"File not found: {csv_file}",
        )

    routes = route_csv(source_path)
    if import_type is not None:
        routes = [route for route in routes if route.import_type == import_type]
    if not routes:
        return PlanImportResult(
            status="error",
            has_uncommitted_changes=has_uncommitted_changes,
            error=f"Could not determine import route for CSV: {source_path.name}",
        )
    options = [_route_option(route, source_path=source_path) for route in routes]
    if len(options) == 1:
        return PlanImportResult(
            status="ready",
            has_uncommitted_changes=has_uncommitted_changes,
            route=options[0],
        )
    return PlanImportResult(
        status="needs_selection",
        has_uncommitted_changes=has_uncommitted_changes,
        route_options=options,
    )


def resolve_import_accounts(
    *,
    import_type: ImportType,
    csv_file: str,
    importer_id: str | None = None,
) -> ResolveImportAccountsResult:
    try:
        if import_type == "cc":
            options = credit_card_import.resolve_credit_card_account_options(
                csv_file,
                importer_id=None if importer_id is None else importer_id,  # type: ignore[arg-type]
            )
            return ResolveImportAccountsResult(
                status="ready",
                import_type=import_type,
                csv_file=csv_file,
                importer_id=options.importer_id,
                account_label=options.account_label,
                account_options=options.account_options,
                as_of=None if options.as_of is None else options.as_of.isoformat(),
            )

        options = chequing_import.resolve_chequing_account_options(csv_file)
        return ResolveImportAccountsResult(
            status="ready",
            import_type=import_type,
            csv_file=csv_file,
            importer_id=options.chequing_type,
            account_label=options.account_label,
            account_options=options.account_options,
            as_of=None if options.as_of is None else options.as_of.isoformat(),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        return ResolveImportAccountsResult(
            status="error",
            import_type=import_type,
            csv_file=csv_file,
            importer_id=importer_id or "",
            error=str(exc),
        )


def apply_import(request: ApplyImportRequest) -> ApplyImportResult:
    if request.import_type == "cc":
        result = credit_card_import.run_credit_card_import(
            credit_card_import.CreditCardImportRequest(
                csv_file=request.csv_file,
                start_date=request.start_date,
                end_date=request.end_date,
                importer_id=None if request.importer_id is None else request.importer_id,  # type: ignore[arg-type]
                selected_account=request.selected_account,
                allow_uncommitted=request.allow_uncommitted,
            )
        )
        return ApplyImportResult(
            status=result.status,
            import_type=request.import_type,
            result_file_path=result.result_file_path,
            result_file_name=result.result_file_name,
            account=result.card_account,
            start_date=result.start_date,
            end_date=result.end_date,
            error=result.error,
        )

    result = chequing_import.run_chequing_import(
        chequing_import.ChequingImportRequest(
            csv_file=request.csv_file,
            selected_account=request.selected_account,
            allow_uncommitted=request.allow_uncommitted,
        )
    )
    return ApplyImportResult(
        status=result.status,
        import_type=request.import_type,
        result_file_path=result.result_file_path,
        result_file_name=result.result_file_name,
        account=result.account,
        start_date=result.start_date,
        end_date=result.end_date,
        error=result.error,
    )
