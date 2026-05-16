from __future__ import annotations

from enum import StrEnum
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import PlanningError, ResourceNotFoundError
from .manifest import Manifest
from .resources import (
    CloudResourceHandler,
    DiskResourceHandler,
    InstanceResourceHandler,
    NetworkResourceHandler,
    ResourceHandlerFactory,
    SecurityGroupResourceHandler,
    SubnetResourceHandler,
)
from .state import InfrastructureState, ResourceState

if TYPE_CHECKING:
    from .facade import YandexCloudFacade


class DriftStatus(StrEnum):
    IN_SYNC = "in_sync"
    DRIFTED = "drifted"
    MISSING_IN_CLOUD = "missing_in_cloud"
    MISSING_IN_STATE = "missing_in_state"
    ORPHANED_IN_STATE = "orphaned_in_state"


class DriftFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_name: str
    resource_type: str
    status: DriftStatus
    resource_id: str | None = None
    details: list[str] = Field(default_factory=list)


class DriftReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[DriftFinding] = Field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return any(finding.status != DriftStatus.IN_SYNC for finding in self.findings)

    @property
    def drift_count(self) -> int:
        return sum(1 for finding in self.findings if finding.status != DriftStatus.IN_SYNC)

    @property
    def in_sync_count(self) -> int:
        return sum(1 for finding in self.findings if finding.status == DriftStatus.IN_SYNC)


class DriftDetector:
    def __init__(self, handlers: list[CloudResourceHandler]) -> None:
        self.handlers = handlers
        self._index = {handler.logical_name: handler for handler in handlers}
        self._validate_dependency_graph()

    @classmethod
    def from_manifest(cls, manifest: Manifest) -> "DriftDetector":
        return cls(ResourceHandlerFactory.build(manifest))

    def _validate_dependency_graph(self) -> None:
        for handler in self.handlers:
            for dependency in handler.dependencies:
                if dependency not in self._index:
                    raise PlanningError(
                        f"Resource '{handler.logical_name}' depends on unknown resource '{dependency}'",
                    )

    def detect(self, state: InfrastructureState, facade: "YandexCloudFacade") -> DriftReport:
        findings: list[DriftFinding] = []

        for handler in self.handlers:
            findings.append(self._detect_handler(handler, state, facade))

        for logical_name, resource in sorted(state.resources.items()):
            if logical_name in self._index:
                continue
            findings.append(
                DriftFinding(
                    logical_name=logical_name,
                    resource_type=resource.resource_type,
                    status=DriftStatus.ORPHANED_IN_STATE,
                    resource_id=resource.resource_id,
                    details=["resource is present in state but absent from the current manifest"],
                ),
            )

        return DriftReport(findings=findings)

    def _detect_handler(
        self,
        handler: CloudResourceHandler,
        state: InfrastructureState,
        facade: "YandexCloudFacade",
    ) -> DriftFinding:
        current = state.get(handler.logical_name)
        if current is None:
            return DriftFinding(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                status=DriftStatus.MISSING_IN_STATE,
                details=["resource is declared in the manifest but absent from state"],
            )

        if current.resource_type != handler.resource_type:
            return DriftFinding(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                status=DriftStatus.DRIFTED,
                resource_id=current.resource_id,
                details=[
                    f"state expects resource_type={handler.resource_type}, but stored value is {current.resource_type}",
                ],
            )

        try:
            observed = self._observed_payload(handler, current, facade)
        except ResourceNotFoundError:
            return DriftFinding(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                status=DriftStatus.MISSING_IN_CLOUD,
                resource_id=current.resource_id,
                details=["resource_id from state was not found in Yandex Cloud"],
            )

        expected, details = self._expected_payload(handler, state)
        details.extend(_compare_payloads(expected, observed))

        status = DriftStatus.IN_SYNC if not details else DriftStatus.DRIFTED
        return DriftFinding(
            logical_name=handler.logical_name,
            resource_type=handler.resource_type,
            status=status,
            resource_id=current.resource_id,
            details=details,
        )

    def _expected_payload(
        self,
        handler: CloudResourceHandler,
        state: InfrastructureState,
    ) -> tuple[dict[str, object], list[str]]:
        details: list[str] = []

        if isinstance(handler, NetworkResourceHandler):
            return {
                "name": handler.config.name,
                "labels": handler.config.labels,
            }, details

        if isinstance(handler, SubnetResourceHandler):
            payload: dict[str, object] = {
                "name": handler.config.name,
                "labels": handler.config.labels,
                "zone_id": handler.provider.zone_id,
                "cidr_blocks": [handler.config.cidr],
            }
            network_id = _resolve_dependency_id(state, handler.config.network, details)
            if network_id is not None:
                payload["network_id"] = network_id
            return payload, details

        if isinstance(handler, SecurityGroupResourceHandler):
            payload = {
                "name": handler.config.name,
                "labels": handler.config.labels,
                "rules": _manifest_security_group_rules(handler),
            }
            network_id = _resolve_dependency_id(state, handler.config.network, details)
            if network_id is not None:
                payload["network_id"] = network_id
            return payload, details

        if isinstance(handler, DiskResourceHandler):
            return {
                "name": handler.config.name,
                "labels": handler.config.labels,
                "type_id": handler.config.type_id,
                "zone_id": handler.provider.zone_id,
                "size_gb": handler.config.size_gb,
            }, details

        if isinstance(handler, InstanceResourceHandler):
            payload = {
                "name": handler.config.name,
                "labels": handler.config.labels,
                "zone_id": handler.provider.zone_id,
                "platform_id": handler.config.platform_id,
                "cores": handler.config.cores,
                "memory_gb": handler.config.memory_gb,
                "preemptible": handler.config.preemptible,
                "assign_public_ip": handler.config.assign_public_ip,
                "service_account_id": handler.config.service_account_id,
            }
            subnet_id = _resolve_dependency_id(state, handler.config.subnet, details)
            if subnet_id is not None:
                payload["subnet_id"] = subnet_id
            security_group_ids = _resolve_dependency_ids(state, handler.config.security_groups, details)
            if security_group_ids is not None:
                payload["security_group_ids"] = security_group_ids
            data_disk_ids = _resolve_dependency_ids(state, handler.config.data_disks, details)
            if data_disk_ids is not None:
                payload["data_disk_ids"] = data_disk_ids
            return payload, details

        raise PlanningError(f"Unsupported resource handler for drift detection: {handler.resource_type}")

    def _observed_payload(
        self,
        handler: CloudResourceHandler,
        resource: ResourceState,
        facade: "YandexCloudFacade",
    ) -> dict[str, object]:
        if isinstance(handler, NetworkResourceHandler):
            return facade.describe_network(resource.resource_id)
        if isinstance(handler, SubnetResourceHandler):
            return facade.describe_subnet(resource.resource_id)
        if isinstance(handler, SecurityGroupResourceHandler):
            return facade.describe_security_group(resource.resource_id)
        if isinstance(handler, DiskResourceHandler):
            return facade.describe_disk(resource.resource_id)
        if isinstance(handler, InstanceResourceHandler):
            return facade.describe_instance(resource.resource_id)
        raise PlanningError(f"Unsupported resource handler for drift detection: {handler.resource_type}")


