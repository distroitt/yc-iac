from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .exceptions import ExecutionError
from .state import ResourceState

if TYPE_CHECKING:
    from .facade import YandexCloudFacade
    from .resources import CloudResourceHandler
    from .state import InfrastructureState, StateStore


class PlanCommand(ABC):
    @property
    @abstractmethod
    def logical_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def resource_type(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def description(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def execute(
        self,
        facade: "YandexCloudFacade",
        state: "InfrastructureState",
        state_store: "StateStore",
    ) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class CreateResourceCommand(PlanCommand):
    handler: "CloudResourceHandler"
    reason: str

    @property
    def logical_name(self) -> str:
        return self.handler.logical_name

    @property
    def resource_type(self) -> str:
        return self.handler.resource_type

    def description(self) -> str:
        return f"create {self.resource_type}:{self.logical_name}"

    def execute(
        self,
        facade: "YandexCloudFacade",
        state: "InfrastructureState",
        state_store: "StateStore",
    ) -> None:
        resource = self.handler.create(facade, state)
        state.put(resource)
        state_store.save(state)


@dataclass(frozen=True)
class DeleteResourceCommand(PlanCommand):
    handler: "CloudResourceHandler"
    reason: str

    @property
    def logical_name(self) -> str:
        return self.handler.logical_name

    @property
    def resource_type(self) -> str:
        return self.handler.resource_type

    def description(self) -> str:
        return f"delete {self.resource_type}:{self.logical_name}"

    def execute(
        self,
        facade: "YandexCloudFacade",
        state: "InfrastructureState",
        state_store: "StateStore",
    ) -> None:
        resource = state.get(self.handler.logical_name)
        if resource is None:
            return
        self.handler.delete(facade, resource)
        state.delete(self.handler.logical_name)
        state_store.save(state)


@dataclass(frozen=True)
class DeleteStateResourceCommand(PlanCommand):
    resource: ResourceState
    reason: str

    @property
    def logical_name(self) -> str:
        return self.resource.logical_name

    @property
    def resource_type(self) -> str:
        return self.resource.resource_type

    def description(self) -> str:
        return f"delete {self.resource_type}:{self.logical_name}"

    def execute(
        self,
        facade: "YandexCloudFacade",
        state: "InfrastructureState",
        state_store: "StateStore",
    ) -> None:
        resource = state.get(self.resource.logical_name)
        if resource is None:
            return
        _delete_stored_resource(facade, resource)
        state.delete(resource.logical_name)
        state_store.save(state)


def _delete_stored_resource(facade: "YandexCloudFacade", resource: ResourceState) -> None:
    if resource.resource_type == "network":
        facade.delete_network(resource.resource_id)
        return
    if resource.resource_type == "security_group":
        facade.delete_security_group(resource.resource_id)
        return
    if resource.resource_type == "subnet":
        facade.delete_subnet(resource.resource_id)
        return
    if resource.resource_type == "disk":
        facade.delete_disk(resource.resource_id)
        return
    if resource.resource_type == "instance":
        facade.delete_instance(resource.resource_id)
        return
    raise ExecutionError(
        f"Cannot delete unsupported state resource type '{resource.resource_type}' "
        f"for logical resource '{resource.logical_name}'",
    )
