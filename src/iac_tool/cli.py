from __future__ import annotations

import json
from pathlib import Path

import typer

from .auth import load_auth_config
from .commands import PlanCommand
from .drift import DriftReport, DriftDetector
from .exceptions import IaCToolError
from .executor import PlanExecutor
from .facade import YandexCloudFacade
from .graphing import render_dependency_graph, write_dependency_graph
from .manifest import Manifest, load_manifest
from .observability import configure_logging, format_exception_chain, get_logger
from .outputs import OutputsCollector, OutputsReport
from .planner import ChangeKind, ExecutionPlan, Planner
from .state import InfrastructureState, StateStore


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Declarative IaC tool for Yandex Cloud based on the official Python SDK.",
)
logger = get_logger("cli")


@app.callback()
def main(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable detailed diagnostic logging to stderr.",
    ),
    log_file: Path | None = typer.Option(
        None,
        "--log-file",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to a log file that receives detailed diagnostics.",
    ),
) -> None:
    """Configure shared CLI options."""
    configure_logging(verbose=verbose, log_file=log_file)
    if log_file is not None:
        logger.info("Detailed logs will be written to %s", log_file.expanduser().resolve())


def _load_manifest_and_state(manifest_path: Path, state_file: Path | None) -> tuple[Manifest, StateStore]:
    manifest = load_manifest(manifest_path)
    store = StateStore.for_manifest(manifest_path, state_file)
    return manifest, store


def _print_manifest_summary(manifest: Manifest, state_path: Path) -> None:
    typer.echo("Manifest is valid.")
    typer.echo(f"Provider folder: {manifest.provider.folder_id}")
    typer.echo(f"Zone: {manifest.provider.zone_id}")
    typer.echo(f"Project: {manifest.provider.project_name}")
    typer.echo(f"State file: {state_path}")
    typer.echo(
        "Resources: "
        f"networks={len(manifest.networks)}, "
        f"security_groups={len(manifest.security_groups)}, "
        f"subnets={len(manifest.subnets)}, "
        f"disks={len(manifest.disks)}, "
        f"instances={len(manifest.instances)}",
    )


def _print_plan(plan: ExecutionPlan) -> None:
    typer.echo("Plan summary:")
    for change in plan.changes:
        dependencies = ", ".join(change.dependencies) if change.dependencies else "-"
        typer.echo(
            f"  - {change.kind.value:<7} {change.resource_type}:{change.logical_name} "
            f"(deps: {dependencies}; reason: {change.reason})",
        )
    typer.echo(f"Executable commands: {len(plan.commands)}")


def _resolve_state_store(manifest: Path | None, state_file: Path | None) -> StateStore:
    if state_file is not None:
        return StateStore(state_file)
    if manifest is None:
        raise typer.BadParameter("Provide either a manifest path or --state-file.")
    if not manifest.exists():
        raise typer.BadParameter(f"Manifest file does not exist: {manifest}")
    if manifest.is_dir():
        raise typer.BadParameter(f"Manifest path must point to a file: {manifest}")
    return StateStore.for_manifest(manifest)


def _print_state(state: InfrastructureState, state_path: Path) -> None:
    typer.echo(f"State file: {state_path}")
    typer.echo(f"Version: {state.version}")
    typer.echo(f"Managed resources: {len(state.resources)}")
    if not state.resources:
        typer.echo("State is empty. No managed resources found.")
        return

    typer.echo("Resources:")
    for logical_name, resource in sorted(state.resources.items()):
        dependencies = ", ".join(resource.dependencies) if resource.dependencies else "-"
        typer.echo(f"  - {resource.resource_type}:{logical_name}")
        typer.echo(f"    id: {resource.resource_id}")
        typer.echo(f"    config_hash: {resource.config_hash}")
        typer.echo(f"    dependencies: {dependencies}")
        if resource.metadata:
            typer.echo(f"    metadata: {json.dumps(resource.metadata, ensure_ascii=True, sort_keys=True)}")


def _print_drift_report(report: DriftReport, state_path: Path) -> None:
    typer.echo(f"State file: {state_path}")
    typer.echo(f"Resources inspected: {len(report.findings)}")
    if not report.findings:
        typer.echo("No managed resources were found in the manifest or state.")
        return

    typer.echo("Drift summary:")
    for finding in report.findings:
        suffix = f" id={finding.resource_id}" if finding.resource_id else ""
        typer.echo(f"  - {finding.status.value:<17} {finding.resource_type}:{finding.logical_name}{suffix}")
        for detail in finding.details:
            typer.echo(f"    detail: {detail}")

    if report.has_drift:
        typer.echo(f"Resources with drift: {report.drift_count}")
        return
    typer.echo("No drift detected.")


