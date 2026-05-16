from __future__ import annotations

from ipaddress import ip_network
from pathlib import Path
import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import yaml

from .exceptions import ManifestError


NAME_PATTERN = re.compile(r"^[a-z]([-a-z0-9]{1,61}[a-z0-9])?$")
LABEL_KEY_PATTERN = re.compile(r"^[a-z][-_./@0-9a-z]{0,62}$")
LABEL_VALUE_PATTERN = re.compile(r"^[-_./@0-9A-Za-z]{0,63}$")
PLACEHOLDER_PREFIXES = ("your-", "example-", "replace-me")


def _validate_name(value: str, field_name: str) -> str:
    if not NAME_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must match Yandex Cloud naming rules and use lowercase Latin letters, numbers and hyphens",
        )
    return value


def _validate_placeholder(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith(PLACEHOLDER_PREFIXES) or "placeholder" in normalized:
        raise ValueError(
            f"{field_name} still contains a template value ('{value}'). Replace it with a real value before running validate/plan/apply",
        )
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProviderConfig(StrictModel):
    folder_id: str = Field(min_length=1)
    zone_id: str = Field(min_length=1)
    project_name: str = Field(min_length=3, max_length=63)

    @field_validator("folder_id")
    @classmethod
    def validate_folder_id(cls, value: str) -> str:
        return _validate_placeholder(value, "provider.folder_id")


class NetworkConfig(StrictModel):
    logical_name: str = "network"
    name: str
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_name(value, "network.name")

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not LABEL_KEY_PATTERN.match(key):
                raise ValueError(f"Invalid label key: {key}")
            if not LABEL_VALUE_PATTERN.match(item):
                raise ValueError(f"Invalid label value for {key}: {item}")
        return value


class DiskConfig(StrictModel):
    logical_name: str = "disk"
    name: str
    size_gb: int = Field(default=10, ge=4)
    type_id: str = "network-hdd"
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_name(value, "disk.name")

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return NetworkConfig.validate_labels(value)


class SubnetConfig(StrictModel):
    logical_name: str = "subnet"
    name: str
    network: str = "network"
    cidr: str
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_name(value, "subnet.name")

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, value: str) -> str:
        network = ip_network(value, strict=True)
        if network.version != 4:
            raise ValueError("Only IPv4 CIDR blocks are supported")
        prefix = network.prefixlen
        if prefix < 16 or prefix > 28:
            raise ValueError("Subnet mask must be between /16 and /28 for this MVP")
        return value

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return NetworkConfig.validate_labels(value)


