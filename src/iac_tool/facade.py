from __future__ import annotations

from pathlib import Path
from ipaddress import ip_network
import json
import time
from typing import Any

from .auth import AuthConfig
from .exceptions import CloudProviderError, ResourceNotFoundError
from .observability import get_logger


GIBIBYTE = 1024 ** 3
logger = get_logger("facade")


class YandexCloudFacade:
    def __init__(
        self,
        auth_config: AuthConfig,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 900.0,
    ) -> None:
        self.auth_config = auth_config
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._sdk: Any | None = None

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = self._build_sdk()
        return self._sdk

    def _build_sdk(self) -> Any:
        try:
            import yandexcloud
        except ImportError as exc:
            raise CloudProviderError(
                "The 'yandexcloud' package is not installed. Install project dependencies before running apply/destroy.",
            ) from exc

        kwargs: dict[str, object] = {}
        if self.auth_config.iam_token:
            kwargs["iam_token"] = self.auth_config.iam_token
        elif self.auth_config.oauth_token:
            kwargs["token"] = self.auth_config.oauth_token
        elif self.auth_config.service_account_key_file:
            sa_key = json.loads(self.auth_config.service_account_key_file.read_text(encoding="utf-8"))
            kwargs["service_account_key"] = sa_key
        else:
            raise CloudProviderError("No authentication source is configured")

        auth_source = "iam_token"
        if self.auth_config.oauth_token:
            auth_source = "oauth_token"
        elif self.auth_config.service_account_key_file:
            auth_source = "service_account_key_file"
        logger.info("Initializing Yandex Cloud SDK using %s authentication", auth_source)
        return yandexcloud.SDK(**kwargs)

    def _wait_for_operation(self, operation_id: str, metadata_cls: type[Any] | None = None) -> Any | None:
        from yandex.cloud.operation.operation_service_pb2 import GetOperationRequest
        from yandex.cloud.operation.operation_service_pb2_grpc import OperationServiceStub

        operation_client = self.sdk.client(OperationServiceStub)
        deadline = time.monotonic() + self.timeout_seconds
        logger.info("Waiting for cloud operation %s", operation_id)

        while True:
            try:
                operation = operation_client.Get(GetOperationRequest(operation_id=operation_id))
            except Exception as exc:
                raise CloudProviderError(f"Failed to poll operation '{operation_id}': {exc}") from exc
            if operation.done:
                error_code = getattr(operation.error, "code", 0)
                if error_code:
                    message = getattr(operation.error, "message", "Unknown operation error")
                    logger.error("Cloud operation %s failed with code %s: %s", operation_id, error_code, message)
                    raise CloudProviderError(
                        f"Cloud operation '{operation_id}' failed with code {error_code}: {message}",
                    )
                if metadata_cls is None:
                    logger.info("Cloud operation %s completed successfully", operation_id)
                    return None
                metadata = metadata_cls()
                operation.metadata.Unpack(metadata)
                logger.info("Cloud operation %s completed successfully", operation_id)
                return metadata

            if time.monotonic() >= deadline:
                raise CloudProviderError(f"Timed out waiting for operation: {operation_id}")

            logger.debug("Cloud operation %s is still running", operation_id)
            time.sleep(self.poll_interval_seconds)

    def _is_not_found_error(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if not callable(code):
            return False
        try:
            grpc_status = code()
        except Exception:
            return False
        return str(grpc_status) == "StatusCode.NOT_FOUND"

    def _raise_describe_error(self, resource_type: str, resource_id: str, exc: Exception) -> None:
        if self._is_not_found_error(exc):
            raise ResourceNotFoundError(
                f"{resource_type.capitalize()} '{resource_id}' was not found in Yandex Cloud",
            ) from exc
        raise CloudProviderError(f"Failed to describe {resource_type} '{resource_id}': {exc}") from exc

    def create_network(self, folder_id: str, name: str, labels: dict[str, str]) -> str:
        from yandex.cloud.vpc.v1.network_service_pb2 import CreateNetworkMetadata, CreateNetworkRequest
        from yandex.cloud.vpc.v1.network_service_pb2_grpc import NetworkServiceStub

        client = self.sdk.client(NetworkServiceStub)
        logger.info("Submitting network creation request for %s", name)
        try:
            operation = client.Create(
                CreateNetworkRequest(
                    folder_id=folder_id,
                    name=name,
                    labels=labels,
                ),
            )
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit network creation request for '{name}': {exc}") from exc
        logger.info("Network creation request accepted for %s, operation_id=%s", name, operation.id)
        metadata = self._wait_for_operation(operation.id, CreateNetworkMetadata)
        return metadata.network_id

    def delete_network(self, network_id: str) -> None:
        from yandex.cloud.vpc.v1.network_service_pb2 import DeleteNetworkRequest
        from yandex.cloud.vpc.v1.network_service_pb2_grpc import NetworkServiceStub

        client = self.sdk.client(NetworkServiceStub)
        logger.info("Submitting network deletion request for %s", network_id)
        try:
            operation = client.Delete(DeleteNetworkRequest(network_id=network_id))
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit network deletion request for '{network_id}': {exc}") from exc
        logger.info("Network deletion request accepted for %s, operation_id=%s", network_id, operation.id)
        self._wait_for_operation(operation.id)

    def describe_network(self, network_id: str) -> dict[str, object]:
        from yandex.cloud.vpc.v1.network_service_pb2 import GetNetworkRequest
        from yandex.cloud.vpc.v1.network_service_pb2_grpc import NetworkServiceStub

        client = self.sdk.client(NetworkServiceStub)
        logger.info("Fetching network %s", network_id)
        try:
            network = client.Get(GetNetworkRequest(network_id=network_id))
        except Exception as exc:
            self._raise_describe_error("network", network_id, exc)
        return {
            "name": network.name,
            "labels": dict(network.labels),
        }

    def create_subnet(
        self,
        folder_id: str,
        network_id: str,
        zone_id: str,
        name: str,
        cidr: str,
        labels: dict[str, str],
    ) -> str:
        from yandex.cloud.vpc.v1.subnet_service_pb2 import CreateSubnetMetadata, CreateSubnetRequest
        from yandex.cloud.vpc.v1.subnet_service_pb2_grpc import SubnetServiceStub

        client = self.sdk.client(SubnetServiceStub)
        logger.info("Submitting subnet creation request for %s", name)
        try:
            operation = client.Create(
                CreateSubnetRequest(
                    folder_id=folder_id,
                    network_id=network_id,
                    zone_id=zone_id,
                    name=name,
                    v4_cidr_blocks=[cidr],
                    labels=labels,
                ),
            )
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit subnet creation request for '{name}': {exc}") from exc
        logger.info("Subnet creation request accepted for %s, operation_id=%s", name, operation.id)
        metadata = self._wait_for_operation(operation.id, CreateSubnetMetadata)
        return metadata.subnet_id

    def delete_subnet(self, subnet_id: str) -> None:
        from yandex.cloud.vpc.v1.subnet_service_pb2 import DeleteSubnetRequest
        from yandex.cloud.vpc.v1.subnet_service_pb2_grpc import SubnetServiceStub

        client = self.sdk.client(SubnetServiceStub)
        logger.info("Submitting subnet deletion request for %s", subnet_id)
        try:
            operation = client.Delete(DeleteSubnetRequest(subnet_id=subnet_id))
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit subnet deletion request for '{subnet_id}': {exc}") from exc
        logger.info("Subnet deletion request accepted for %s, operation_id=%s", subnet_id, operation.id)
        self._wait_for_operation(operation.id)

    def describe_subnet(self, subnet_id: str) -> dict[str, object]:
        from yandex.cloud.vpc.v1.subnet_service_pb2 import GetSubnetRequest
        from yandex.cloud.vpc.v1.subnet_service_pb2_grpc import SubnetServiceStub

        client = self.sdk.client(SubnetServiceStub)
        logger.info("Fetching subnet %s", subnet_id)
        try:
            subnet = client.Get(GetSubnetRequest(subnet_id=subnet_id))
        except Exception as exc:
            self._raise_describe_error("subnet", subnet_id, exc)
        return {
            "name": subnet.name,
            "labels": dict(subnet.labels),
            "network_id": subnet.network_id,
            "zone_id": subnet.zone_id,
            "cidr_blocks": sorted(subnet.v4_cidr_blocks),
        }

    def create_disk(
        self,
        *,
        folder_id: str,
        zone_id: str,
        name: str,
        size_gb: int,
        type_id: str,
        labels: dict[str, str],
    ) -> str:
        from yandex.cloud.compute.v1.disk_service_pb2 import CreateDiskMetadata, CreateDiskRequest
        from yandex.cloud.compute.v1.disk_service_pb2_grpc import DiskServiceStub

        client = self.sdk.client(DiskServiceStub)
        logger.info("Submitting disk creation request for %s", name)
        try:
            operation = client.Create(
                CreateDiskRequest(
                    folder_id=folder_id,
                    zone_id=zone_id,
                    name=name,
                    size=size_gb * GIBIBYTE,
                    type_id=type_id,
                    labels=labels,
                ),
            )
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit disk creation request for '{name}': {exc}") from exc
        logger.info("Disk creation request accepted for %s, operation_id=%s", name, operation.id)
        metadata = self._wait_for_operation(operation.id, CreateDiskMetadata)
        return metadata.disk_id

    def delete_disk(self, disk_id: str) -> None:
        from yandex.cloud.compute.v1.disk_service_pb2 import DeleteDiskRequest
        from yandex.cloud.compute.v1.disk_service_pb2_grpc import DiskServiceStub

        client = self.sdk.client(DiskServiceStub)
        logger.info("Submitting disk deletion request for %s", disk_id)
        try:
            operation = client.Delete(DeleteDiskRequest(disk_id=disk_id))
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit disk deletion request for '{disk_id}': {exc}") from exc
        logger.info("Disk deletion request accepted for %s, operation_id=%s", disk_id, operation.id)
        self._wait_for_operation(operation.id)

    def describe_disk(self, disk_id: str) -> dict[str, object]:
        from yandex.cloud.compute.v1.disk_service_pb2 import GetDiskRequest
        from yandex.cloud.compute.v1.disk_service_pb2_grpc import DiskServiceStub

        client = self.sdk.client(DiskServiceStub)
        logger.info("Fetching disk %s", disk_id)
        try:
            disk = client.Get(GetDiskRequest(disk_id=disk_id))
        except Exception as exc:
            self._raise_describe_error("disk", disk_id, exc)
        return {
            "name": disk.name,
            "labels": dict(disk.labels),
            "type_id": disk.type_id,
            "zone_id": disk.zone_id,
            "size_gb": disk.size // GIBIBYTE,
            "instance_ids": sorted(disk.instance_ids),
        }

    def _build_security_group_rule_specs(
        self,
        *,
        ingress_rules: list[dict[str, Any]],
        egress_rules: list[dict[str, Any]],
    ) -> list[Any]:
        from yandex.cloud.vpc.v1.security_group_pb2 import CidrBlocks, PortRange
        from yandex.cloud.vpc.v1.security_group_service_pb2 import SecurityGroupRuleSpec

        direction_enum = SecurityGroupRuleSpec.DESCRIPTOR.fields_by_name["direction"].enum_type.values_by_name
        rule_specs: list[Any] = []

        for direction_name, rules in (
            ("INGRESS", ingress_rules),
            ("EGRESS", egress_rules),
        ):
            for rule in rules:
                v4_cidr_blocks: list[str] = []
                v6_cidr_blocks: list[str] = []
                for cidr in rule["cidr_blocks"]:
                    parsed = ip_network(cidr, strict=False)
                    if parsed.version == 4:
                        v4_cidr_blocks.append(cidr)
                    else:
                        v6_cidr_blocks.append(cidr)

                kwargs: dict[str, Any] = {
                    "direction": direction_enum[direction_name].number,
                    "protocol_name": rule["protocol"],
                    "cidr_blocks": CidrBlocks(
                        v4_cidr_blocks=v4_cidr_blocks,
                        v6_cidr_blocks=v6_cidr_blocks,
                    ),
                }
                if rule.get("description"):
                    kwargs["description"] = rule["description"]
                if rule.get("from_port") is not None and rule.get("to_port") is not None:
                    kwargs["ports"] = PortRange(
                        from_port=rule["from_port"],
                        to_port=rule["to_port"],
                    )
                rule_specs.append(SecurityGroupRuleSpec(**kwargs))

        return rule_specs

    def create_security_group(
        self,
        *,
        folder_id: str,
        network_id: str,
        name: str,
        labels: dict[str, str],
        ingress_rules: list[dict[str, Any]],
        egress_rules: list[dict[str, Any]],
    ) -> str:
        from yandex.cloud.vpc.v1.security_group_service_pb2 import CreateSecurityGroupMetadata, CreateSecurityGroupRequest
        from yandex.cloud.vpc.v1.security_group_service_pb2_grpc import SecurityGroupServiceStub

        client = self.sdk.client(SecurityGroupServiceStub)
        rule_specs = self._build_security_group_rule_specs(
            ingress_rules=ingress_rules,
            egress_rules=egress_rules,
        )
        logger.info("Submitting security group creation request for %s", name)
        try:
            operation = client.Create(
                CreateSecurityGroupRequest(
                    folder_id=folder_id,
                    network_id=network_id,
                    name=name,
                    labels=labels,
                    rule_specs=rule_specs,
                ),
            )
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit security group creation request for '{name}': {exc}") from exc
        logger.info("Security group creation request accepted for %s, operation_id=%s", name, operation.id)
        metadata = self._wait_for_operation(operation.id, CreateSecurityGroupMetadata)
        return metadata.security_group_id

    def delete_security_group(self, security_group_id: str) -> None:
        from yandex.cloud.vpc.v1.security_group_service_pb2 import DeleteSecurityGroupRequest
        from yandex.cloud.vpc.v1.security_group_service_pb2_grpc import SecurityGroupServiceStub

        client = self.sdk.client(SecurityGroupServiceStub)
        logger.info("Submitting security group deletion request for %s", security_group_id)
        try:
            operation = client.Delete(DeleteSecurityGroupRequest(security_group_id=security_group_id))
        except Exception as exc:
            raise CloudProviderError(
                f"Failed to submit security group deletion request for '{security_group_id}': {exc}",
            ) from exc
        logger.info(
            "Security group deletion request accepted for %s, operation_id=%s",
            security_group_id,
            operation.id,
        )
        self._wait_for_operation(operation.id)

    def describe_security_group(self, security_group_id: str) -> dict[str, object]:
        from yandex.cloud.vpc.v1.security_group_pb2 import SecurityGroupRule
        from yandex.cloud.vpc.v1.security_group_service_pb2 import GetSecurityGroupRequest
        from yandex.cloud.vpc.v1.security_group_service_pb2_grpc import SecurityGroupServiceStub

        direction_values = SecurityGroupRule.DESCRIPTOR.fields_by_name["direction"].enum_type.values_by_number
        client = self.sdk.client(SecurityGroupServiceStub)
        logger.info("Fetching security group %s", security_group_id)
        try:
            security_group = client.Get(GetSecurityGroupRequest(security_group_id=security_group_id))
        except Exception as exc:
            self._raise_describe_error("security group", security_group_id, exc)

        rules: list[dict[str, object]] = []
        for rule in security_group.rules:
            direction_name = direction_values[rule.direction].name
            cidr_blocks = sorted([*rule.cidr_blocks.v4_cidr_blocks, *rule.cidr_blocks.v6_cidr_blocks])
            from_port = rule.ports.from_port if rule.HasField("ports") else None
            to_port = rule.ports.to_port if rule.HasField("ports") else None
            rules.append(
                {
                    "direction": direction_name,
                    "protocol": rule.protocol_name,
                    "cidr_blocks": cidr_blocks,
                    "from_port": from_port,
                    "to_port": to_port,
                    "description": rule.description or None,
                },
            )

        rules.sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True))
        return {
            "name": security_group.name,
            "labels": dict(security_group.labels),
            "network_id": security_group.network_id,
            "rules": rules,
        }

    def resolve_image_id(self, image_folder_id: str, image_family: str) -> str:
        from yandex.cloud.compute.v1.image_service_pb2 import GetImageLatestByFamilyRequest
        from yandex.cloud.compute.v1.image_service_pb2_grpc import ImageServiceStub

        client = self.sdk.client(ImageServiceStub)
        logger.info("Resolving image by family %s in folder %s", image_family, image_folder_id)
        try:
            image = client.GetLatestByFamily(
                GetImageLatestByFamilyRequest(
                    folder_id=image_folder_id,
                    family=image_family,
                ),
            )
        except Exception as exc:
            raise CloudProviderError(
                f"Failed to resolve image family '{image_family}' in folder '{image_folder_id}': {exc}",
            ) from exc
        logger.info("Resolved image family %s to image %s", image_family, image.id)
        return image.id

    def create_instance(
        self,
        *,
        folder_id: str,
        zone_id: str,
        subnet_id: str,
        name: str,
        labels: dict[str, str],
        platform_id: str,
        cores: int,
        memory_gb: int,
        boot_disk_gb: int,
        disk_type: str,
        image_family: str,
        image_folder_id: str,
        username: str,
        ssh_public_key_path: Path,
        assign_public_ip: bool,
        preemptible: bool,
        data_disk_ids: list[str],
        security_group_ids: list[str],
        service_account_id: str | None,
    ) -> str:
        from yandex.cloud.compute.v1.instance_pb2 import SchedulingPolicy
        from yandex.cloud.compute.v1.instance_service_pb2 import (
            AttachedDiskSpec,
            CreateInstanceMetadata,
            CreateInstanceRequest,
            NetworkInterfaceSpec,
            OneToOneNatSpec,
            PrimaryAddressSpec,
            ResourcesSpec,
        )
        from yandex.cloud.compute.v1.instance_service_pb2_grpc import InstanceServiceStub

        image_id = self.resolve_image_id(image_folder_id=image_folder_id, image_family=image_family)
        ssh_public_key = ssh_public_key_path.read_text(encoding="utf-8").strip()

        resources_spec = ResourcesSpec(
            cores=cores,
            memory=memory_gb * GIBIBYTE,
            core_fraction=100,
        )
        disk_spec = AttachedDiskSpec.DiskSpec(
            name=f"{name}-boot",
            type_id=disk_type,
            size=boot_disk_gb * GIBIBYTE,
            image_id=image_id,
        )
        boot_disk_spec = AttachedDiskSpec(
            auto_delete=True,
            disk_spec=disk_spec,
        )
        secondary_disk_specs = [
            AttachedDiskSpec(
                auto_delete=False,
                disk_id=disk_id,
            )
            for disk_id in data_disk_ids
        ]

        primary_v4_address_spec = PrimaryAddressSpec()
        if assign_public_ip:
            primary_v4_address_spec.one_to_one_nat_spec.CopyFrom(
                OneToOneNatSpec(
                    ip_version=OneToOneNatSpec.DESCRIPTOR.fields_by_name["ip_version"].enum_type.values_by_name["IPV4"].number,
                ),
            )

        network_interface_spec = NetworkInterfaceSpec(
            subnet_id=subnet_id,
            primary_v4_address_spec=primary_v4_address_spec,
            security_group_ids=security_group_ids,
        )
        scheduling_policy = SchedulingPolicy(preemptible=preemptible)

        request = CreateInstanceRequest(
            folder_id=folder_id,
            zone_id=zone_id,
            name=name,
            hostname=name,
            platform_id=platform_id,
            labels=labels,
            resources_spec=resources_spec,
            metadata={"ssh-keys": f"{username}:{ssh_public_key}"},
            boot_disk_spec=boot_disk_spec,
            secondary_disk_specs=secondary_disk_specs,
            network_interface_specs=[network_interface_spec],
            scheduling_policy=scheduling_policy,
        )
        if service_account_id:
            request.service_account_id = service_account_id

        client = self.sdk.client(InstanceServiceStub)
        logger.info("Submitting instance creation request for %s", name)
        try:
            operation = client.Create(request)
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit instance creation request for '{name}': {exc}") from exc
        logger.info("Instance creation request accepted for %s, operation_id=%s", name, operation.id)
        metadata = self._wait_for_operation(operation.id, CreateInstanceMetadata)
        return metadata.instance_id

    def delete_instance(self, instance_id: str) -> None:
        from yandex.cloud.compute.v1.instance_service_pb2 import DeleteInstanceRequest
        from yandex.cloud.compute.v1.instance_service_pb2_grpc import InstanceServiceStub

        client = self.sdk.client(InstanceServiceStub)
        logger.info("Submitting instance deletion request for %s", instance_id)
        try:
            operation = client.Delete(DeleteInstanceRequest(instance_id=instance_id))
        except Exception as exc:
            raise CloudProviderError(f"Failed to submit instance deletion request for '{instance_id}': {exc}") from exc
        logger.info("Instance deletion request accepted for %s, operation_id=%s", instance_id, operation.id)
        self._wait_for_operation(operation.id)

    def describe_instance(self, instance_id: str) -> dict[str, object]:
        from yandex.cloud.compute.v1.instance_service_pb2 import GetInstanceRequest
        from yandex.cloud.compute.v1.instance_service_pb2_grpc import InstanceServiceStub

        client = self.sdk.client(InstanceServiceStub)
        logger.info("Fetching instance %s", instance_id)
        full_view = GetInstanceRequest.DESCRIPTOR.fields_by_name["view"].enum_type.values_by_name["FULL"].number
        try:
            instance = client.Get(GetInstanceRequest(instance_id=instance_id, view=full_view))
        except Exception as exc:
            self._raise_describe_error("instance", instance_id, exc)

        primary_interface = instance.network_interfaces[0] if instance.network_interfaces else None
        assign_public_ip = False
        subnet_id = None
        security_group_ids: list[str] = []
        if primary_interface is not None:
            subnet_id = primary_interface.subnet_id or None
            security_group_ids = sorted(primary_interface.security_group_ids)
            assign_public_ip = bool(primary_interface.primary_v4_address.one_to_one_nat.address)

        return {
            "name": instance.name,
            "labels": dict(instance.labels),
            "zone_id": instance.zone_id,
            "platform_id": instance.platform_id,
            "cores": instance.resources.cores,
            "memory_gb": instance.resources.memory // GIBIBYTE,
            "preemptible": instance.scheduling_policy.preemptible,
            "subnet_id": subnet_id,
            "security_group_ids": security_group_ids,
            "assign_public_ip": assign_public_ip,
            "data_disk_ids": sorted(disk.disk_id for disk in instance.secondary_disks),
            "service_account_id": instance.service_account_id or None,
        }
