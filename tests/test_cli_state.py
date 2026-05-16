from pathlib import Path
import json

from typer.testing import CliRunner

from iac_tool.cli import app
from iac_tool.state import InfrastructureState, ResourceState, StateStore


def _write_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_state_command_shows_empty_state(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_manifest(tmp_path)

    result = runner.invoke(app, ["state", str(manifest_path)])

    assert result.exit_code == 0
    assert "Managed resources: 0" in result.stdout
    assert "State is empty. No managed resources found." in result.stdout


def test_state_command_shows_human_readable_resources(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_manifest(tmp_path)
    store = StateStore.for_manifest(manifest_path)
    state = InfrastructureState()
    state.put(
        ResourceState(
            logical_name="network",
            resource_type="network",
            resource_id="enp123network",
            config_hash="abc123",
            dependencies=[],
            metadata={"name": "oop-course-network"},
        ),
    )
    state.put(
        ResourceState(
            logical_name="subnet",
            resource_type="subnet",
            resource_id="e9b123subnet",
            config_hash="def456",
            dependencies=["network"],
            metadata={"name": "oop-course-subnet"},
        ),
    )
    store.save(state)

    result = runner.invoke(app, ["state", str(manifest_path)])

    assert result.exit_code == 0
    assert "Managed resources: 2" in result.stdout
    assert "network:network" in result.stdout
    assert "subnet:subnet" in result.stdout
    assert "dependencies: network" in result.stdout


def test_state_command_outputs_json(tmp_path: Path) -> None:
    runner = CliRunner()
    state_path = tmp_path / "custom-state.json"
    state = InfrastructureState()
    state.put(
        ResourceState(
            logical_name="instance",
            resource_type="instance",
            resource_id="fhm123instance",
            config_hash="xyz789",
            dependencies=["subnet"],
            metadata={},
        ),
    )
    StateStore(state_path).save(state)

    result = runner.invoke(app, ["state", "--state-file", str(state_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == 1
    assert payload["resources"]["instance"]["resource_id"] == "fhm123instance"
