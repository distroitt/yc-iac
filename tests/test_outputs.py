from pathlib import Path

from iac_tool.outputs import OutputStatus, OutputsCollector
from iac_tool.exceptions import ResourceNotFoundError
from iac_tool.manifest import load_manifest
from iac_tool.resources import ResourceHandlerFactory
from iac_tool.state import InfrastructureState, ResourceState


class FakeFacade:
    def __init__(self, payloads: dict[str, dict[str, object]], missing: set[str] | None = None) -> None:
        self.payloads = payloads
        self.missing = missing or set()

    def _get(self, resource_id: str) -> dict[str, object]:
        if resource_id in self.missing:
            raise ResourceNotFoundError(f"Resource '{resource_id}' is missing")
        return self.payloads[resource_id]

    def describe_network(self, resource_id: str) -> dict[str, object]:
        return self._get(resource_id)

    def describe_security_group(self, resource_id: str) -> dict[str, object]:
        return self._get(resource_id)

    def describe_subnet(self, resource_id: str) -> dict[str, object]:
        return self._get(resource_id)

    def describe_disk(self, resource_id: str) -> dict[str, object]:
        return self._get(resource_id)

    def describe_instance(self, resource_id: str) -> dict[str, object]:
        return self._get(resource_id)


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
    return manifest_path


def _matching_state(manifest_path: Path) -> tuple[InfrastructureState, dict[str, str]]:
    manifest = load_manifest(manifest_path)
    handlers = ResourceHandlerFactory.build(manifest)
    state = InfrastructureState()
    resource_ids: dict[str, str] = {}
    for handler in handlers:
        resource_id = f"id-{handler.logical_name}"
        resource_ids[handler.logical_name] = resource_id
        state.put(
            ResourceState(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                resource_id=resource_id,
                config_hash=handler.config_hash(),
                dependencies=list(handler.dependencies),
            ),
        )
    return state, resource_ids


def _matching_payloads(resource_ids: dict[str, str]) -> dict[str, dict[str, object]]:
    return {
        resource_ids["network"]: {
            "name": "demo-network",
            "labels": {},
        },
        resource_ids["ssh-access"]: {
            "name": "demo-sg",
            "labels": {},
            "network_id": resource_ids["network"],
        },
        resource_ids["subnet"]: {
            "name": "demo-subnet",
            "labels": {},
            "network_id": resource_ids["network"],
            "zone_id": "ru-central1-a",
            "cidr_blocks": ["10.10.0.0/24"],
        },
        resource_ids["data-disk"]: {
            "name": "demo-disk",
            "labels": {},
            "type_id": "network-hdd",
            "zone_id": "ru-central1-a",
            "size_gb": 10,
            "instance_ids": ["id-instance"],
        },
        resource_ids["instance"]: {
            "name": "demo-instance",
            "status": "RUNNING",
            "fqdn": "demo-instance.auto.internal",
            "zone_id": "ru-central1-a",
            "labels": {},
            "platform_id": "standard-v3",
            "cores": 2,
            "memory_gb": 2,
            "preemptible": False,
            "subnet_id": resource_ids["subnet"],
            "internal_ip": "10.10.0.10",
            "public_ip": "51.250.10.10",
            "security_group_ids": [resource_ids["ssh-access"]],
            "assign_public_ip": True,
            "data_disk_ids": [resource_ids["data-disk"]],
            "service_account_id": None,
        },
    }


def test_outputs_collector_returns_standard_outputs_for_available_resources(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    state, resource_ids = _matching_state(manifest_path)
    report = OutputsCollector.from_manifest(manifest).collect(state, FakeFacade(_matching_payloads(resource_ids)))

    assert report.available_count == 5
    by_name = {resource.logical_name: resource for resource in report.resources}
    assert by_name["network"].status == OutputStatus.AVAILABLE
    assert by_name["network"].outputs == {
        "id": resource_ids["network"],
        "name": "demo-network",
    }
    assert by_name["instance"].status == OutputStatus.AVAILABLE
    assert by_name["instance"].outputs["internal_ip"] == "10.10.0.10"
    assert by_name["instance"].outputs["public_ip"] == "51.250.10.10"
    assert by_name["instance"].outputs["status"] == "RUNNING"


def test_outputs_collector_marks_missing_state_and_missing_cloud(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    state, resource_ids = _matching_state(manifest_path)
    state.delete("subnet")
    report = OutputsCollector.from_manifest(manifest).collect(
        state,
        FakeFacade(_matching_payloads(resource_ids), missing={resource_ids["data-disk"]}),
    )

    by_name = {resource.logical_name: resource for resource in report.resources}
    assert by_name["subnet"].status == OutputStatus.MISSING_IN_STATE
    assert by_name["data-disk"].status == OutputStatus.MISSING_IN_CLOUD
