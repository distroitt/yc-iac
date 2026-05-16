from pathlib import Path

from iac_tool.drift import DriftDetector, DriftStatus
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
            "rules": [
                {
                    "direction": "INGRESS",
                    "protocol": "TCP",
                    "cidr_blocks": ["0.0.0.0/0"],
                    "from_port": 22,
                    "to_port": 22,
                    "description": None,
                },
            ],
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
            "instance_ids": [],
        },
        resource_ids["instance"]: {
            "name": "demo-instance",
            "labels": {},
            "zone_id": "ru-central1-a",
            "platform_id": "standard-v3",
            "cores": 2,
            "memory_gb": 2,
            "preemptible": False,
            "subnet_id": resource_ids["subnet"],
            "security_group_ids": [resource_ids["ssh-access"]],
            "assign_public_ip": True,
            "data_disk_ids": [resource_ids["data-disk"]],
            "service_account_id": None,
        },
    }


def test_drift_detector_reports_in_sync_resources(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    state, resource_ids = _matching_state(manifest_path)
    facade = FakeFacade(_matching_payloads(resource_ids))

    report = DriftDetector.from_manifest(manifest).detect(state, facade)

    assert not report.has_drift
    assert report.drift_count == 0
    assert [finding.status for finding in report.findings] == [
        DriftStatus.IN_SYNC,
        DriftStatus.IN_SYNC,
        DriftStatus.IN_SYNC,
        DriftStatus.IN_SYNC,
        DriftStatus.IN_SYNC,
    ]


def test_drift_detector_reports_missing_in_cloud_and_orphaned_state(tmp_path: Path) -> None:
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
    manifest = load_manifest(manifest_path)
    state = InfrastructureState()
    state.put(
        ResourceState(
            logical_name="network",
            resource_type="network",
            resource_id="id-network",
            config_hash="hash-network",
            dependencies=[],
        ),
    )
    state.put(
        ResourceState(
            logical_name="old-instance",
            resource_type="instance",
            resource_id="id-old-instance",
            config_hash="hash-old-instance",
            dependencies=[],
        ),
    )

    report = DriftDetector.from_manifest(manifest).detect(
        state,
        FakeFacade(payloads={}, missing={"id-network"}),
    )

    assert report.has_drift
    assert {finding.logical_name: finding.status for finding in report.findings} == {
        "network": DriftStatus.MISSING_IN_CLOUD,
        "old-instance": DriftStatus.ORPHANED_IN_STATE,
    }


def test_drift_detector_marks_instance_as_drifted_when_dependency_state_is_missing(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = load_manifest(manifest_path)
    state, resource_ids = _matching_state(manifest_path)
    state.delete("subnet")
    facade = FakeFacade(_matching_payloads(resource_ids))

    report = DriftDetector.from_manifest(manifest).detect(state, facade)
    by_name = {finding.logical_name: finding for finding in report.findings}

    assert by_name["subnet"].status == DriftStatus.MISSING_IN_STATE
    assert by_name["instance"].status == DriftStatus.DRIFTED
    assert "dependency 'subnet' is missing from state" in by_name["instance"].details