def _compare_payloads(expected: dict[str, object], observed: dict[str, object]) -> list[str]:
    details: list[str] = []
    for field_name in sorted(expected):
        if field_name not in observed:
            details.append(f"{field_name}: expected {_format_value(expected[field_name])}, but cloud returned no value")
            continue
        if expected[field_name] != observed[field_name]:
            details.append(
                f"{field_name}: expected {_format_value(expected[field_name])}, observed {_format_value(observed[field_name])}",
            )
    return details


def _format_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _resolve_dependency_id(state: InfrastructureState, logical_name: str, details: list[str]) -> str | None:
    dependency = state.get(logical_name)
    if dependency is None:
        details.append(f"dependency '{logical_name}' is missing from state")
        return None
    return dependency.resource_id


def _resolve_dependency_ids(
    state: InfrastructureState,
    logical_names: list[str],
    details: list[str],
) -> list[str] | None:
    resource_ids: list[str] = []
    unresolved = False
    for logical_name in logical_names:
        resource_id = _resolve_dependency_id(state, logical_name, details)
        if resource_id is None:
            unresolved = True
            continue
        resource_ids.append(resource_id)
    if unresolved:
        return None
    return sorted(resource_ids)


def _manifest_security_group_rules(handler: SecurityGroupResourceHandler) -> list[dict[str, object]]:
    rules: list[dict[str, object]] = []
    for direction, items in (
        ("INGRESS", handler.config.ingress_rules),
        ("EGRESS", handler.config.egress_rules),
    ):
        for item in items:
            rules.append(
                {
                    "direction": direction,
                    "protocol": item.protocol,
                    "cidr_blocks": sorted(item.cidr_blocks),
                    "from_port": item.from_port,
                    "to_port": item.to_port,
                    "description": item.description,
                },
            )
    rules.sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True))
    return rules
