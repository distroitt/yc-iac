class IaCToolError(Exception):
    """Base application error."""


class ManifestError(IaCToolError):
    """Raised when the manifest is invalid."""


class PlanningError(IaCToolError):
    """Raised when a plan cannot be constructed."""


class ExecutionError(IaCToolError):
    """Raised when a plan command fails."""


class CloudProviderError(IaCToolError):
    """Raised when the cloud provider returns an error."""


class AuthenticationError(IaCToolError):
    """Raised when authentication settings are missing or invalid."""

