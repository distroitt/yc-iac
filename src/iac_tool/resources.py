from __future__ import annotations

from abc import ABC, abstractmethod
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any

from .exceptions import ExecutionError
from .manifest import DiskConfig, InstanceConfig, Manifest, NetworkConfig, ProviderConfig, SecurityGroupConfig, SubnetConfig
from .state import InfrastructureState, ResourceState

if TYPE_CHECKING:
    from .facade import YandexCloudFacade


def _stable_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


class CloudResourceHandler(ABC):
    def __init__(self, provider: ProviderConfig) -> None:
        self.provider = provider

    @property
    @abstractmethod
    def resource_type(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def logical_name(self) -> str:
        raise NotImplementedError

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ()

    @abstractmethod
    def fingerprint_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create(self, facade: "YandexCloudFacade", state: InfrastructureState) -> ResourceState:
        raise NotImplementedError

    @abstractmethod
    def delete(self, facade: "YandexCloudFacade", resource: ResourceState) -> None:
        raise NotImplementedError

    def can_update(self, resource: ResourceState) -> bool:
        return False

    def dependency_change_requires_replace(self, dependency: str, dependency_kind: str) -> bool:
        return True

    def update(self, facade: "YandexCloudFacade", state: InfrastructureState, resource: ResourceState) -> ResourceState:
        raise ExecutionError(f"Resource type '{self.resource_type}' does not support in-place updates")

    def config_hash(self) -> str:
        return _stable_hash(self.fingerprint_payload())

    def build_state(self, resource_id: str, metadata: dict[str, str] | None = None) -> ResourceState:
        return ResourceState(
            logical_name=self.logical_name,
            resource_type=self.resource_type,
            resource_id=resource_id,
            config_hash=self.config_hash(),
            dependencies=list(self.dependencies),
            metadata=metadata or {},
        )


class NetworkResourceHandler(CloudResourceHandler):
    def __init__(self, provider: ProviderConfig, config: NetworkConfig) -> None:
        super().__init__(provider)
        self.config = config

    @property
    def resource_type(self) -> str:
        return "network"

    @property
    def logical_name(self) -> str:
        return self.config.logical_name

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "labels": self.config.labels,
        }

    def create(self, facade: "YandexCloudFacade", state: InfrastructureState) -> ResourceState:
        resource_id = facade.create_network(
            folder_id=self.provider.folder_id,
            name=self.config.name,
            labels=self.config.labels,
        )
        return self.build_state(resource_id)

    def delete(self, facade: "YandexCloudFacade", resource: ResourceState) -> None:
        facade.delete_network(resource.resource_id)


class SubnetResourceHandler(CloudResourceHandler):
    def __init__(self, provider: ProviderConfig, config: SubnetConfig) -> None:
        super().__init__(provider)
        self.config = config

    @property
    def resource_type(self) -> str:
        return "subnet"

    @property
    def logical_name(self) -> str:
        return self.config.logical_name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.config.network,)

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "network": self.config.network,
            "cidr": self.config.cidr,
            "zone_id": self.provider.zone_id,
            "labels": self.config.labels,
        }

    def create(self, facade: "YandexCloudFacade", state: InfrastructureState) -> ResourceState:
        network_state = state.get(self.config.network)
        if network_state is None:
            raise ExecutionError(f"Dependency '{self.config.network}' is missing from state")
        resource_id = facade.create_subnet(
            folder_id=self.provider.folder_id,
            network_id=network_state.resource_id,
            zone_id=self.provider.zone_id,
            name=self.config.name,
            cidr=self.config.cidr,
            labels=self.config.labels,
        )
        return self.build_state(resource_id)

    def delete(self, facade: "YandexCloudFacade", resource: ResourceState) -> None:
        facade.delete_subnet(resource.resource_id)


class DiskResourceHandler(CloudResourceHandler):
    def __init__(self, provider: ProviderConfig, config: DiskConfig) -> None:
        super().__init__(provider)
        self.config = config

    @property
    def resource_type(self) -> str:
        return "disk"

    @property
    def logical_name(self) -> str:
        return self.config.logical_name

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "size_gb": self.config.size_gb,
            "type_id": self.config.type_id,
            "zone_id": self.provider.zone_id,
            "labels": self.config.labels,
        }

    def create(self, facade: "YandexCloudFacade", state: InfrastructureState) -> ResourceState:
        resource_id = facade.create_disk(
            folder_id=self.provider.folder_id,
            zone_id=self.provider.zone_id,
            name=self.config.name,
            size_gb=self.config.size_gb,
            type_id=self.config.type_id,
            labels=self.config.labels,
        )
        return self.build_state(resource_id)

    def delete(self, facade: "YandexCloudFacade", resource: ResourceState) -> None:
        facade.delete_disk(resource.resource_id)


class SecurityGroupResourceHandler(CloudResourceHandler):
    def __init__(self, provider: ProviderConfig, config: SecurityGroupConfig) -> None:
        super().__init__(provider)
        self.config = config

    @property
    def resource_type(self) -> str:
        return "security_group"

    @property
    def logical_name(self) -> str:
        return self.config.logical_name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.config.network,)

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "network": self.config.network,
            "ingress_rules": [rule.model_dump(mode="json") for rule in self.config.ingress_rules],
            "egress_rules": [rule.model_dump(mode="json") for rule in self.config.egress_rules],
            "labels": self.config.labels,
        }

    def create(self, facade: "YandexCloudFacade", state: InfrastructureState) -> ResourceState:
        network_state = state.get(self.config.network)
        if network_state is None:
            raise ExecutionError(f"Dependency '{self.config.network}' is missing from state")
        resource_id = facade.create_security_group(
            folder_id=self.provider.folder_id,
            network_id=network_state.resource_id,
            name=self.config.name,
            labels=self.config.labels,
            ingress_rules=[rule.model_dump(mode="json") for rule in self.config.ingress_rules],
            egress_rules=[rule.model_dump(mode="json") for rule in self.config.egress_rules],
        )
        return self.build_state(resource_id)

    def delete(self, facade: "YandexCloudFacade", resource: ResourceState) -> None:
        facade.delete_security_group(resource.resource_id)


