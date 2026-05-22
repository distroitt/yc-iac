from pathlib import Path

from iac_tool.executor import PlanExecutor
from iac_tool.manifest import load_manifest
from iac_tool.planner import Planner
from iac_tool.state import ResourceState, StateStore


class FakeFacade:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_network(self, folder_id: str, name: str, labels: dict[str, str]) -> str:
        self.calls.append("create_network")
        return "net-1"

    def delete_network(self, network_id: str) -> None:
        self.calls.append("delete_network")

    def create_subnet(
        self,
        folder_id: str,
        network_id: str,
        zone_id: str,
        name: str,
        cidr: str,
        labels: dict[str, str],
    ) -> str:
        self.calls.append("create_subnet")
        return "subnet-1"

    def delete_subnet(self, subnet_id: str) -> None:
        self.calls.append("delete_subnet")

    def create_instance(self, **kwargs: object) -> str:
        self.calls.append("create_instance")
        return "instance-1"

    def delete_instance(self, instance_id: str) -> None:
        self.calls.append("delete_instance")

    def delete_disk(self, disk_id: str) -> None:
        self.calls.append("delete_disk")


def _manifest_file(tmp_path: Path) -> Path:
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


def test_executor_applies_and_destroys_in_dependency_order(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    facade = FakeFacade()
    executor = PlanExecutor(facade, store)

    apply_plan = planner.build_apply_plan(store.load())
    executor.execute(apply_plan)
    saved_state = store.load()

    assert facade.calls == ["create_network", "create_subnet", "create_instance"]
    assert set(saved_state.resources) == {"network", "subnet", "instance"}

    destroy_plan = planner.build_destroy_plan(saved_state)
    executor.execute(destroy_plan)

    assert facade.calls[-3:] == ["delete_instance", "delete_subnet", "delete_network"]
    assert not store.load().resources


def test_executor_deletes_orphaned_state_resource(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    facade = FakeFacade()
    executor = PlanExecutor(facade, store)
    executor.execute(planner.build_apply_plan(store.load()))
    facade.calls.clear()

    state = store.load()
    state.put(
        ResourceState(
            logical_name="old-disk",
            resource_type="disk",
            resource_id="disk-1",
            config_hash="old-hash",
            dependencies=[],
        ),
    )
    store.save(state)

    apply_plan = planner.build_apply_plan(store.load())
    executor.execute(apply_plan)

    assert facade.calls == ["delete_disk"]
    assert store.load().get("old-disk") is None
