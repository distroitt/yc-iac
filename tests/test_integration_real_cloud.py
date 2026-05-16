from pathlib import Path
import os
import uuid

import pytest

from iac_tool.auth import load_auth_config
from iac_tool.executor import PlanExecutor
from iac_tool.facade import YandexCloudFacade
from iac_tool.manifest import load_manifest
from iac_tool.planner import Planner
from iac_tool.state import StateStore


pytestmark = pytest.mark.integration


def test_real_cloud_apply_and_destroy(tmp_path: Path) -> None:
    if os.getenv("YC_RUN_INTEGRATION") != "1":
        pytest.skip("Set YC_RUN_INTEGRATION=1 to run the real cloud integration test")

    folder_id = os.getenv("YC_TEST_FOLDER_ID")
    zone_id = os.getenv("YC_TEST_ZONE_ID")
    ssh_key_path = os.getenv("YC_TEST_SSH_PUBLIC_KEY_PATH")
    if not all([folder_id, zone_id, ssh_key_path]):
        pytest.skip("YC_TEST_FOLDER_ID, YC_TEST_ZONE_ID and YC_TEST_SSH_PUBLIC_KEY_PATH must be configured")

    suffix = uuid.uuid4().hex[:8]
    manifest_path = tmp_path / "integration-manifest.yaml"
    manifest_path.write_text(
        f"""
provider:
  folder_id: "{folder_id}"
  zone_id: "{zone_id}"
  project_name: "oop-course-work"

network:
  logical_name: "network"
  name: "oop-network-{suffix}"

subnet:
  logical_name: "subnet"
  name: "oop-subnet-{suffix}"
  network: "network"
  cidr: "10.20.0.0/24"

instance:
  logical_name: "instance"
  name: "oop-instance-{suffix}"
  subnet: "subnet"
  username: "yc-user"
  ssh_public_key_path: "{ssh_key_path}"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)
    planner = Planner.from_manifest(manifest)
    store = StateStore.for_manifest(manifest_path)
    executor = PlanExecutor(YandexCloudFacade(load_auth_config()), store)

    apply_plan = planner.build_apply_plan(store.load())
    assert not apply_plan.is_noop
    executor.execute(apply_plan)

    assert planner.build_apply_plan(store.load()).is_noop

    destroy_plan = planner.build_destroy_plan(store.load())
    executor.execute(destroy_plan)

    assert not store.load().resources