class InstanceConfig(StrictModel):
    logical_name: str = "instance"
    name: str
    subnet: str = "subnet"
    platform_id: str = "standard-v3"
    cores: int = Field(default=2, ge=2)
    memory_gb: int = Field(default=2, ge=1)
    boot_disk_gb: int = Field(default=10, ge=10)
    disk_type: str = "network-hdd"
    image_family: str = "ubuntu-2204-lts"
    image_folder_id: str = "standard-images"
    username: str = Field(min_length=1, max_length=32)
    ssh_public_key_path: Path
    assign_public_ip: bool = True
    preemptible: bool = False
    data_disks: list[str] = Field(default_factory=list)
    security_groups: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    service_account_id: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_name(value, "instance.name")

    @field_validator("ssh_public_key_path")
    @classmethod
    def validate_ssh_key_path(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.exists():
            raise ValueError(f"SSH public key was not found: {path}")
        if not path.is_file():
            raise ValueError(f"SSH public key path must point to a file: {path}")
        return path

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return NetworkConfig.validate_labels(value)


class SecurityGroupRuleConfig(StrictModel):
    protocol: str = "ANY"
    cidr_blocks: Annotated[list[str], Field(min_length=1)]
    from_port: int | None = Field(default=None, ge=0, le=65535)
    to_port: int | None = Field(default=None, ge=0, le=65535)
    description: str | None = Field(default=None, max_length=256)

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.match(r"^[A-Z0-9-]+$", normalized):
            raise ValueError("security group protocol must use uppercase letters, numbers or hyphens")
        return normalized

    @field_validator("cidr_blocks")
    @classmethod
    def validate_cidr_blocks(cls, value: list[str]) -> list[str]:
        for cidr in value:
            ip_network(cidr, strict=False)
        return value

    @model_validator(mode="after")
    def validate_ports(self) -> "SecurityGroupRuleConfig":
        if (self.from_port is None) != (self.to_port is None):
            raise ValueError("from_port and to_port must be specified together")
        if self.from_port is not None and self.to_port is not None and self.from_port > self.to_port:
            raise ValueError("from_port cannot be greater than to_port")
        return self


class SecurityGroupConfig(StrictModel):
    logical_name: str = "security-group"
    name: str
    network: str
    ingress_rules: list[SecurityGroupRuleConfig] = Field(default_factory=list)
    egress_rules: list[SecurityGroupRuleConfig] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_name(value, "security_group.name")

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return NetworkConfig.validate_labels(value)


class Manifest(StrictModel):
    provider: ProviderConfig
    networks: list[NetworkConfig] = Field(default_factory=list)
    security_groups: list[SecurityGroupConfig] = Field(default_factory=list)
    subnets: list[SubnetConfig] = Field(default_factory=list)
    disks: list[DiskConfig] = Field(default_factory=list)
    instances: list[InstanceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_links(self) -> "Manifest":
        network_names = {network.logical_name for network in self.networks}
        security_group_names = {security_group.logical_name for security_group in self.security_groups}
        subnet_names = {subnet.logical_name for subnet in self.subnets}
        disk_names = {disk.logical_name for disk in self.disks}

        seen_logical_names: set[str] = set()
        for resource in [*self.networks, *self.security_groups, *self.subnets, *self.disks, *self.instances]:
            if resource.logical_name in seen_logical_names:
                raise ValueError(f"Duplicate logical_name detected: {resource.logical_name}")
            seen_logical_names.add(resource.logical_name)

        for security_group in self.security_groups:
            if security_group.network not in network_names:
                raise ValueError(
                    f"security group '{security_group.logical_name}' references unknown network '{security_group.network}'",
                )

        for subnet in self.subnets:
            if subnet.network not in network_names:
                raise ValueError(f"subnet '{subnet.logical_name}' references unknown network '{subnet.network}'")

        for instance in self.instances:
            if instance.subnet not in subnet_names:
                raise ValueError(f"instance '{instance.logical_name}' references unknown subnet '{instance.subnet}'")
            for security_group in instance.security_groups:
                if security_group not in security_group_names:
                    raise ValueError(
                        f"instance '{instance.logical_name}' references unknown security group '{security_group}'",
                    )
            for data_disk in instance.data_disks:
                if data_disk not in disk_names:
                    raise ValueError(f"instance '{instance.logical_name}' references unknown disk '{data_disk}'")

        return self


def _normalize_collection_sections(payload: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(payload)
    section_pairs = (
        ("network", "networks"),
        ("security_group", "security_groups"),
        ("subnet", "subnets"),
        ("disk", "disks"),
        ("instance", "instances"),
    )

    for singular, plural in section_pairs:
        singular_present = singular in prepared
        plural_present = plural in prepared
        if singular_present and plural_present:
            raise ManifestError(f"Use either '{singular}' or '{plural}', but not both in the same manifest")
        if singular_present:
            singular_payload = prepared.pop(singular)
            if not isinstance(singular_payload, dict):
                raise ManifestError(f"Section '{singular}' must be a mapping")
            prepared[plural] = [singular_payload]
            continue
        if plural_present:
            plural_payload = prepared[plural]
            if plural_payload is None:
                prepared[plural] = []
            elif not isinstance(plural_payload, list):
                raise ManifestError(f"Section '{plural}' must be a list")
            continue
        prepared[plural] = []

    return prepared


def _prepare_manifest_payload(payload: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    prepared = _normalize_collection_sections(payload)
    prepared_instances: list[dict[str, Any]] = []

    for instance_payload in prepared.get("instances", []):
        if not isinstance(instance_payload, dict):
            raise ManifestError("Each item in 'instances' must be a mapping")
        normalized_instance = dict(instance_payload)
        ssh_key_path = normalized_instance.get("ssh_public_key_path")
        if ssh_key_path:
            ssh_path = Path(str(ssh_key_path)).expanduser()
            if not ssh_path.is_absolute():
                ssh_path = (manifest_path.parent / ssh_path).resolve()
            normalized_instance["ssh_public_key_path"] = str(ssh_path)
        prepared_instances.append(normalized_instance)

    prepared["instances"] = prepared_instances
    return prepared


def load_manifest(path: Path) -> Manifest:
    manifest_path = path.expanduser().resolve()
    try:
        raw_content = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Unable to read manifest file: {manifest_path}") from exc

    try:
        payload = yaml.safe_load(raw_content)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Unable to parse YAML manifest: {manifest_path}") from exc

    if not isinstance(payload, dict):
        raise ManifestError("Manifest must be a YAML mapping at the top level")

    prepared_payload = _prepare_manifest_payload(payload, manifest_path)

    try:
        return Manifest.model_validate(prepared_payload)
    except ValidationError as exc:
        raise ManifestError(str(exc)) from exc
