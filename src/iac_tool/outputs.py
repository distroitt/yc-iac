from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import CloudProviderError, PlanningError, ResourceNotFoundError
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


class OutputStatus(StrEnum):
    AVAILABLE = "available"
    MISSING_IN_STATE = "missing_in_state"
    MISSING_IN_CLOUD = "missing_in_cloud"
    ERROR = "error"


class ResourceOutputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_name: str
    resource_type: str
    resource_id: str | None = None
    status: OutputStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    details: list[str] = Field(default_factory=list)


class OutputsReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resources: list[ResourceOutputs] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def available_count(self) -> int:
        return sum(1 for resource in self.resources if resource.status == OutputStatus.AVAILABLE)


class OutputsCollector:
    def __init__(self, handlers: list[CloudResourceHandler]) -> None:
        self.handlers = handlers
        self._index = {handler.logical_name: handler for handler in handlers}
        self._validate_dependency_graph()

    @classmethod
    def from_manifest(cls, manifest: Manifest) -> "OutputsCollector":
        return cls(ResourceHandlerFactory.build(manifest))

    def _validate_dependency_graph(self) -> None:
        for handler in self.handlers:
            for dependency in handler.dependencies:
                if dependency not in self._index:
                    raise PlanningError(
                        f"Resource '{handler.logical_name}' depends on unknown resource '{dependency}'",
                    )

    def collect(
        self,
        state: InfrastructureState,
        facade: "YandexCloudFacade",
        *,
        continue_on_error: bool = False,
    ) -> OutputsReport:
        resources: list[ResourceOutputs] = []
        warnings: list[str] = []

        for handler in self.handlers:
            try:
                resources.append(self._collect_resource(handler, state, facade))
            except CloudProviderError as exc:
                if not continue_on_error:
                    raise
                warnings.append(
                    f"Failed to collect outputs for {handler.resource_type}:{handler.logical_name}: {exc}",
                )
                resources.append(
                    ResourceOutputs(
                        logical_name=handler.logical_name,
                        resource_type=handler.resource_type,
                        resource_id=_safe_resource_id(state.get(handler.logical_name)),
                        status=OutputStatus.ERROR,
                        details=[str(exc)],
                    ),
                )

        return OutputsReport(resources=resources, warnings=warnings)

    def _collect_resource(
        self,
        handler: CloudResourceHandler,
        state: InfrastructureState,
        facade: "YandexCloudFacade",
    ) -> ResourceOutputs:
        current = state.get(handler.logical_name)
        if current is None:
            return ResourceOutputs(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                status=OutputStatus.MISSING_IN_STATE,
                details=["resource is declared in the manifest but absent from state"],
            )

        if current.resource_type != handler.resource_type:
            return ResourceOutputs(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                resource_id=current.resource_id,
                status=OutputStatus.MISSING_IN_STATE,
                details=[
                    f"state stores resource_type={current.resource_type}, expected {handler.resource_type}",
                ],
            )

        try:
            observed = self._observed_payload(handler, current, facade)
        except ResourceNotFoundError:
            return ResourceOutputs(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                resource_id=current.resource_id,
                status=OutputStatus.MISSING_IN_CLOUD,
                details=["resource_id from state was not found in Yandex Cloud"],
            )

        return ResourceOutputs(
            logical_name=handler.logical_name,
            resource_type=handler.resource_type,
            resource_id=current.resource_id,
            status=OutputStatus.AVAILABLE,
            outputs=self._standard_outputs(handler, current, observed),
        )

    def _observed_payload(
        self,
        handler: CloudResourceHandler,
        resource: ResourceState,
        facade: "YandexCloudFacade",
    ) -> dict[str, object]:
        if isinstance(handler, NetworkResourceHandler):
            return facade.describe_network(resource.resource_id)
        if isinstance(handler, SecurityGroupResourceHandler):
            return facade.describe_security_group(resource.resource_id)
        if isinstance(handler, SubnetResourceHandler):
            return facade.describe_subnet(resource.resource_id)
        if isinstance(handler, DiskResourceHandler):
            return facade.describe_disk(resource.resource_id)
        if isinstance(handler, InstanceResourceHandler):
            return facade.describe_instance(resource.resource_id)
        raise PlanningError(f"Unsupported resource handler for outputs collection: {handler.resource_type}")

    def _standard_outputs(
        self,
        handler: CloudResourceHandler,
        resource: ResourceState,
        observed: dict[str, object],
    ) -> dict[str, Any]:
        if isinstance(handler, NetworkResourceHandler):
            return {
                "id": resource.resource_id,
                "name": observed["name"],
            }

        if isinstance(handler, SecurityGroupResourceHandler):
            return {
                "id": resource.resource_id,
                "name": observed["name"],
                "network_id": observed["network_id"],
            }

        if isinstance(handler, SubnetResourceHandler):
            return {
                "id": resource.resource_id,
                "name": observed["name"],
                "zone_id": observed["zone_id"],
                "cidr_blocks": observed["cidr_blocks"],
                "network_id": observed["network_id"],
            }

        if isinstance(handler, DiskResourceHandler):
            return {
                "id": resource.resource_id,
                "name": observed["name"],
                "size_gb": observed["size_gb"],
                "type_id": observed["type_id"],
                "attached_instance_ids": observed["instance_ids"],
            }

        if isinstance(handler, InstanceResourceHandler):
            return {
                "id": resource.resource_id,
                "name": observed["name"],
                "status": observed["status"],
                "zone_id": observed["zone_id"],
                "fqdn": observed["fqdn"],
                "internal_ip": observed["internal_ip"],
                "public_ip": observed["public_ip"],
                "subnet_id": observed["subnet_id"],
                "security_group_ids": observed["security_group_ids"],
            }

        raise PlanningError(f"Unsupported resource handler for outputs collection: {handler.resource_type}")


def _safe_resource_id(resource: ResourceState | None) -> str | None:
    if resource is None:
        return None
    return resource.resource_id
