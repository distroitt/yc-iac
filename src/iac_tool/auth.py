from __future__ import annotations

from pathlib import Path
import json
import os

from pydantic import BaseModel, ConfigDict, model_validator

from .exceptions import AuthenticationError


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    iam_token: str | None = None
    oauth_token: str | None = None
    service_account_key_file: Path | None = None

    @model_validator(mode="after")
    def validate_auth_source(self) -> "AuthConfig":
        sources = [
            bool(self.iam_token),
            bool(self.oauth_token),
            bool(self.service_account_key_file),
        ]
        if sum(sources) != 1:
            raise ValueError(
                "Exactly one authentication source must be configured: iam_token, oauth_token or service_account_key_file",
            )
        if self.service_account_key_file is not None:
            path = self.service_account_key_file.expanduser().resolve()
            if not path.exists():
                raise ValueError(f"Service account key file does not exist: {path}")
            self.service_account_key_file = path
        return self


def _read_json_file(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AuthenticationError(f"Unable to read auth config: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AuthenticationError(f"Invalid JSON auth config: {path}") from exc


def load_auth_config(path: Path | None = None) -> AuthConfig:
    payload: dict[str, object] = {}

    candidate_paths: list[Path] = []
    if path is not None:
        candidate_paths.append(path.expanduser().resolve())
    else:
        default_path = Path(".iac-tool-auth.json").resolve()
        if default_path.exists():
            candidate_paths.append(default_path)

    for candidate in candidate_paths:
        if not candidate.exists():
            raise AuthenticationError(f"Auth config file does not exist: {candidate}")
        payload.update(_read_json_file(candidate))

    env_payload = {
        "iam_token": os.getenv("YC_IAM_TOKEN"),
        "oauth_token": os.getenv("YC_OAUTH_TOKEN"),
        "service_account_key_file": os.getenv("YC_SERVICE_ACCOUNT_KEY_FILE"),
    }

    for key, value in env_payload.items():
        if value:
            payload[key] = value

    if not payload:
        raise AuthenticationError(
            "Authentication is not configured. Set YC_IAM_TOKEN, YC_OAUTH_TOKEN, YC_SERVICE_ACCOUNT_KEY_FILE "
            "or create .iac-tool-auth.json",
        )

    try:
        return AuthConfig.model_validate(payload)
    except Exception as exc:
        raise AuthenticationError(str(exc)) from exc

