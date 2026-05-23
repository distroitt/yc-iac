from pathlib import Path

import pytest

from iac_tool.exceptions import StateError
from iac_tool.state import InfrastructureState, ResourceState, StateStore


def test_state_store_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    initial = InfrastructureState()
    initial.put(
        ResourceState(
            logical_name="network",
            resource_type="network",
            resource_id="net-1",
            config_hash="abc",
            config_payload={"name": "demo-network"},
            dependencies=[],
            metadata={"name": "demo-network"},
        ),
    )

    store.save(initial)
    loaded = store.load()

    assert loaded.get("network") is not None
    assert loaded.get("network").resource_id == "net-1"
    assert loaded.get("network").config_payload == {"name": "demo-network"}


def test_state_store_accepts_legacy_resources_without_config_payload(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        """
{
  "version": 1,
  "resources": {
    "network": {
      "logical_name": "network",
      "resource_type": "network",
      "resource_id": "net-1",
      "config_hash": "abc",
      "dependencies": [],
      "metadata": {}
    }
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    loaded = StateStore(state_path).load()

    assert loaded.require("network").config_payload == {}


def test_state_store_rejects_invalid_json(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StateError, match="invalid JSON"):
        StateStore(state_path).load()


def test_state_store_uses_atomic_temporary_file(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)

    store.save(InfrastructureState())

    assert state_path.exists()
    assert not (tmp_path / ".state.json.tmp").exists()
