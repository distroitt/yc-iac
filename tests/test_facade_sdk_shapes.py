from yandex.cloud.compute.v1.disk_service_pb2 import CreateDiskRequest, UpdateDiskRequest
from yandex.cloud.compute.v1.instance_pb2 import SchedulingPolicy
from yandex.cloud.compute.v1.instance_service_pb2 import (
    AttachedDiskSpec,
    CreateInstanceRequest,
    GetInstanceRequest,
    NetworkInterfaceSpec,
    OneToOneNatSpec,
    PrimaryAddressSpec,
    ResourcesSpec,
    UpdateInstanceRequest,
    UpdateInstanceNetworkInterfaceRequest,
)
from google.protobuf.field_mask_pb2 import FieldMask
from yandex.cloud.vpc.v1.security_group_pb2 import CidrBlocks, PortRange
from yandex.cloud.vpc.v1.network_service_pb2 import UpdateNetworkRequest
from yandex.cloud.vpc.v1.security_group_service_pb2 import (
    CreateSecurityGroupRequest,
    SecurityGroupRuleSpec,
    UpdateSecurityGroupRequest,
)
from yandex.cloud.vpc.v1.subnet_service_pb2 import UpdateSubnetRequest
import yaml

from iac_tool.facade import _build_cloud_init_user_data


def test_instance_request_sdk_shapes_are_compatible() -> None:
    ipv4_value = OneToOneNatSpec.DESCRIPTOR.fields_by_name["ip_version"].enum_type.values_by_name["IPV4"].number
    request = CreateInstanceRequest(
        folder_id="folder-id",
        zone_id="ru-central1-a",
        name="demo-instance",
        platform_id="standard-v3",
        resources_spec=ResourcesSpec(
            cores=2,
            memory=2 * 1024 ** 3,
            core_fraction=100,
        ),
        boot_disk_spec=AttachedDiskSpec(
            auto_delete=True,
            disk_spec=AttachedDiskSpec.DiskSpec(
                name="demo-instance-boot",
                type_id="network-hdd",
                size=10 * 1024 ** 3,
                image_id="image-id",
            ),
        ),
        secondary_disk_specs=[
            AttachedDiskSpec(
                auto_delete=False,
                disk_id="data-disk-id",
            ),
        ],
        network_interface_specs=[
            NetworkInterfaceSpec(
                subnet_id="subnet-id",
                security_group_ids=["sg-id"],
                primary_v4_address_spec=PrimaryAddressSpec(
                    one_to_one_nat_spec=OneToOneNatSpec(ip_version=ipv4_value),
                ),
            ),
        ],
        scheduling_policy=SchedulingPolicy(preemptible=False),
    )

    assert request.resources_spec.cores == 2
    assert request.boot_disk_spec.disk_spec.image_id == "image-id"
    assert request.secondary_disk_specs[0].disk_id == "data-disk-id"
    assert request.network_interface_specs[0].subnet_id == "subnet-id"
    assert request.network_interface_specs[0].security_group_ids[0] == "sg-id"


def test_nat_spec_sets_ipv4_explicitly() -> None:
    ipv4_value = OneToOneNatSpec.DESCRIPTOR.fields_by_name["ip_version"].enum_type.values_by_name["IPV4"].number
    nat_spec = OneToOneNatSpec(ip_version=ipv4_value)

    assert nat_spec.ip_version == ipv4_value


def test_get_instance_request_full_view_is_resolved_via_descriptor() -> None:
    full_view = GetInstanceRequest.DESCRIPTOR.fields_by_name["view"].enum_type.values_by_name["FULL"].number
    request = GetInstanceRequest(instance_id="instance-id", view=full_view)

    assert request.view == full_view


def test_update_instance_network_interface_request_supports_security_group_ids() -> None:
    request = UpdateInstanceNetworkInterfaceRequest(
        instance_id="instance-id",
        network_interface_index="0",
        update_mask=FieldMask(paths=["security_group_ids"]),
        security_group_ids=["sg-1", "sg-2"],
    )

    assert request.instance_id == "instance-id"
    assert request.network_interface_index == "0"
    assert list(request.update_mask.paths) == ["security_group_ids"]
    assert list(request.security_group_ids) == ["sg-1", "sg-2"]


