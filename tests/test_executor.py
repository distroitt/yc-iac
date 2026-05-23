from pathlib import Path

from iac_tool.executor import PlanExecutor
from iac_tool.manifest import load_manifest
from iac_tool.planner import Planner
from iac_tool.state import ResourceState, StateStore


class FakeFacade:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.instance_status = "RUNNING"

    def create_network(self, folder_id: str, name: str, labels: dict[str, str]) -> str:
        self.calls.append("create_network")
        return "net-1"

    def delete_network(self, network_id: str) -> None:
        self.calls.append("delete_network")

    def update_network(self, **kwargs: object) -> None:
        self.calls.append(f"update_network:{kwargs['network_id']}:{','.join(kwargs['update_mask_paths'])}")

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

    def update_subnet(self, **kwargs: object) -> None:
        self.calls.append(f"update_subnet:{kwargs['subnet_id']}:{','.join(kwargs['update_mask_paths'])}")

    def create_security_group(self, **kwargs: object) -> str:
        self.calls.append("create_security_group")
        return f"sg-{kwargs['name']}"

    def delete_security_group(self, security_group_id: str) -> None:
        self.calls.append("delete_security_group")

    def update_security_group(self, **kwargs: object) -> None:
        self.calls.append(f"update_security_group:{kwargs['security_group_id']}:{','.join(kwargs['update_mask_paths'])}")

    def create_instance(self, **kwargs: object) -> str:
        self.calls.append("create_instance")
        return "instance-1"

    def delete_instance(self, instance_id: str) -> None:
        self.calls.append("delete_instance")

    def update_instance(self, **kwargs: object) -> None:
        self.calls.append(f"update_instance:{kwargs['instance_id']}:{','.join(kwargs['update_mask_paths'])}")

    def stop_instance_if_running(self, instance_id: str) -> bool:
        self.calls.append(f"stop_instance_if_running:{instance_id}:{self.instance_status}")
        if self.instance_status == "RUNNING":
            self.instance_status = "STOPPED"
            return True
        return False

    def start_instance(self, instance_id: str) -> None:
        self.calls.append(f"start_instance:{instance_id}")
        self.instance_status = "RUNNING"

    def delete_disk(self, disk_id: str) -> None:
        self.calls.append("delete_disk")

    def update_disk(self, **kwargs: object) -> None:
        self.calls.append(f"update_disk:{kwargs['disk_id']}:{','.join(kwargs['update_mask_paths'])}")

    def update_instance_security_groups(self, instance_id: str, security_group_ids: list[str]) -> None:
        self.calls.append(f"update_instance_security_groups:{instance_id}:{','.join(security_group_ids)}")


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
  memory_gb: 2
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
    events: list[tuple[str, int, int, str]] = []
    executor = PlanExecutor(
        facade,
        store,
        progress_callback=lambda event, index, total, command: events.append(
            (event, index, total, command.description()),
        ),
    )

    apply_plan = planner.build_apply_plan(store.load())
    executor.execute(apply_plan)
    saved_state = store.load()

    assert facade.calls == ["create_network", "create_subnet", "create_instance"]
    assert set(saved_state.resources) == {"network", "subnet", "instance"}
    assert events[:2] == [
        ("start", 1, 3, "create network:network"),
        ("done", 1, 3, "create network:network"),
    ]

    events.clear()
    destroy_plan = planner.build_destroy_plan(saved_state)
    executor.execute(destroy_plan)

    assert facade.calls[-3:] == ["delete_instance", "delete_subnet", "delete_network"]
    assert not store.load().resources
    assert events[:2] == [
        ("start", 1, 3, "delete instance:instance"),
        ("done", 1, 3, "delete instance:instance"),
    ]


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


