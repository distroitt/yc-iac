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
        state.put(handler.build_state(f"id-{index}"))
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
        ChangeKind.UPDATE,
        ChangeKind.NOOP,
        ChangeKind.NOOP,
    ]
    assert [command.description() for command in plan.commands] == ["update network:network"]


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
        "network-a": ChangeKind.UPDATE,
        "network-b": ChangeKind.NOOP,
        "subnet-a": ChangeKind.NOOP,
        "subnet-b": ChangeKind.NOOP,
        "instance-a": ChangeKind.NOOP,
        "instance-b": ChangeKind.NOOP,
    }
    assert [command.description() for command in plan.commands] == ["update network:network-a"]


def test_plan_deletes_resources_removed_from_manifest(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    state = _matching_state(manifest_path)
    state.put(
        ResourceState(
            logical_name="old-disk",
            resource_type="disk",
            resource_id="disk-id",
            config_hash="old-hash",
            dependencies=[],
        ),
    )

    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    plan = planner.build_apply_plan(state)

    assert {change.logical_name: change.kind for change in plan.changes}["old-disk"] == ChangeKind.DELETE
    assert [command.description() for command in plan.commands] == ["delete disk:old-disk"]


def test_plan_deletes_old_state_type_when_logical_resource_type_changes(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    state = _matching_state(manifest_path)
    state.put(
        ResourceState(
            logical_name="network",
            resource_type="subnet",
            resource_id="old-subnet-id",
            config_hash="old-hash",
            dependencies=[],
        ),
    )

    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    plan = planner.build_apply_plan(state)
    descriptions = [command.description() for command in plan.commands]

    assert "delete subnet:network" in descriptions
    assert descriptions.index("delete subnet:network") < descriptions.index("create network:network")


def test_planner_topologically_sorts_scrambled_handlers(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    handlers = list(reversed(ResourceHandlerFactory.build(manifest)))

    planner = Planner(handlers)
    plan = planner.build_apply_plan(InfrastructureState())

    assert [command.description() for command in plan.commands] == [
        "create network:network",
        "create subnet:subnet",
        "create instance:instance",
    ]


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


def test_plan_updates_instance_when_only_security_groups_change(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "security-group-update.yaml"
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
    state = _matching_state(manifest_path)
    manifest_path.write_text(updated_manifest + "\n", encoding="utf-8")

    plan = Planner.from_manifest(load_manifest(manifest_path)).build_apply_plan(state)
    change_kinds = {change.logical_name: change.kind for change in plan.changes}

    assert change_kinds["web-access"] == ChangeKind.CREATE
    assert change_kinds["instance"] == ChangeKind.UPDATE
    assert [command.description() for command in plan.commands] == [
        "create security_group:web-access",
        "update instance:instance",
    ]


def test_plan_updates_instance_when_mutable_fields_change(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    state = _matching_state(manifest_path)
    content = manifest_path.read_text(encoding="utf-8").replace("demo-instance", "changed-instance")
    manifest_path.write_text(content, encoding="utf-8")

    plan = Planner.from_manifest(load_manifest(manifest_path)).build_apply_plan(state)
    change_kinds = {change.logical_name: change.kind for change in plan.changes}

    assert change_kinds["instance"] == ChangeKind.UPDATE
    assert [command.description() for command in plan.commands] == ["update instance:instance"]


def test_plan_still_replaces_instance_when_non_updatable_field_changes(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    state = _matching_state(manifest_path)
    content = manifest_path.read_text(encoding="utf-8").replace('username: "yc-user"', 'username: "ubuntu"')
    manifest_path.write_text(content, encoding="utf-8")

    plan = Planner.from_manifest(load_manifest(manifest_path)).build_apply_plan(state)
    change_kinds = {change.logical_name: change.kind for change in plan.changes}

    assert change_kinds["instance"] == ChangeKind.REPLACE
    assert [command.description() for command in plan.commands][-2:] == [
        "delete instance:instance",
        "create instance:instance",
    ]


def test_plan_updates_mutable_resource_fields_in_place(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "updates.yaml"
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
    name: "demo-sg"
    network: "network"
    ingress_rules:
      - protocol: "TCP"
        from_port: 22
        to_port: 22
        cidr_blocks:
          - "10.0.0.0/8"

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
    cores: 2
    memory_gb: 2
    security_groups:
      - "ssh-access"
    data_disks:
      - "data-disk"
""".strip()
    updated_manifest = base_manifest.replace("demo-network", "renamed-network")
    updated_manifest = updated_manifest.replace("10.10.0.0/24", "10.10.1.0/24")
    updated_manifest = updated_manifest.replace("10.0.0.0/8", "0.0.0.0/0")
    updated_manifest = updated_manifest.replace("size_gb: 10", "size_gb: 12")
    updated_manifest = updated_manifest.replace("cores: 2", "cores: 4")

    manifest_path.write_text(base_manifest + "\n", encoding="utf-8")
    state = _matching_state(manifest_path)
    manifest_path.write_text(updated_manifest + "\n", encoding="utf-8")

    plan = Planner.from_manifest(load_manifest(manifest_path)).build_apply_plan(state)

    assert {change.logical_name: change.kind for change in plan.changes} == {
        "network": ChangeKind.UPDATE,
        "ssh-access": ChangeKind.UPDATE,
        "subnet": ChangeKind.UPDATE,
        "data-disk": ChangeKind.UPDATE,
        "instance": ChangeKind.UPDATE,
    }
    assert [command.description() for command in plan.commands] == [
        "update network:network",
        "update security_group:ssh-access",
        "update subnet:subnet",
        "update disk:data-disk",
        "update instance:instance",
    ]


def test_plan_replaces_resource_without_config_payload_for_non_security_group_update(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    state = _matching_state(manifest_path)
    network = state.require("network")
    state.put(network.model_copy(update={"config_payload": {}}))
    content = manifest_path.read_text(encoding="utf-8").replace("demo-network", "changed-network")
    manifest_path.write_text(content, encoding="utf-8")

    plan = Planner.from_manifest(load_manifest(manifest_path)).build_apply_plan(state)

    assert {change.logical_name: change.kind for change in plan.changes}["network"] == ChangeKind.REPLACE


def test_destroy_plan_deletes_orphaned_state_resources(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    state = _matching_state(manifest_path)
    state.put(
        ResourceState(
            logical_name="old-disk",
            resource_type="disk",
            resource_id="disk-id",
            config_hash="old-hash",
            dependencies=[],
        ),
    )

    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    plan = planner.build_destroy_plan(state)

    assert {change.logical_name: change.kind for change in plan.changes}["old-disk"] == ChangeKind.DELETE
    assert "delete disk:old-disk" in [command.description() for command in plan.commands]
