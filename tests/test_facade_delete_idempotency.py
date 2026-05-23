import pytest

from iac_tool.auth import AuthConfig
from iac_tool.exceptions import CloudProviderError
from iac_tool.facade import YandexCloudFacade


class FakeGrpcStatus:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class FakeGrpcError(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = FakeGrpcStatus(status)

    def code(self) -> FakeGrpcStatus:
        return self.status


class FakeDeleteClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def Delete(self, request: object) -> object:
        raise self.exc


class FakeSdk:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def client(self, stub_class: object) -> FakeDeleteClient:
        return FakeDeleteClient(self.exc)


def _facade_with_delete_error(exc: Exception) -> YandexCloudFacade:
    facade = YandexCloudFacade(AuthConfig(iam_token="token"))
    facade._sdk = FakeSdk(exc)
    return facade


@pytest.mark.parametrize(
    ("method_name", "resource_id"),
    [
        ("delete_network", "network-id"),
        ("delete_subnet", "subnet-id"),
        ("delete_disk", "disk-id"),
        ("delete_security_group", "security-group-id"),
        ("delete_instance", "instance-id"),
    ],
)
def test_delete_treats_not_found_as_success(method_name: str, resource_id: str) -> None:
    facade = _facade_with_delete_error(FakeGrpcError("StatusCode.NOT_FOUND"))

    getattr(facade, method_name)(resource_id)


def test_delete_keeps_non_not_found_errors() -> None:
    facade = _facade_with_delete_error(FakeGrpcError("StatusCode.PERMISSION_DENIED"))

    with pytest.raises(CloudProviderError, match="Failed to submit instance deletion request"):
        facade.delete_instance("instance-id")