def _print_outputs_report(report: OutputsReport, state_path: Path) -> None:
    typer.echo(f"State file: {state_path}")
    typer.echo(f"Resources reported: {len(report.resources)}")
    if not report.resources:
        typer.echo("No managed resources were found in the manifest.")
        return

    typer.echo("Outputs:")
    for resource in report.resources:
        suffix = f" id={resource.resource_id}" if resource.resource_id else ""
        typer.echo(f"  - {resource.status.value:<16} {resource.resource_type}:{resource.logical_name}{suffix}")
        for key, value in resource.outputs.items():
            typer.echo(f"    {key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}")
        for detail in resource.details:
            typer.echo(f"    detail: {detail}")

    if report.warnings:
        typer.echo("Warnings:")
        for warning in report.warnings:
            typer.echo(f"  - {warning}")


def _print_outputs_after_apply(
    manifest: Manifest,
    store: StateStore,
    facade: YandexCloudFacade,
) -> None:
    try:
        report = OutputsCollector.from_manifest(manifest).collect(
            store.load(),
            facade,
            continue_on_error=True,
        )
    except IaCToolError as exc:
        typer.secho(
            f"Warning: unable to collect live outputs after apply: {format_exception_chain(exc)}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return

    typer.echo("Live outputs:")
    _print_outputs_report(report, store.path)


def _print_execution_progress(event: str, index: int, total: int, command: PlanCommand) -> None:
    if event == "start":
        typer.echo(f"[{index}/{total}] {command.description()} ...")
        return
    if event == "done":
        typer.echo(f"[{index}/{total}] {command.description()} completed.")
        return
    if event == "failed":
        typer.secho(f"[{index}/{total}] {command.description()} failed.", fg=typer.colors.RED, err=True)


def _ensure_confirm(confirm: bool, action: str) -> None:
    if not confirm:
        raise typer.BadParameter(
            f"Refusing to {action} without --confirm. This guard prevents accidental infrastructure changes.",
        )


def _handle_cli_error(exc: IaCToolError) -> None:
    message = format_exception_chain(exc)
    logger.exception("Command failed: %s", message)
    typer.secho(message, fg=typer.colors.RED, err=True)
    typer.secho(
        "Tip: rerun with --verbose or add --log-file ./iac-tool.log for detailed diagnostics.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=1) from exc


@app.command()
def validate(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the infrastructure manifest in YAML format.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to state.json. Defaults to manifest directory/state.json.",
    ),
) -> None:
    """Validate the infrastructure manifest."""
    try:
        logger.info("Validating manifest %s", manifest)
        loaded_manifest, store = _load_manifest_and_state(manifest, state_file)
        logger.info("Manifest %s is valid; state file is %s", manifest, store.path)
        _print_manifest_summary(loaded_manifest, store.path)
    except IaCToolError as exc:
        _handle_cli_error(exc)


@app.command()
def plan(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the infrastructure manifest in YAML format.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to state.json. Defaults to manifest directory/state.json.",
    ),
) -> None:
    """Build a declarative infrastructure plan."""
    try:
        logger.info("Planning manifest %s", manifest)
        loaded_manifest, store = _load_manifest_and_state(manifest, state_file)
        planner = Planner.from_manifest(loaded_manifest)
        execution_plan = planner.build_apply_plan(store.load())
        logger.info("Built apply plan for %s with %d executable commands", manifest, len(execution_plan.commands))
        _print_plan(execution_plan)
        if execution_plan.is_noop:
            typer.echo("No infrastructure changes are required.")
    except IaCToolError as exc:
        _handle_cli_error(exc)


@app.command()
def state(
    manifest: Path | None = typer.Argument(
        None,
        help="Optional path to the infrastructure manifest. Used to resolve the default state.json location.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        dir_okay=False,
        resolve_path=True,
        help="Explicit path to state.json. If omitted, the path is derived from the manifest directory.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the raw state as JSON.",
    ),
) -> None:
    """Show the current local infrastructure state."""
    try:
        store = _resolve_state_store(manifest, state_file)
        logger.info("Reading state from %s", store.path)
        loaded_state = store.load()
        if as_json:
            typer.echo(json.dumps(loaded_state.model_dump(mode="json"), indent=2, ensure_ascii=True))
            return
        _print_state(loaded_state, store.path)
    except typer.BadParameter as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except IaCToolError as exc:
        _handle_cli_error(exc)


@app.command()
def graph(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the infrastructure manifest in YAML format.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        dir_okay=False,
        resolve_path=True,
        help="Optional file path for Graphviz DOT output.",
    ),
) -> None:
    """Generate a dependency graph in Graphviz DOT format."""
    try:
        logger.info("Generating dependency graph for manifest %s", manifest)
        loaded_manifest = load_manifest(manifest)
        content = render_dependency_graph(loaded_manifest)
        if output is None:
            typer.echo(content, nl=False)
            return
        target = write_dependency_graph(output, content)
        typer.echo(f"Graph written to: {target}")
    except IaCToolError as exc:
        _handle_cli_error(exc)