class InstanceResourceHandler(CloudResourceHandler):
    def __init__(self, provider: ProviderConfig, config: InstanceConfig) -> None:
        super().__init__(provider)
        self.config = config

    @property
    def resource_type(self) -> str:
        return "instance"

    @property
    def logical_name(self) -> str:
        return self.config.logical_name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.config.subnet, *self.config.security_groups, *self.config.data_disks)

    def fingerprint_payload(self) -> dict[str, Any]:
        ssh_key_contents = self.config.ssh_public_key_path.read_text(encoding="utf-8").strip()
        return {
            "name": self.config.name,
            "subnet": self.config.subnet,
            "platform_id": self.config.platform_id,
            "cores": self.config.cores,
            "memory_gb": self.config.memory_gb,
            "boot_disk_gb": self.config.boot_disk_gb,
            "disk_type": self.config.disk_type,
            "image_family": self.config.image_family,
            "image_folder_id": self.config.image_folder_id,
            "username": self.config.username,
            "ssh_public_key": ssh_key_contents,
            "assign_public_ip": self.config.assign_public_ip,
            "preemptible": self.config.preemptible,
            "data_disks": self.config.data_disks,
            "security_groups": self.config.security_groups,
            "labels": self.config.labels,
            "service_account_id": self.config.service_account_id,
            "zone_id": self.provider.zone_id,
        }

    def _fingerprint_payload_with_security_groups(self, security_groups: list[str]) -> dict[str, Any]:
        payload = self.fingerprint_payload()
        payload["security_groups"] = security_groups
        return payload

    def can_update(self, resource: ResourceState) -> bool:
        if resource.resource_type != self.resource_type:
            return False
        old_security_groups = [
            dependency
            for dependency in resource.dependencies
            if dependency != self.config.subnet and dependency not in self.config.data_disks
        ]
        old_payload = self._fingerprint_payload_with_security_groups(old_security_groups)
        return _stable_hash(old_payload) == resource.config_hash

    def dependency_change_requires_replace(self, dependency: str, dependency_kind: str) -> bool:
        if dependency in self.config.security_groups and dependency_kind == "create":
            return False
        return True

    def create(self, facade: "YandexCloudFacade", state: InfrastructureState) -> ResourceState:
        subnet_state = state.get(self.config.subnet)
        if subnet_state is None:
            raise ExecutionError(f"Dependency '{self.config.subnet}' is missing from state")
        security_group_ids: list[str] = []
        for logical_name in self.config.security_groups:
            security_group_state = state.get(logical_name)
            if security_group_state is None:
                raise ExecutionError(f"Dependency '{logical_name}' is missing from state")
            security_group_ids.append(security_group_state.resource_id)
        data_disk_ids: list[str] = []
        for logical_name in self.config.data_disks:
            data_disk_state = state.get(logical_name)
            if data_disk_state is None:
                raise ExecutionError(f"Dependency '{logical_name}' is missing from state")
            data_disk_ids.append(data_disk_state.resource_id)
        resource_id = facade.create_instance(
            folder_id=self.provider.folder_id,
            zone_id=self.provider.zone_id,
            subnet_id=subnet_state.resource_id,
            name=self.config.name,
            labels=self.config.labels,
            platform_id=self.config.platform_id,
            cores=self.config.cores,
            memory_gb=self.config.memory_gb,
            boot_disk_gb=self.config.boot_disk_gb,
            disk_type=self.config.disk_type,
            image_family=self.config.image_family,
            image_folder_id=self.config.image_folder_id,
            username=self.config.username,
            ssh_public_key_path=self.config.ssh_public_key_path,
            assign_public_ip=self.config.assign_public_ip,
            preemptible=self.config.preemptible,
            data_disk_ids=data_disk_ids,
            security_group_ids=security_group_ids,
            service_account_id=self.config.service_account_id,
        )
        return self.build_state(resource_id)

    def delete(self, facade: "YandexCloudFacade", resource: ResourceState) -> None:
        facade.delete_instance(resource.resource_id)

    def update(self, facade: "YandexCloudFacade", state: InfrastructureState, resource: ResourceState) -> ResourceState:
        security_group_ids: list[str] = []
        for logical_name in self.config.security_groups:
            security_group_state = state.get(logical_name)
            if security_group_state is None:
                raise ExecutionError(f"Dependency '{logical_name}' is missing from state")
            security_group_ids.append(security_group_state.resource_id)
        facade.update_instance_security_groups(
            instance_id=resource.resource_id,
            security_group_ids=security_group_ids,
        )
        return self.build_state(resource.resource_id, metadata=resource.metadata)


class ResourceHandlerFactory:
    @staticmethod
    def build(manifest: Manifest) -> list[CloudResourceHandler]:
        handlers: list[CloudResourceHandler] = []
        handlers.extend(NetworkResourceHandler(manifest.provider, network) for network in manifest.networks)
        handlers.extend(SecurityGroupResourceHandler(manifest.provider, security_group) for security_group in manifest.security_groups)
        handlers.extend(SubnetResourceHandler(manifest.provider, subnet) for subnet in manifest.subnets)
        handlers.extend(DiskResourceHandler(manifest.provider, disk) for disk in manifest.disks)
        handlers.extend(InstanceResourceHandler(manifest.provider, instance) for instance in manifest.instances)
        return handlers
