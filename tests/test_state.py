from pathlib import Path

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
            dependencies=[],
            metadata={"name": "demo-network"},
        ),
    )

    store.save(initial)
    loaded = store.load()

    assert loaded.get("network") is not None
    assert loaded.get("network").resource_id == "net-1"