def test_executor_updates_instance_security_groups_in_place(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.yaml"
    base_manifest = f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network"
    name: "demo-network"

security_groups:
  - logical_name: "ssh-access"
    name: "demo-ssh-sg"
    network: "network"

subnets:
  - logical_name: "subnet"
    name: "demo-subnet"
    network: "network"
    cidr: "10.10.0.0/24"

instances:
  - logical_name: "instance"
    name: "demo-instance"
    subnet: "subnet"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
    security_groups:
      - "ssh-access"
""".strip()
    updated_manifest = f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network"
    name: "demo-network"

security_groups:
  - logical_name: "ssh-access"
    name: "demo-ssh-sg"
    network: "network"
  - logical_name: "web-access"
    name: "demo-web-sg"
    network: "network"

subnets:
  - logical_name: "subnet"
    name: "demo-subnet"
    network: "network"
    cidr: "10.10.0.0/24"

instances:
  - logical_name: "instance"
    name: "demo-instance"
    subnet: "subnet"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
    security_groups:
      - "ssh-access"
      - "web-access"
""".strip()
    manifest_path.write_text(base_manifest + "\n", encoding="utf-8")
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    facade = FakeFacade()
    executor = PlanExecutor(facade, store)

    executor.execute(planner.build_apply_plan(store.load()))
    manifest_path.write_text(updated_manifest + "\n", encoding="utf-8")
    planner = Planner.from_manifest(load_manifest(manifest_path))
    facade.calls.clear()

    executor.execute(planner.build_apply_plan(store.load()))

    assert facade.calls == [
        "create_security_group",
        "update_instance_security_groups:instance-1:sg-demo-ssh-sg,sg-demo-web-sg",
    ]
    updated_instance = store.load().require("instance")
    assert updated_instance.dependencies == ["subnet", "ssh-access", "web-access"]
    assert updated_instance.config_payload["security_groups"] == ["ssh-access", "web-access"]


def test_executor_updates_network_in_place_and_refreshes_state_payload(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    facade = FakeFacade()
    executor = PlanExecutor(facade, store)

    executor.execute(planner.build_apply_plan(store.load()))
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("demo-network", "renamed-network"),
        encoding="utf-8",
    )
    planner = Planner.from_manifest(load_manifest(manifest_path))
    facade.calls.clear()

    executor.execute(planner.build_apply_plan(store.load()))

    assert facade.calls == ["update_network:net-1:name"]
    updated_network = store.load().require("network")
    assert updated_network.config_payload["name"] == "renamed-network"


def test_executor_stops_dependent_instances_before_subnet_cidr_update(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    facade = FakeFacade()
    executor = PlanExecutor(facade, store)

    executor.execute(planner.build_apply_plan(store.load()))
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("10.10.0.0/24", "10.10.1.0/24"),
        encoding="utf-8",
    )
    planner = Planner.from_manifest(load_manifest(manifest_path))
    facade.calls.clear()

    executor.execute(planner.build_apply_plan(store.load()))

    assert facade.calls == [
        "stop_instance_if_running:instance-1:RUNNING",
        "update_subnet:subnet-1:v4_cidr_blocks",
        "start_instance:instance-1",
    ]
    assert store.load().require("subnet").config_payload["cidr"] == "10.10.1.0/24"


def test_executor_stops_running_instance_before_resource_update(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    facade = FakeFacade()
    executor = PlanExecutor(facade, store)

    executor.execute(planner.build_apply_plan(store.load()))
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("memory_gb: 2", "memory_gb: 4"),
        encoding="utf-8",
    )
    planner = Planner.from_manifest(load_manifest(manifest_path))
    facade.calls.clear()

    executor.execute(planner.build_apply_plan(store.load()))

    assert facade.calls == [
        "stop_instance_if_running:instance-1:RUNNING",
        "update_instance:instance-1:resources_spec",
        "start_instance:instance-1",
    ]
    assert facade.instance_status == "RUNNING"


def test_executor_does_not_start_instance_that_was_already_stopped(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    facade = FakeFacade()
    executor = PlanExecutor(facade, store)

    executor.execute(planner.build_apply_plan(store.load()))
    facade.instance_status = "STOPPED"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("memory_gb: 2", "memory_gb: 4"),
        encoding="utf-8",
    )
    planner = Planner.from_manifest(load_manifest(manifest_path))
    facade.calls.clear()

    executor.execute(planner.build_apply_plan(store.load()))

    assert facade.calls == [
        "stop_instance_if_running:instance-1:STOPPED",
        "update_instance:instance-1:resources_spec",
    ]
    assert facade.instance_status == "STOPPED"
