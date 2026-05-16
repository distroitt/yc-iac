from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .commands import CreateResourceCommand, DeleteResourceCommand, PlanCommand
from .exceptions import PlanningError
from .manifest import Manifest
from .resources import CloudResourceHandler, ResourceHandlerFactory
from .state import InfrastructureState


class ChangeKind(StrEnum):
    CREATE = "create"
    DELETE = "delete"
    REPLACE = "replace"
    NOOP = "noop"


@dataclass(frozen=True)
class PlannedChange:
    logical_name: str
    resource_type: str
    kind: ChangeKind
    reason: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionPlan:
    changes: list[PlannedChange]
    commands: list[PlanCommand]

    @property
    def is_noop(self) -> bool:
        return not self.commands


class Planner:
    def __init__(self, handlers: list[CloudResourceHandler]) -> None:
        self.handlers = handlers
        self._index = {handler.logical_name: handler for handler in handlers}
        self._validate_dependency_graph()

    @classmethod
    def from_manifest(cls, manifest: Manifest) -> "Planner":
        return cls(ResourceHandlerFactory.build(manifest))

    def _validate_dependency_graph(self) -> None:
        for handler in self.handlers:
            for dependency in handler.dependencies:
                if dependency not in self._index:
                    raise PlanningError(
                        f"Resource '{handler.logical_name}' depends on unknown resource '{dependency}'",
                    )

    def build_apply_plan(self, state: InfrastructureState) -> ExecutionPlan:
        decisions: dict[str, ChangeKind] = {}
        reasons: dict[str, str] = {}

        for handler in self.handlers:
            current = state.get(handler.logical_name)
            if current is None:
                decisions[handler.logical_name] = ChangeKind.CREATE
                reasons[handler.logical_name] = "resource is absent from state"
            elif current.resource_type != handler.resource_type:
                decisions[handler.logical_name] = ChangeKind.REPLACE
                reasons[handler.logical_name] = "resource type changed"
            elif current.config_hash != handler.config_hash():
                decisions[handler.logical_name] = ChangeKind.REPLACE
                reasons[handler.logical_name] = "configuration hash changed"
            else:
                decisions[handler.logical_name] = ChangeKind.NOOP
                reasons[handler.logical_name] = "configuration matches state"

        changed = True
        while changed:
            changed = False
            for handler in self.handlers:
                dependency_changes = [decisions[dependency] for dependency in handler.dependencies]
                if not any(kind in {ChangeKind.CREATE, ChangeKind.REPLACE} for kind in dependency_changes):
                    continue
                current = state.get(handler.logical_name)
                next_kind = ChangeKind.CREATE if current is None else ChangeKind.REPLACE
                if decisions[handler.logical_name] != next_kind:
                    decisions[handler.logical_name] = next_kind
                    reasons[handler.logical_name] = "dependency will be recreated"
                    changed = True

        changes = [
            PlannedChange(
                logical_name=handler.logical_name,
                resource_type=handler.resource_type,
                kind=decisions[handler.logical_name],
                reason=reasons[handler.logical_name],
                dependencies=handler.dependencies,
            )
            for handler in self.handlers
        ]

        commands: list[PlanCommand] = []
        for handler in reversed(self.handlers):
            if decisions[handler.logical_name] == ChangeKind.REPLACE:
                commands.append(DeleteResourceCommand(handler=handler, reason=reasons[handler.logical_name]))

        for handler in self.handlers:
            kind = decisions[handler.logical_name]
            if kind in {ChangeKind.CREATE, ChangeKind.REPLACE}:
                commands.append(CreateResourceCommand(handler=handler, reason=reasons[handler.logical_name]))

        return ExecutionPlan(changes=changes, commands=commands)

    def build_destroy_plan(self, state: InfrastructureState) -> ExecutionPlan:
        changes: list[PlannedChange] = []
        commands: list[PlanCommand] = []

        for handler in self.handlers:
            current = state.get(handler.logical_name)
            if current is None:
                changes.append(
                    PlannedChange(
                        logical_name=handler.logical_name,
                        resource_type=handler.resource_type,
                        kind=ChangeKind.NOOP,
                        reason="resource is absent from state",
                        dependencies=handler.dependencies,
                    ),
                )
            else:
                changes.append(
                    PlannedChange(
                        logical_name=handler.logical_name,
                        resource_type=handler.resource_type,
                        kind=ChangeKind.DELETE,
                        reason="resource is present in state",
                        dependencies=handler.dependencies,
                    ),
                )

        for handler in reversed(self.handlers):
            if state.get(handler.logical_name) is not None:
                commands.append(DeleteResourceCommand(handler=handler, reason="resource is present in state"))

        return ExecutionPlan(changes=changes, commands=commands)

