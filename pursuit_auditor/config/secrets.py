"""Secure secret management backed by Google Cloud Secret Manager.

Never hardcodes API keys, database passwords, or credentials. In production
environments (Cloud Run / Agent Engine), secrets are retrieved from Google
Cloud Secret Manager via IAM-authenticated service accounts or mounted via
Cloud Run secret volume/env references provisioned by Terraform.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pursuit_auditor.observability.logging import get_logger

logger = get_logger(__name__)

_SECRET_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,255}$")
_SECRET_VERSION_PATTERN = re.compile(r"^(latest|[0-9]+)$")


class SecretManagerBackend(Protocol):
    """Protocol for secret retrieval backends (enables deterministic unit testing)."""

    def access_secret(self, project_id: str, secret_id: str, version: str = "latest") -> str:
        """Retrieve a secret payload from the backing store."""
        ...


class GoogleCloudSecretManagerBackend:
    """Production backend wrapping `google.cloud.secretmanager.SecretManagerServiceClient`."""

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from google.cloud import secretmanager

            self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    def access_secret(self, project_id: str, secret_id: str, version: str = "latest") -> str:
        client = self._get_client()
        resource_name = f"projects/{project_id}/secrets/{secret_id}/versions/{version}"
        response = client.access_secret_version(request={"name": resource_name})
        return response.payload.data.decode("utf-8")


@dataclass(slots=True)
class _CachedSecret:
    value: str
    expires_at: float


class SecretRetrievalError(RuntimeError):
    """Raised when a required secret cannot be retrieved securely."""


class SecretProvider:
    """Fetches runtime secrets from Google Cloud Secret Manager with local env fallback.

    Secrets are cached in memory with a bounded TTL and are NEVER written to logs
    or serialized into state or tracing attributes.
    """

    def __init__(
        self,
        *,
        project_id: str,
        use_secret_manager: bool = False,
        cache_ttl_seconds: int = 300,
        backend: SecretManagerBackend | None = None,
    ) -> None:
        self._project_id = project_id
        self._use_secret_manager = use_secret_manager
        self._cache_ttl_seconds = cache_ttl_seconds
        self._backend = backend
        self._cache: dict[str, _CachedSecret] = {}

    def get_secret(
        self,
        *,
        secret_id: str,
        env_fallback: str | None = None,
        version: str = "latest",
        required: bool = False,
    ) -> str | None:
        """Fetch a secret by ID using Secret Manager in GCP or env fallback locally.

        Args:
            secret_id: Secret Manager secret identifier (alphanumeric, `_`, `-`).
            env_fallback: Local environment variable name checked when Secret Manager
                is disabled or when Cloud Run mounts the secret as an env var.
            version: Secret Manager version (`"latest"` or numeric version string).
            required: If True, raises `SecretRetrievalError` when unavailable.

        Returns:
            The secret string if available, otherwise `None` (when `required=False`).
        """
        if not _SECRET_NAME_PATTERN.match(secret_id):
            raise ValueError(f"Invalid secret_id format: '{secret_id}'")
        if not _SECRET_VERSION_PATTERN.match(version):
            raise ValueError(f"Invalid secret version: '{version}'")

        cache_key = f"{secret_id}:{version}"
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached and cached.expires_at > now:
            return cached.value

        secret_value: str | None = None
        source = "unavailable"

        # 1. Check Cloud Run Secret Manager injected env var or local dev env var first
        #    when Secret Manager API calls are disabled.
        if not self._use_secret_manager and env_fallback:
            env_val = os.getenv(env_fallback)
            if env_val:
                secret_value = env_val
                source = f"env:{env_fallback}"

        # 2. Retrieve directly from Google Cloud Secret Manager API when enabled.
        if secret_value is None and self._use_secret_manager:
            if not self._project_id or self._project_id == "your-gcp-project-id":
                raise SecretRetrievalError(
                    "GOOGLE_CLOUD_PROJECT must be configured when ENABLE_SECRET_MANAGER=true."
                )
            backend = self._backend or GoogleCloudSecretManagerBackend()
            try:
                secret_value = backend.access_secret(
                    project_id=self._project_id,
                    secret_id=secret_id,
                    version=version,
                )
                source = "gcp_secret_manager"
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "secret_retrieval_failed",
                    secret_id=secret_id,
                    version=version,
                    error_type=type(exc).__name__,
                )
                if env_fallback and os.getenv(env_fallback):
                    secret_value = os.getenv(env_fallback)
                    source = f"env_fallback:{env_fallback}"
                elif required:
                    raise SecretRetrievalError(
                        f"Unable to load secret '{secret_id}' from Secret Manager: {type(exc).__name__}"
                    ) from exc

        if secret_value is not None:
            self._cache[cache_key] = _CachedSecret(
                value=secret_value,
                expires_at=now + self._cache_ttl_seconds,
            )
            # Log ONLY metadata — never log secret payload or length!
            logger.info("secret_loaded", secret_id=secret_id, version=version, source=source)
            return secret_value

        if required:
            raise SecretRetrievalError(
                f"Required secret '{secret_id}' is not configured via Secret Manager or env '{env_fallback}'."
            )
        return None

    def clear_cache(self) -> None:
        """Evict all cached secrets from memory."""
        self._cache.clear()


_default_provider: SecretProvider | None = None


def get_secret_provider(
    *,
    project_id: str | None = None,
    use_secret_manager: bool | None = None,
) -> SecretProvider:
    """Return a lazily initialized process-wide `SecretProvider`."""
    global _default_provider
    if _default_provider is None or project_id is not None or use_secret_manager is not None:
        resolved_project: str = project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or ""
        resolved_sm = (
            use_secret_manager
            if use_secret_manager is not None
            else os.getenv("ENABLE_SECRET_MANAGER", "false").lower() == "true"
        )
        _default_provider = SecretProvider(
            project_id=resolved_project,
            use_secret_manager=resolved_sm,
        )
    return _default_provider
