from __future__ import annotations

from pathlib import Path
import json

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import PlanningError


class ResourceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_name: str
    resource_type: str
    resource_id: str
    config_hash: str
    dependencies: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class InfrastructureState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    resources: dict[str, ResourceState] = Field(default_factory=dict)

    def get(self, logical_name: str) -> ResourceState | None:
        return self.resources.get(logical_name)

    def require(self, logical_name: str) -> ResourceState:
        resource = self.get(logical_name)
        if resource is None:
            raise PlanningError(f"Resource '{logical_name}' is missing in state")
        return resource

    def put(self, resource: ResourceState) -> None:
        self.resources[resource.logical_name] = resource

    def delete(self, logical_name: str) -> None:
        self.resources.pop(logical_name, None)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    @classmethod
    def for_manifest(cls, manifest_path: Path, state_file: Path | None = None) -> "StateStore":
        target = state_file.expanduser() if state_file else manifest_path.expanduser().resolve().parent / "state.json"
        return cls(target)

    def load(self) -> InfrastructureState:
        if not self.path.exists():
            return InfrastructureState()
        content = json.loads(self.path.read_text(encoding="utf-8"))
        return InfrastructureState.model_validate(content)

    def save(self, state: InfrastructureState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