def test_update_resource_requests_support_mutable_fields() -> None:
    network_request = UpdateNetworkRequest(
        network_id="network-id",
        update_mask=FieldMask(paths=["name", "labels"]),
        name="renamed-network",
        labels={"env": "demo"},
    )
    subnet_request = UpdateSubnetRequest(
        subnet_id="subnet-id",
        update_mask=FieldMask(paths=["v4_cidr_blocks"]),
        v4_cidr_blocks=["10.10.1.0/24"],
    )
    disk_request = UpdateDiskRequest(
        disk_id="disk-id",
        update_mask=FieldMask(paths=["size"]),
        size=12 * 1024 ** 3,
    )
    instance_request = UpdateInstanceRequest(
        instance_id="instance-id",
        update_mask=FieldMask(paths=["resources_spec", "scheduling_policy"]),
        resources_spec=ResourcesSpec(cores=4, memory=4 * 1024 ** 3, core_fraction=100),
        scheduling_policy=SchedulingPolicy(preemptible=True),
    )

    assert list(network_request.update_mask.paths) == ["name", "labels"]
    assert list(subnet_request.v4_cidr_blocks) == ["10.10.1.0/24"]
    assert disk_request.size == 12 * 1024 ** 3
    assert instance_request.resources_spec.cores == 4
    assert instance_request.scheduling_policy.preemptible is True


def test_cloud_init_user_data_creates_requested_ssh_user() -> None:
    user_data = _build_cloud_init_user_data("yc-user", "ssh-ed25519 AAAATESTKEY test@example")
    payload = yaml.safe_load(user_data.removeprefix("#cloud-config\n"))

    assert payload["datasource"]["Ec2"]["strict_id"] is False
    assert payload["ssh_pwauth"] is False
    assert payload["users"][0]["name"] == "yc-user"
    assert payload["users"][0]["groups"] == "sudo"
    assert payload["users"][0]["shell"] == "/bin/bash"
    assert payload["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 AAAATESTKEY test@example"]


def test_instance_request_accepts_cloud_init_user_data_metadata() -> None:
    user_data = _build_cloud_init_user_data("yc-user", "ssh-ed25519 AAAATESTKEY test@example")
    request = CreateInstanceRequest(
        folder_id="folder-id",
        zone_id="ru-central1-a",
        name="demo-instance",
        platform_id="standard-v3",
        resources_spec=ResourcesSpec(cores=2, memory=2 * 1024 ** 3, core_fraction=100),
        metadata={"user-data": user_data},
    )

    assert "yc-user" in request.metadata["user-data"]
    assert "ssh_authorized_keys" in request.metadata["user-data"]
    assert "ssh-keys" not in request.metadata


def test_disk_request_sdk_shapes_are_compatible() -> None:
    request = CreateDiskRequest(
        folder_id="folder-id",
        zone_id="ru-central1-a",
        name="data-disk",
        size=10 * 1024 ** 3,
        type_id="network-hdd",
        labels={"env": "demo"},
    )

    assert request.zone_id == "ru-central1-a"
    assert request.size == 10 * 1024 ** 3
    assert request.type_id == "network-hdd"


def test_security_group_request_sdk_shapes_are_compatible() -> None:
    ingress_value = SecurityGroupRuleSpec.DESCRIPTOR.fields_by_name["direction"].enum_type.values_by_name["INGRESS"].number
    egress_value = SecurityGroupRuleSpec.DESCRIPTOR.fields_by_name["direction"].enum_type.values_by_name["EGRESS"].number
    request = CreateSecurityGroupRequest(
        folder_id="folder-id",
        network_id="network-id",
        name="ssh-access",
        rule_specs=[
            SecurityGroupRuleSpec(
                direction=ingress_value,
                protocol_name="TCP",
                ports=PortRange(from_port=22, to_port=22),
                cidr_blocks=CidrBlocks(v4_cidr_blocks=["0.0.0.0/0"]),
            ),
            SecurityGroupRuleSpec(
                direction=egress_value,
                protocol_name="ANY",
                cidr_blocks=CidrBlocks(v4_cidr_blocks=["0.0.0.0/0"]),
            ),
        ],
    )

    assert request.rule_specs[0].ports.from_port == 22
    assert request.rule_specs[1].protocol_name == "ANY"


def test_update_security_group_request_supports_rule_specs() -> None:
    ingress_value = SecurityGroupRuleSpec.DESCRIPTOR.fields_by_name["direction"].enum_type.values_by_name["INGRESS"].number
    request = UpdateSecurityGroupRequest(
        security_group_id="sg-id",
        update_mask=FieldMask(paths=["rule_specs"]),
        rule_specs=[
            SecurityGroupRuleSpec(
                direction=ingress_value,
                protocol_name="TCP",
                ports=PortRange(from_port=22, to_port=22),
                cidr_blocks=CidrBlocks(v4_cidr_blocks=["0.0.0.0/0"]),
            ),
        ],
    )

    assert list(request.update_mask.paths) == ["rule_specs"]
    assert request.rule_specs[0].protocol_name == "TCP"
