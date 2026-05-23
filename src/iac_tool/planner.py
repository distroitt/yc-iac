from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .commands import CreateResourceCommand, DeleteResourceCommand, DeleteStateResourceCommand, PlanCommand, UpdateResourceCommand
from .exceptions import PlanningError
from .manifest import Manifest
from .resources import CloudResourceHandler, ResourceHandlerFactory
from .state import InfrastructureState, ResourceState


class ChangeKind(StrEnum):
    CREATE = "create"
    DELETE = "delete"
    REPLACE = "replace"
    UPDATE = "update"
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
        if len(self._index) != len(handlers):
            raise PlanningError("Duplicate logical_name detected in resource handlers")
        self._validate_dependency_graph()
        self.handlers = self._sort_handlers_topologically(handlers)

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

    def _sort_handlers_topologically(self, handlers: list[CloudResourceHandler]) -> list[CloudResourceHandler]:
        sorted_handlers: list[CloudResourceHandler] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(handler: CloudResourceHandler, path: tuple[str, ...]) -> None:
            if handler.logical_name in visited:
                return
            if handler.logical_name in visiting:
                cycle = " -> ".join([*path, handler.logical_name])
                raise PlanningError(f"Resource dependency cycle detected: {cycle}")

            visiting.add(handler.logical_name)
            for dependency in handler.dependencies:
                visit(self._index[dependency], (*path, handler.logical_name))
            visiting.remove(handler.logical_name)
            visited.add(handler.logical_name)
            sorted_handlers.append(handler)

        for handler in handlers:
            visit(handler, ())

        return sorted_handlers

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
            elif current.config_hash != handler.config_hash() and handler.can_update(current):
                decisions[handler.logical_name] = ChangeKind.UPDATE
                reasons[handler.logical_name] = "configuration can be updated in place"
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
                changed_dependencies = [
                    (dependency, decisions[dependency])
                    for dependency in handler.dependencies
                    if decisions[dependency] in {ChangeKind.CREATE, ChangeKind.REPLACE}
                ]
                if not changed_dependencies:
                    continue
                current = state.get(handler.logical_name)
                replace_required = any(
                    handler.dependency_change_requires_replace(dependency, kind.value)
                    for dependency, kind in changed_dependencies
                )
                if current is None:
                    next_kind = ChangeKind.CREATE
                elif replace_required:
                    next_kind = ChangeKind.REPLACE
                elif handler.can_update(current):
                    next_kind = ChangeKind.UPDATE
                else:
                    next_kind = ChangeKind.REPLACE
                if decisions[handler.logical_name] != next_kind:
                    decisions[handler.logical_name] = next_kind
                    reasons[handler.logical_name] = (
                        "dependency will be recreated"
                        if next_kind == ChangeKind.REPLACE
                        else "security group dependency will be created"
                    )
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
        orphaned_resources = [
            resource
            for logical_name, resource in state.resources.items()
            if logical_name not in self._index
        ]
        changes.extend(
            PlannedChange(
                logical_name=resource.logical_name,
                resource_type=resource.resource_type,
                kind=ChangeKind.DELETE,
                reason="resource is absent from manifest",
                dependencies=tuple(resource.dependencies),
            )
            for resource in orphaned_resources
        )

        commands: list[PlanCommand] = []
        replaced_resources = [
            state.require(handler.logical_name)
            for handler in self.handlers
            if decisions[handler.logical_name] == ChangeKind.REPLACE
        ]
        for resource in reversed(_sort_state_resources_topologically(replaced_resources)):
            handler = self._index[resource.logical_name]
            reason = reasons[resource.logical_name]
            if resource.resource_type == handler.resource_type:
                commands.append(DeleteResourceCommand(handler=handler, reason=reason))
            else:
                commands.append(DeleteStateResourceCommand(resource=resource, reason=reason))

        for resource in reversed(_sort_state_resources_topologically(orphaned_resources)):
            commands.append(DeleteStateResourceCommand(resource=resource, reason="resource is absent from manifest"))

        for handler in self.handlers:
            kind = decisions[handler.logical_name]
            if kind in {ChangeKind.CREATE, ChangeKind.REPLACE}:
                commands.append(CreateResourceCommand(handler=handler, reason=reasons[handler.logical_name]))
            elif kind == ChangeKind.UPDATE:
                commands.append(UpdateResourceCommand(handler=handler, reason=reasons[handler.logical_name]))

        return ExecutionPlan(changes=changes, commands=commands)

    def build_destroy_plan(self, state: InfrastructureState) -> ExecutionPlan:
        changes: list[PlannedChange] = []

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

        for logical_name, resource in sorted(state.resources.items()):
            if logical_name in self._index:
                continue
            changes.append(
                PlannedChange(
                    logical_name=logical_name,
                    resource_type=resource.resource_type,
                    kind=ChangeKind.DELETE,
                    reason="resource is present in state but absent from the current manifest",
                    dependencies=tuple(resource.dependencies),
                ),
            )

        commands: list[PlanCommand] = []
        for resource in reversed(_sort_state_resources_topologically(list(state.resources.values()))):
            handler = self._index.get(resource.logical_name)
            if handler is not None and resource.resource_type == handler.resource_type:
                commands.append(DeleteResourceCommand(handler=handler, reason="resource is present in state"))
            else:
                commands.append(DeleteStateResourceCommand(resource=resource, reason="resource is present in state"))

        return ExecutionPlan(changes=changes, commands=commands)


def _sort_state_resources_topologically(resources: list[ResourceState]) -> list[ResourceState]:
    index = {resource.logical_name: resource for resource in resources}
    sorted_resources: list[ResourceState] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(resource: ResourceState, path: tuple[str, ...]) -> None:
        if resource.logical_name in visited:
            return
        if resource.logical_name in visiting:
            cycle = " -> ".join([*path, resource.logical_name])
            raise PlanningError(f"State dependency cycle detected: {cycle}")

        visiting.add(resource.logical_name)
        for dependency in resource.dependencies:
            if dependency in index:
                visit(index[dependency], (*path, resource.logical_name))
        visiting.remove(resource.logical_name)
        visited.add(resource.logical_name)
        sorted_resources.append(resource)

    for resource in resources:
        visit(resource, ())

    return sorted_resources
