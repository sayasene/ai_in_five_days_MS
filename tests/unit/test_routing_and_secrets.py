"""Unit tests for Strategic Model Routing (Flash vs. Pro) and Secret Manager provider."""

from __future__ import annotations

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types as genai_types

from pursuit_auditor.config.secrets import SecretProvider, SecretRetrievalError
from pursuit_auditor.routing import DynamicModelRoutingPlugin, ModelRouter, ModelTier
from tests.conftest import FakeToolContext


class _FakeSecretManagerBackend:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets
        self.calls: int = 0

    def access_secret(self, project_id: str, secret_id: str, version: str = "latest") -> str:
        self.calls += 1
        if secret_id not in self._secrets:
            raise KeyError(secret_id)
        return self._secrets[secret_id]


def test_model_router_assigns_flash_and_pro_tiers_strategically() -> None:
    router = ModelRouter()
    assert router.model_for_agent("intake_normalizer_agent") == router.fast_model
    assert router.model_for_agent("gate3_aesthetic_density_agent") == router.fast_model
    assert router.model_for_agent("gate1_math_reconciler_agent") == router.reasoning_model
    assert router.model_for_agent("gate2_commercial_posture_agent") == router.reasoning_model
    assert router.model_for_agent("gate4_executive_brief_agent") == router.reasoning_model
    assert router.model_for_agent("self_evaluator_agent") == router.reasoning_model

    # Simple coordinator query stays on Fast tier
    simple = router.route(
        agent_name="pursuit_audit_coordinator",
        prompt_text="List available synthetic baselines.",
        audience="internal_leadership",
    )
    assert simple.tier == ModelTier.FAST
    assert simple.escalated_dynamically is False

    # Complex SOW / external partner query escalates dynamically to Reasoning tier
    complex_req = router.route(
        agent_name="pursuit_audit_coordinator",
        prompt_text="Reconcile SOW liability clauses and TCV rate card.",
        audience="customer",
    )
    assert complex_req.tier == ModelTier.REASONING
    assert complex_req.escalated_dynamically is True


@pytest.mark.asyncio
async def test_dynamic_model_routing_plugin_updates_llm_request_model() -> None:
    plugin = DynamicModelRoutingPlugin()
    ctx = FakeToolContext(
        agent_name="pursuit_audit_coordinator",
        state={"target_audience": "partner"},
    )
    req = LlmRequest(
        model="gemini-2.5-flash",
        contents=[
            genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text="Audit partner proposal rate card.")],
            )
        ],
    )
    res = await plugin.before_model_callback(
        callback_context=ctx,  # type: ignore[arg-type]
        llm_request=req,
    )
    assert res is None
    assert req.model == "gemini-2.5-pro"
    assert ctx.state["last_routing_tier"] == "reasoning"


def test_secret_provider_uses_secret_manager_caches_and_rejects_bad_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeSecretManagerBackend({"pursuit-auditor-gemini-api-key": "test-sm-value"})
    provider = SecretProvider(
        project_id="demo-project-123",
        use_secret_manager=True,
        cache_ttl_seconds=60,
        backend=backend,
    )
    val1 = provider.get_secret(secret_id="pursuit-auditor-gemini-api-key", required=True)
    val2 = provider.get_secret(secret_id="pursuit-auditor-gemini-api-key", required=True)
    assert val1 == "test-sm-value"
    assert val2 == "test-sm-value"
    assert backend.calls == 1  # Served from TTL cache on second call

    with pytest.raises(ValueError, match="Invalid secret_id"):
        provider.get_secret(secret_id="../etc/passwd")

    with pytest.raises(SecretRetrievalError):
        provider.get_secret(secret_id="missing-secret", required=True)

    # Local env fallback mode
    monkeypatch.setenv("LOCAL_TEST_TOKEN", "env-val-999")
    local_provider = SecretProvider(project_id="", use_secret_manager=False)
    assert (
        local_provider.get_secret(
            secret_id="local-test-token",
            env_fallback="LOCAL_TEST_TOKEN",
            required=True,
        )
        == "env-val-999"
    )
