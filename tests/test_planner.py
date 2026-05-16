from pathlib import Path

from iac_tool.manifest import load_manifest
from iac_tool.planner import ChangeKind, Planner
from iac_tool.resources import ResourceHandlerFactory
from iac_tool.state import InfrastructureState, ResourceState


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

networks:
  - logical_name: "network"
    name: "demo-network"

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
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _multi_manifest_file(tmp_path: Path) -> Path:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "multi-manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network-a"
    name: "demo-network-a"
  - logical_name: "network-b"
    name: "demo-network-b"

subnets:
  - logical_name: "subnet-a"
    name: "demo-subnet-a"
    network: "network-a"
    cidr: "10.10.0.0/24"
  - logical_name: "subnet-b"
    name: "demo-subnet-b"
    network: "network-b"
    cidr: "10.20.0.0/24"

instances:
  - logical_name: "instance-a"
    name: "demo-instance-a"
    subnet: "subnet-a"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
  - logical_name: "instance-b"
    name: "demo-instance-b"
    subnet: "subnet-b"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _matching_state(manifest_path: Path) -> InfrastructureState:
    manifest = load_manifest(manifest_path)
    handlers = ResourceHandlerFactory.build(manifest)
    state = InfrastructureState()
    for index, handler in enumerate(handlers, start=1):
        state.put(
            ResourceState(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                resource_id=f"id-{index}",
                config_hash=handler.config_hash(),
                dependencies=list(handler.dependencies),
            ),
        )
    return state


def test_plan_create_from_empty_state(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)

    plan = planner.build_apply_plan(InfrastructureState())

    assert [change.kind for change in plan.changes] == [
        ChangeKind.CREATE,
        ChangeKind.CREATE,
        ChangeKind.CREATE,
    ]
    assert len(plan.commands) == 3


def test_plan_is_noop_when_hashes_match(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)

    plan = planner.build_apply_plan(_matching_state(manifest_path))

    assert all(change.kind == ChangeKind.NOOP for change in plan.changes)
    assert plan.is_noop


def test_plan_cascades_replace_to_dependents(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    state = _matching_state(manifest_path)

    content = manifest_path.read_text(encoding="utf-8").replace("demo-network", "changed-network")
    manifest_path.write_text(content, encoding="utf-8")

    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    plan = planner.build_apply_plan(state)

    assert [change.kind for change in plan.changes] == [
        ChangeKind.REPLACE,
        ChangeKind.REPLACE,
        ChangeKind.REPLACE,
    ]
    assert len(plan.commands) == 6


def test_plan_replaces_only_affected_dependency_branch(tmp_path: Path) -> None:
    manifest_path = _multi_manifest_file(tmp_path)
    state = _matching_state(manifest_path)

    content = manifest_path.read_text(encoding="utf-8").replace("demo-network-a", "changed-network-a")
    manifest_path.write_text(content, encoding="utf-8")

    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    plan = planner.build_apply_plan(state)
    change_kinds = {change.logical_name: change.kind for change in plan.changes}

    assert change_kinds == {
        "network-a": ChangeKind.REPLACE,
        "network-b": ChangeKind.NOOP,
        "subnet-a": ChangeKind.REPLACE,
        "subnet-b": ChangeKind.NOOP,
        "instance-a": ChangeKind.REPLACE,
        "instance-b": ChangeKind.NOOP,
    }
    assert len(plan.commands) == 6


def test_plan_handles_security_groups_and_disks(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "extended-manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network"
    name: "demo-network"

security_groups:
  - logical_name: "ssh-access"
    name: "demo-sg"
    network: "network"
    ingress_rules:
      - protocol: "TCP"
        from_port: 22
        to_port: 22
        cidr_blocks:
          - "0.0.0.0/0"

subnets:
  - logical_name: "subnet"
    name: "demo-subnet"
    network: "network"
    cidr: "10.10.0.0/24"

disks:
  - logical_name: "data-disk"
    name: "demo-disk"
    size_gb: 10

instances:
  - logical_name: "instance"
    name: "demo-instance"
    subnet: "subnet"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
    security_groups:
      - "ssh-access"
    data_disks:
      - "data-disk"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    plan = planner.build_apply_plan(InfrastructureState())
    change_kinds = {change.logical_name: change.kind for change in plan.changes}

    assert change_kinds == {
        "network": ChangeKind.CREATE,
        "ssh-access": ChangeKind.CREATE,
        "subnet": ChangeKind.CREATE,
        "data-disk": ChangeKind.CREATE,
        "instance": ChangeKind.CREATE,
    }
    assert len(plan.commands) == 5