@app.command()
def drift_detect(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the infrastructure manifest in YAML format.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to state.json. Defaults to manifest directory/state.json.",
    ),
    auth_config: Path | None = typer.Option(
        None,
        "--auth-config",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to local JSON authentication settings.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the drift report as JSON.",
    ),
) -> None:
    """Detect drift between manifest/state and the real cloud."""
    try:
        logger.info("Detecting drift for manifest %s", manifest)
        loaded_manifest, store = _load_manifest_and_state(manifest, state_file)
        detector = DriftDetector.from_manifest(loaded_manifest)
        report = detector.detect(store.load(), YandexCloudFacade(load_auth_config(auth_config)))
        logger.info(
            "Drift detection completed for %s: inspected=%d drifted=%d",
            manifest,
            len(report.findings),
            report.drift_count,
        )
        if as_json:
            typer.echo(
                json.dumps(
                    {
                        "state_file": str(store.path),
                        "findings": report.model_dump(mode="json")["findings"],
                        "has_drift": report.has_drift,
                        "drift_count": report.drift_count,
                        "in_sync_count": report.in_sync_count,
                    },
                    indent=2,
                    ensure_ascii=True,
                ),
            )
        else:
            _print_drift_report(report, store.path)
        if report.has_drift:
            raise typer.Exit(code=2)
    except IaCToolError as exc:
        _handle_cli_error(exc)


@app.command()
def outputs(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the infrastructure manifest in YAML format.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to state.json. Defaults to manifest directory/state.json.",
    ),
    auth_config: Path | None = typer.Option(
        None,
        "--auth-config",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to local JSON authentication settings.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print outputs as JSON.",
    ),
) -> None:
    """Show standard live outputs for managed resources."""
    try:
        logger.info("Collecting live outputs for manifest %s", manifest)
        loaded_manifest, store = _load_manifest_and_state(manifest, state_file)
        report = OutputsCollector.from_manifest(loaded_manifest).collect(
            store.load(),
            YandexCloudFacade(load_auth_config(auth_config)),
        )
        logger.info("Collected outputs for %s: resources=%d", manifest, len(report.resources))
        if as_json:
            typer.echo(
                json.dumps(
                    {
                        "state_file": str(store.path),
                        "resources": report.model_dump(mode="json")["resources"],
                        "warnings": report.warnings,
                        "available_count": report.available_count,
                    },
                    indent=2,
                    ensure_ascii=True,
                ),
            )
            return
        _print_outputs_report(report, store.path)
    except IaCToolError as exc:
        _handle_cli_error(exc)


@app.command()
def apply(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the infrastructure manifest in YAML format.",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Required safety flag that allows infrastructure changes.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to state.json. Defaults to manifest directory/state.json.",
    ),
    auth_config: Path | None = typer.Option(
        None,
        "--auth-config",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to local JSON authentication settings.",
    ),
) -> None:
    """Apply the desired infrastructure state."""
    try:
        _ensure_confirm(confirm, "apply changes")
        logger.info("Applying manifest %s", manifest)
        loaded_manifest, store = _load_manifest_and_state(manifest, state_file)
        planner = Planner.from_manifest(loaded_manifest)
        execution_plan = planner.build_apply_plan(store.load())
        logger.info("Apply plan for %s contains %d commands", manifest, len(execution_plan.commands))
        _print_plan(execution_plan)
        if execution_plan.is_noop:
            typer.echo("Infrastructure is already up to date.")
            return

        facade = YandexCloudFacade(load_auth_config(auth_config))
        executor = PlanExecutor(facade, store, progress_callback=_print_execution_progress)
        executed = executor.execute(execution_plan)
        logger.info("Apply completed for %s", manifest)
        typer.echo("Apply completed.")
        for item in executed:
            typer.echo(f"  - {item}")
        _print_outputs_after_apply(loaded_manifest, store, facade)
    except typer.BadParameter as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except IaCToolError as exc:
        _handle_cli_error(exc)


@app.command()
def destroy(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the infrastructure manifest in YAML format.",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Required safety flag that allows infrastructure deletion.",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to state.json. Defaults to manifest directory/state.json.",
    ),
    auth_config: Path | None = typer.Option(
        None,
        "--auth-config",
        dir_okay=False,
        resolve_path=True,
        help="Optional path to local JSON authentication settings.",
    ),
) -> None:
    """Delete infrastructure described by the current state."""
    try:
        _ensure_confirm(confirm, "destroy resources")
        logger.info("Destroying infrastructure for manifest %s", manifest)
        loaded_manifest, store = _load_manifest_and_state(manifest, state_file)
        planner = Planner.from_manifest(loaded_manifest)
        execution_plan = planner.build_destroy_plan(store.load())
        logger.info("Destroy plan for %s contains %d commands", manifest, len(execution_plan.commands))
        _print_plan(execution_plan)
        if execution_plan.is_noop:
            typer.echo("State is empty. Nothing to destroy.")
            return

        facade = YandexCloudFacade(load_auth_config(auth_config))
        executor = PlanExecutor(facade, store, progress_callback=_print_execution_progress)
        executed = executor.execute(execution_plan)
        logger.info("Destroy completed for %s", manifest)
        typer.echo("Destroy completed.")
        for item in executed:
            typer.echo(f"  - {item}")
    except typer.BadParameter as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except IaCToolError as exc:
        _handle_cli_error(exc)
