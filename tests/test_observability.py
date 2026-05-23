from pathlib import Path

from typer.testing import CliRunner

from iac_tool.cli import _diagnostic_hints, app
from iac_tool.observability import format_exception_chain


def _write_manifest(tmp_path: Path) -> Path:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

network:
  logical_name: "network"
  name: "demo-network"

subnet:
  logical_name: "subnet"
  name: "demo-subnet"
  network: "network"
  cidr: "10.10.0.0/24"

instance:
  logical_name: "instance"
  name: "demo-instance"
  subnet: "subnet"
  username: "yc-user"
  ssh_public_key_path: "{ssh_key}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_format_exception_chain_includes_causes() -> None:
    try:
        try:
            raise ValueError("inner cause")
        except ValueError as exc:
            raise RuntimeError("outer failure") from exc
    except RuntimeError as exc:
        message = format_exception_chain(exc)

    assert message == "outer failure | caused by: inner cause"


def test_format_exception_chain_collapses_nested_duplicates() -> None:
    try:
        try:
            try:
                raise RuntimeError("root cause")
            except RuntimeError as exc:
                raise RuntimeError(f"middle context: {exc}") from exc
        except RuntimeError as exc:
            raise RuntimeError(f"top context: {exc}") from exc
    except RuntimeError as exc:
        message = format_exception_chain(exc)

    assert message == "top context: middle context: root cause"


def test_validate_writes_detailed_logs_to_file(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_manifest(tmp_path)
    log_path = tmp_path / "iac-tool.log"

    result = runner.invoke(app, ["--log-file", str(log_path), "validate", str(manifest_path)])

    assert result.exit_code == 0
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "Validating manifest" in content
    assert "is valid; state file is" in content


def test_resource_exhausted_external_address_error_gets_actionable_hint() -> None:
    hints = _diagnostic_hints(
        "Cloud operation failed with code 8: RESOURCE_EXHAUSTED: "
        "Quota limit vpc.externalAddressesCreation.rate exceeded",
    )

    assert "rate-limited public IPv4 allocation" in hints[0]
    assert "assign_public_ip: false" in hints[0]
