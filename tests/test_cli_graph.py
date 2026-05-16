from pathlib import Path

from typer.testing import CliRunner

from iac_tool.cli import app


def _write_manifest(tmp_path: Path) -> Path:
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAATESTKEY test@example\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "folder-id"
  zone_id: "ru-central1-a"
  project_name: "oop-course-work"

networks:
  - logical_name: "network"
    name: "oop-course-network"

security_groups:
  - logical_name: "ssh-access"
    name: "oop-course-ssh-access"
    network: "network"
    ingress_rules:
      - protocol: "TCP"
        from_port: 22
        to_port: 22
        cidr_blocks:
          - "0.0.0.0/0"
    egress_rules:
      - protocol: "ANY"
        cidr_blocks:
          - "0.0.0.0/0"

subnets:
  - logical_name: "subnet"
    name: "oop-course-subnet"
    network: "network"
    cidr: "10.10.0.0/24"

disks:
  - logical_name: "data-disk"
    name: "oop-course-data-disk"
    size_gb: 10

instances:
  - logical_name: "instance"
    name: "oop-course-vm"
    subnet: "subnet"
    username: "yc-user"
    ssh_public_key_path: "{ssh_key}"
    security_groups:
      - "ssh-access"
    data_disks:
      - "data-disk"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_graph_command_outputs_dot_to_stdout(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_manifest(tmp_path)

    result = runner.invoke(app, ["graph", str(manifest_path)])

    assert result.exit_code == 0
    assert "digraph infrastructure" in result.stdout
    assert 'label="network\\nlogical: network\\nname: oop-course-network"' in result.stdout
    assert 'label="network\\\\nlogical: network\\\\nname: oop-course-network"' not in result.stdout
    assert '"network" -> "ssh-access";' in result.stdout
    assert '"network" -> "subnet";' in result.stdout
    assert '"ssh-access" -> "instance";' in result.stdout
    assert '"subnet" -> "instance";' in result.stdout
    assert '"data-disk" -> "instance";' in result.stdout
    assert "Dependency graph for oop-course-work" in result.stdout


def test_graph_command_writes_dot_to_file(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = _write_manifest(tmp_path)
    output_path = tmp_path / "graph.dot"

    result = runner.invoke(app, ["graph", str(manifest_path), "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert 'label="network\\nlogical: network\\nname: oop-course-network"' in content
    assert 'label="network\\\\nlogical: network\\\\nname: oop-course-network"' not in content
    assert '"network" -> "ssh-access";' in content
    assert '"network" -> "subnet";' in content
    assert '"subnet" -> "instance";' in content
    assert '"data-disk" -> "instance";' in content
    assert f"Graph written to: {output_path.resolve()}" in result.stdout
