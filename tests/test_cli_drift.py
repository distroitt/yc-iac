from pathlib import Path
import json

from typer.testing import CliRunner

from iac_tool.cli import app
from iac_tool.state import InfrastructureState, ResourceState, StateStore


class FakeFacade:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads

    def describe_network(self, resource_id: str) -> dict[str, object]:
        return self.payloads[resource_id]


def _write_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

network:
  logical_name: "network"
  name: "demo-network"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _write_state(manifest_path: Path, resource_id: str) -> None:
    store = StateStore.for_manifest(manifest_path)
    state = InfrastructureState()
    state.put(
        ResourceState(
            logical_name="network",
            resource_type="network",
            resource_id=resource_id,
            config_hash="hash-network",
            dependencies=[],
        ),
    )
    store.save(state)


def test_drift_detect_command_reports_clean_state(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_manifest(tmp_path)
    _write_state(manifest_path, "id-network")
    facade = FakeFacade(
        {
            "id-network": {
                "name": "demo-network",
                "labels": {},
            },
        },
    )
    monkeypatch.setattr("iac_tool.cli.load_auth_config", lambda path=None: object())
    monkeypatch.setattr("iac_tool.cli.YandexCloudFacade", lambda auth_config: facade)

    result = runner.invoke(app, ["drift-detect", str(manifest_path)])

    assert result.exit_code == 0
    assert "Drift summary:" in result.stdout
    assert "in_sync" in result.stdout
    assert "No drift detected." in result.stdout


def test_drift_detect_command_returns_exit_code_2_and_json_on_drift(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_manifest(tmp_path)
    _write_state(manifest_path, "id-network")
    facade = FakeFacade(
        {
            "id-network": {
                "name": "changed-network",
                "labels": {},
            },
        },
    )
    monkeypatch.setattr("iac_tool.cli.load_auth_config", lambda path=None: object())
    monkeypatch.setattr("iac_tool.cli.YandexCloudFacade", lambda auth_config: facade)

    result = runner.invoke(app, ["drift-detect", str(manifest_path), "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["has_drift"] is True
    assert payload["drift_count"] == 1
    assert payload["findings"][0]["status"] == "drifted"
