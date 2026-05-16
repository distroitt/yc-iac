from pathlib import Path

import pytest

from iac_tool.exceptions import ManifestError
from iac_tool.manifest import load_manifest


def _write_manifest(tmp_path: Path, ssh_key_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network"
    name: "demo-network"

subnets:
  - logical_name: "subnet"
    name: "demo-subnet"
    network: "network"
    cidr: "10.10.0.0/24"

instances:
  - logical_name: "instance"
    name: "demo-instance"
    subnet: "subnet"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key_path}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_load_manifest_resolves_and_validates(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = _write_manifest(tmp_path, ssh_key)

    manifest = load_manifest(manifest_path)

    assert manifest.provider.folder_id == "folder-id"
    assert len(manifest.networks) == 1
    assert len(manifest.subnets) == 1
    assert len(manifest.instances) == 1
    assert manifest.subnets[0].network == "network"
    assert manifest.instances[0].ssh_public_key_path == ssh_key.resolve()


def test_load_manifest_rejects_invalid_links(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = _write_manifest(tmp_path, ssh_key)
    content = manifest_path.read_text(encoding="utf-8").replace('subnet: "subnet"', 'subnet: "wrong-subnet"')
    manifest_path.write_text(content, encoding="utf-8")

    with pytest.raises(ManifestError):
        load_manifest(manifest_path)


def test_load_manifest_rejects_placeholder_folder_id(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = _write_manifest(tmp_path, ssh_key).resolve()
    content = manifest_path.read_text(encoding="utf-8").replace('folder_id: "folder-id"', 'folder_id: "your-folder-id"')
    manifest_path.write_text(content, encoding="utf-8")

    with pytest.raises(ManifestError, match="provider\\.folder_id still contains a template value"):
        load_manifest(manifest_path)


def test_load_manifest_supports_multiple_resources(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "multi-manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network-a"
    name: "demo-network-a"
  - logical_name: "network-b"
    name: "demo-network-b"

security_groups:
  - logical_name: "sg-a"
    name: "demo-sg-a"
    network: "network-a"
    ingress_rules:
      - protocol: "TCP"
        from_port: 22
        to_port: 22
        cidr_blocks:
          - "0.0.0.0/0"

subnets:
  - logical_name: "subnet-a"
    name: "demo-subnet-a"
    network: "network-a"
    cidr: "10.10.0.0/24"
  - logical_name: "subnet-b"
    name: "demo-subnet-b"
    network: "network-b"
    cidr: "10.20.0.0/24"

disks:
  - logical_name: "disk-a"
    name: "demo-disk-a"
    size_gb: 10

instances:
  - logical_name: "instance-a"
    name: "demo-instance-a"
    subnet: "subnet-a"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
    security_groups:
      - "sg-a"
    data_disks:
      - "disk-a"
  - logical_name: "instance-b"
    name: "demo-instance-b"
    subnet: "subnet-b"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert [resource.logical_name for resource in manifest.networks] == ["network-a", "network-b"]
    assert [resource.logical_name for resource in manifest.security_groups] == ["sg-a"]
    assert [resource.logical_name for resource in manifest.subnets] == ["subnet-a", "subnet-b"]
    assert [resource.logical_name for resource in manifest.disks] == ["disk-a"]
    assert [resource.logical_name for resource in manifest.instances] == ["instance-a", "instance-b"]


def test_load_manifest_rejects_unknown_security_group_reference(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "invalid-security-group-manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network"
    name: "demo-network"

subnets:
  - logical_name: "subnet"
    name: "demo-subnet"
    network: "network"
    cidr: "10.10.0.0/24"

instances:
  - logical_name: "instance"
    name: "demo-instance"
    subnet: "subnet"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
    security_groups:
      - "missing-sg"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="references unknown security group"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_duplicate_logical_names(tmp_path: Path) -> None:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "duplicate-manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "shared"
    name: "demo-network"

subnets:
  - logical_name: "shared"
    name: "demo-subnet"
    network: "shared"
    cidr: "10.10.0.0/24"

instances:
  - logical_name: "instance"
    name: "demo-instance"
    subnet: "shared"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Duplicate logical_name detected"):
        load_manifest(manifest_path)
