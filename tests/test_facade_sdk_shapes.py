from yandex.cloud.compute.v1.disk_service_pb2 import CreateDiskRequest
from yandex.cloud.compute.v1.instance_pb2 import SchedulingPolicy
from yandex.cloud.compute.v1.instance_service_pb2 import (
    AttachedDiskSpec,
    CreateInstanceRequest,
    GetInstanceRequest,
    NetworkInterfaceSpec,
    OneToOneNatSpec,
    PrimaryAddressSpec,
    ResourcesSpec,
)
from yandex.cloud.vpc.v1.security_group_pb2 import CidrBlocks, PortRange
from yandex.cloud.vpc.v1.security_group_service_pb2 import CreateSecurityGroupRequest, SecurityGroupRuleSpec


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
