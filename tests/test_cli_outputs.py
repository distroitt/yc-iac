from pathlib import Path
import json

from typer.testing import CliRunner

from iac_tool.cli import app
from iac_tool.exceptions import CloudProviderError, ResourceNotFoundError
from iac_tool.state import InfrastructureState, ResourceState, StateStore


class FakeOutputsFacade:
    def __init__(self, payloads: dict[str, dict[str, object]], missing: set[str] | None = None) -> None:
        self.payloads = payloads
        self.missing = missing or set()
        self.calls: list[str] = []

    def create_network(self, folder_id: str, name: str, labels: dict[str, str]) -> str:
        self.calls.append("create_network")
        return "id-network"

    def describe_network(self, resource_id: str) -> dict[str, object]:
        if resource_id in self.missing:
            raise ResourceNotFoundError(f"Resource '{resource_id}' is missing")
        return self.payloads[resource_id]


def _write_network_manifest(tmp_path: Path) -> Path:
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


def _write_network_state(manifest_path: Path, resource_id: str) -> None:
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


def test_outputs_command_prints_human_readable_outputs(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_network_manifest(tmp_path)
    _write_network_state(manifest_path, "id-network")
    facade = FakeOutputsFacade({"id-network": {"name": "demo-network", "labels": {}}})
    monkeypatch.setattr("iac_tool.cli.load_auth_config", lambda path=None: object())
    monkeypatch.setattr("iac_tool.cli.YandexCloudFacade", lambda auth_config: facade)

    result = runner.invoke(app, ["outputs", str(manifest_path)])

    assert result.exit_code == 0
    assert "Outputs:" in result.stdout
    assert "available" in result.stdout
    assert 'name: "demo-network"' in result.stdout


def test_outputs_command_outputs_json_and_missing_in_cloud(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_network_manifest(tmp_path)
    _write_network_state(manifest_path, "id-network")
    facade = FakeOutputsFacade({}, missing={"id-network"})
    monkeypatch.setattr("iac_tool.cli.load_auth_config", lambda path=None: object())
    monkeypatch.setattr("iac_tool.cli.YandexCloudFacade", lambda auth_config: facade)

    result = runner.invoke(app, ["outputs", str(manifest_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["available_count"] == 0
    assert payload["resources"][0]["status"] == "missing_in_cloud"


def test_apply_prints_live_outputs_after_success(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_network_manifest(tmp_path)
    facade = FakeOutputsFacade({"id-network": {"name": "demo-network", "labels": {}}})
    monkeypatch.setattr("iac_tool.cli.load_auth_config", lambda path=None: object())
    monkeypatch.setattr("iac_tool.cli.YandexCloudFacade", lambda auth_config: facade)

    result = runner.invoke(app, ["apply", str(manifest_path), "--confirm"])

    assert result.exit_code == 0
    assert "[1/1] create network:network ..." in result.stdout
    assert "[1/1] create network:network completed." in result.stdout
    assert "Apply completed." in result.stdout
    assert "Live outputs:" in result.stdout
    assert "available" in result.stdout
    assert 'name: "demo-network"' in result.stdout


def test_apply_keeps_success_status_when_output_collection_warns(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_network_manifest(tmp_path)

    class WarningFacade(FakeOutputsFacade):
        def describe_network(self, resource_id: str) -> dict[str, object]:
            raise CloudProviderError("temporary output lookup failure")

    facade = WarningFacade({})
    monkeypatch.setattr("iac_tool.cli.load_auth_config", lambda path=None: object())
    monkeypatch.setattr("iac_tool.cli.YandexCloudFacade", lambda auth_config: facade)

    result = runner.invoke(app, ["apply", str(manifest_path), "--confirm"])

    assert result.exit_code == 0
    assert "Apply completed." in result.stdout
    assert "Live outputs:" in result.stdout
    assert "error" in result.stdout
    assert "Warnings:" in result.stdout
