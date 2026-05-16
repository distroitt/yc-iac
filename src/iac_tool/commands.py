from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

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

