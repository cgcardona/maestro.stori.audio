"""Tests for Maestro Default UI endpoints (app/api/routes/maestro_ui.py).

Covers placeholders, prompt inspiration cards (random sample), single template
lookup, budget status derivation, auth requirements, and camelCase serialization.
"""

import pytest

from app.data.maestro_ui import PLACEHOLDERS, PROMPT_BY_ID, PROMPT_POOL


# ---------------------------------------------------------------------------
# 1. GET /api/v1/maestro/ui/placeholders
# ---------------------------------------------------------------------------


class TestPlaceholders:

    @pytest.mark.anyio
    async def test_returns_placeholders(self, client, db_session):
        """Happy path — returns a list of placeholder strings."""
        resp = await client.get("/api/v1/maestro/ui/placeholders")
        assert resp.status_code == 200
        data = resp.json()
        assert "placeholders" in data
        assert isinstance(data["placeholders"], list)

    @pytest.mark.anyio
    async def test_at_least_three_placeholders(self, client, db_session):
        """Contract: at least 3 placeholders so the rotation feels varied."""
        data = (await client.get("/api/v1/maestro/ui/placeholders")).json()
        assert len(data["placeholders"]) >= 3

    @pytest.mark.anyio
    async def test_placeholders_are_strings(self, client, db_session):
        """Every placeholder is a non-empty string."""
        data = (await client.get("/api/v1/maestro/ui/placeholders")).json()
        for p in data["placeholders"]:
            assert isinstance(p, str) and len(p) > 0

    @pytest.mark.anyio
    async def test_no_auth_required(self, client, db_session):
        """Placeholders endpoint is public — no auth header needed."""
        resp = await client.get("/api/v1/maestro/ui/placeholders")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 2. GET /api/v1/maestro/prompts
# ---------------------------------------------------------------------------


class TestPrompts:

    @pytest.mark.anyio
    async def test_returns_prompts(self, client, db_session):
        """Happy path — returns a list of prompt items."""
        resp = await client.get("/api/v1/maestro/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompts" in data
        assert isinstance(data["prompts"], list)

    @pytest.mark.anyio
    async def test_returns_exactly_four(self, client, db_session):
        """Endpoint returns exactly 4 items per call."""
        data = (await client.get("/api/v1/maestro/prompts")).json()
        assert len(data["prompts"]) == 4

    @pytest.mark.anyio
    async def test_prompt_item_shape(self, client, db_session):
        """Every prompt item has the required camelCase fields."""
        data = (await client.get("/api/v1/maestro/prompts")).json()
        for item in data["prompts"]:
            assert "id" in item
            assert "title" in item
            assert "preview" in item
            assert "fullPrompt" in item

    @pytest.mark.anyio
    async def test_full_prompt_starts_with_sentinel(self, client, db_session):
        """Every fullPrompt must begin with STORI PROMPT."""
        data = (await client.get("/api/v1/maestro/prompts")).json()
        for item in data["prompts"]:
            assert item["fullPrompt"].startswith("STORI PROMPT"), (
                f"Item '{item['id']}' fullPrompt doesn't start with 'STORI PROMPT'"
            )

    @pytest.mark.anyio
    async def test_full_prompt_contains_required_fields(self, client, db_session):
        """Every fullPrompt contains the mandatory STORI PROMPT spec fields."""
        required_fields = ["Mode:", "Style:", "Key:", "Tempo:", "Role:", "Vibe:", "Request:"]
        data = (await client.get("/api/v1/maestro/prompts")).json()
        for item in data["prompts"]:
            for field in required_fields:
                assert field in item["fullPrompt"], (
                    f"Item '{item['id']}' fullPrompt missing '{field}'"
                )

    @pytest.mark.anyio
    async def test_full_prompt_contains_maestro_dimensions(self, client, db_session):
        """Every fullPrompt uses full spec breadth — all Maestro dimensions present."""
        dimensions = [
            "Harmony:", "Melody:", "Rhythm:", "Dynamics:",
            "Orchestration:", "Effects:", "Expression:", "Texture:",
            "MidiExpressiveness:",
        ]
        data = (await client.get("/api/v1/maestro/prompts")).json()
        for item in data["prompts"]:
            missing = [d for d in dimensions if d not in item["fullPrompt"]]
            assert not missing, (
                f"Item '{item['id']}' missing dimensions: {missing}"
            )

    @pytest.mark.anyio
    async def test_ids_are_unique_in_sample(self, client, db_session):
        """The 4 sampled items have distinct IDs."""
        data = (await client.get("/api/v1/maestro/prompts")).json()
        ids = [p["id"] for p in data["prompts"]]
        assert len(ids) == len(set(ids))

    @pytest.mark.anyio
    async def test_returns_different_samples(self, client, db_session):
        """Multiple calls return different results (probabilistic, but pool is large enough)."""
        results = set()
        for _ in range(6):
            data = (await client.get("/api/v1/maestro/prompts")).json()
            results.add(frozenset(p["id"] for p in data["prompts"]))
        # With a pool of 22 and sample of 4, it's astronomically unlikely
        # that 6 calls all return the same set
        assert len(results) > 1, "All 6 calls returned identical samples — random sampling broken"

    @pytest.mark.anyio
    async def test_preview_is_nonempty(self, client, db_session):
        """Every preview string is non-empty."""
        data = (await client.get("/api/v1/maestro/prompts")).json()
        for item in data["prompts"]:
            assert len(item["preview"]) > 10

    @pytest.mark.anyio
    async def test_no_auth_required(self, client, db_session):
        """Prompts endpoint is public."""
        resp = await client.get("/api/v1/maestro/prompts")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_camel_case_field_name(self, client, db_session):
        """Wire format uses fullPrompt (camelCase), not full_prompt."""
        data = (await client.get("/api/v1/maestro/prompts")).json()
        item = data["prompts"][0]
        assert "fullPrompt" in item
        assert "full_prompt" not in item


# ---------------------------------------------------------------------------
# 3. GET /api/v1/maestro/prompts/{prompt_id}
# ---------------------------------------------------------------------------


class TestSinglePrompt:

    @pytest.mark.anyio
    async def test_returns_prompt_item(self, client, db_session):
        """Happy path — returns a PromptItem from the pool by ID."""
        first_id = next(iter(PROMPT_BY_ID))
        resp = await client.get(f"/api/v1/maestro/prompts/{first_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == first_id
        assert "title" in data
        assert "preview" in data
        assert "fullPrompt" in data

    @pytest.mark.anyio
    async def test_shape_matches_carousel(self, client, db_session):
        """Single-lookup returns the same shape as carousel items (no 'sections' field)."""
        first_id = next(iter(PROMPT_BY_ID))
        data = (await client.get(f"/api/v1/maestro/prompts/{first_id}")).json()
        assert "fullPrompt" in data
        assert "sections" not in data

    @pytest.mark.anyio
    async def test_not_found_returns_404(self, client, db_session):
        """Unknown prompt_id returns 404."""
        resp = await client.get("/api/v1/maestro/prompts/does_not_exist_xyz")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_all_pool_ids_resolvable(self, client, db_session):
        """Every ID in PROMPT_BY_ID resolves to the correct item."""
        for pid, item in PROMPT_BY_ID.items():
            resp = await client.get(f"/api/v1/maestro/prompts/{pid}")
            assert resp.status_code == 200, f"Pool item '{pid}' returned {resp.status_code}"
            data = resp.json()
            assert data["id"] == pid
            assert data["title"] == item.title

    @pytest.mark.anyio
    async def test_full_prompt_matches_pool(self, client, db_session):
        """fullPrompt in response matches the pool's stored value exactly."""
        first_id = next(iter(PROMPT_BY_ID))
        expected = PROMPT_BY_ID[first_id].full_prompt
        data = (await client.get(f"/api/v1/maestro/prompts/{first_id}")).json()
        assert data["fullPrompt"] == expected

    @pytest.mark.anyio
    async def test_camel_case_field_name(self, client, db_session):
        """Wire format uses fullPrompt (camelCase), not full_prompt."""
        first_id = next(iter(PROMPT_BY_ID))
        data = (await client.get(f"/api/v1/maestro/prompts/{first_id}")).json()
        assert "fullPrompt" in data
        assert "full_prompt" not in data

    @pytest.mark.anyio
    async def test_no_auth_required(self, client, db_session):
        """Single-prompt endpoint is public."""
        first_id = next(iter(PROMPT_BY_ID))
        resp = await client.get(f"/api/v1/maestro/prompts/{first_id}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. GET /api/v1/maestro/budget/status
# ---------------------------------------------------------------------------


class TestBudgetStatus:

    @pytest.mark.anyio
    async def test_returns_budget_status(self, client, auth_headers, test_user):
        """Happy path — returns budget status for authenticated user."""
        resp = await client.get("/api/v1/maestro/budget/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "remaining" in data
        assert "total" in data
        assert "state" in data
        assert "sessionsUsed" in data

    @pytest.mark.anyio
    async def test_budget_values_match_user(self, client, auth_headers, test_user):
        """remaining/total match the test user's budget."""
        data = (await client.get(
            "/api/v1/maestro/budget/status", headers=auth_headers
        )).json()
        assert data["remaining"] == test_user.budget_remaining
        assert data["total"] == test_user.budget_limit

    @pytest.mark.anyio
    async def test_sessions_used_zero_for_new_user(self, client, auth_headers, test_user):
        """A fresh user has 0 sessions used."""
        data = (await client.get(
            "/api/v1/maestro/budget/status", headers=auth_headers
        )).json()
        assert data["sessionsUsed"] == 0

    @pytest.mark.anyio
    async def test_requires_auth(self, client, db_session):
        """Returns 401/403 without auth."""
        resp = await client.get("/api/v1/maestro/budget/status")
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_user_not_found(self, client, db_session):
        """Returns 404 if user doesn't exist."""
        from app.auth.tokens import create_access_token
        token = create_access_token(
            user_id="00000000-0000-0000-0000-000000000099",
            expires_hours=1,
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/maestro/budget/status", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_budget_camel_case(self, client, auth_headers, test_user):
        """Budget response uses sessionsUsed, not sessions_used."""
        data = (await client.get(
            "/api/v1/maestro/budget/status", headers=auth_headers
        )).json()
        assert "sessionsUsed" in data
        assert "sessions_used" not in data


# ---------------------------------------------------------------------------
# Budget state derivation (unit tests)
# ---------------------------------------------------------------------------


class TestBudgetStateDerivation:
    """Verify the threshold table is implemented exactly as specified."""

    @pytest.mark.anyio
    async def test_state_normal(self, client, db_session, test_user):
        """remaining >= 1.0 → normal."""
        from app.auth.tokens import create_access_token
        test_user.budget_cents = 500
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        data = (await client.get(
            "/api/v1/maestro/budget/status",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
        assert data["state"] == "normal"

    @pytest.mark.anyio
    async def test_state_low(self, client, db_session, test_user):
        """0.25 <= remaining < 1.0 → low."""
        from app.auth.tokens import create_access_token
        test_user.budget_cents = 50
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        data = (await client.get(
            "/api/v1/maestro/budget/status",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
        assert data["state"] == "low"

    @pytest.mark.anyio
    async def test_state_critical(self, client, db_session, test_user):
        """0 < remaining < 0.25 → critical."""
        from app.auth.tokens import create_access_token
        test_user.budget_cents = 10
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        data = (await client.get(
            "/api/v1/maestro/budget/status",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
        assert data["state"] == "critical"

    @pytest.mark.anyio
    async def test_state_exhausted_zero(self, client, db_session, test_user):
        """remaining == 0 → exhausted."""
        from app.auth.tokens import create_access_token
        test_user.budget_cents = 0
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        data = (await client.get(
            "/api/v1/maestro/budget/status",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
        assert data["state"] == "exhausted"

    @pytest.mark.anyio
    async def test_state_exhausted_negative(self, client, db_session, test_user):
        """remaining < 0 → exhausted."""
        from app.auth.tokens import create_access_token
        test_user.budget_cents = -10
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        data = (await client.get(
            "/api/v1/maestro/budget/status",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
        assert data["state"] == "exhausted"

    @pytest.mark.anyio
    async def test_state_boundary_exactly_025(self, client, db_session, test_user):
        """remaining == 0.25 → low (< 0.25 is critical, == 0.25 is low)."""
        from app.auth.tokens import create_access_token
        test_user.budget_cents = 25
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        data = (await client.get(
            "/api/v1/maestro/budget/status",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
        assert data["state"] == "low"

    @pytest.mark.anyio
    async def test_state_boundary_exactly_100(self, client, db_session, test_user):
        """remaining == 1.0 → normal (< 1.0 is low, == 1.0 is normal)."""
        from app.auth.tokens import create_access_token
        test_user.budget_cents = 100
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        data = (await client.get(
            "/api/v1/maestro/budget/status",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
        assert data["state"] == "normal"


# ---------------------------------------------------------------------------
# Data integrity (unit tests — no HTTP)
# ---------------------------------------------------------------------------


class TestDataIntegrity:
    """Cross-cutting checks on the pool and template data."""

    def test_pool_has_at_least_fifty_items(self):
        """Pool has 50 curated prompts spanning every continent."""
        assert len(PROMPT_POOL) >= 50

    def test_pool_ids_unique(self):
        """All pool IDs are unique."""
        ids = [p.id for p in PROMPT_POOL]
        assert len(ids) == len(set(ids))

    def test_all_prompts_start_with_sentinel(self):
        """Every fullPrompt in the pool starts with STORI PROMPT."""
        for p in PROMPT_POOL:
            assert p.full_prompt.startswith("STORI PROMPT"), (
                f"Pool item '{p.id}' doesn't start with STORI PROMPT"
            )

    def test_all_prompts_have_required_routing_fields(self):
        """Every fullPrompt contains the mandatory routing fields."""
        required = ["Mode:", "Style:", "Key:", "Tempo:", "Role:", "Vibe:", "Request:"]
        for p in PROMPT_POOL:
            for field in required:
                assert field in p.full_prompt, (
                    f"Pool item '{p.id}' missing field '{field}'"
                )

    def test_all_prompts_have_full_spec_breadth(self):
        """Every fullPrompt contains all Maestro dimension blocks."""
        dimensions = [
            "Harmony:", "Melody:", "Rhythm:", "Dynamics:",
            "Orchestration:", "Effects:", "Expression:", "Texture:",
            "MidiExpressiveness:",
        ]
        for p in PROMPT_POOL:
            missing = [d for d in dimensions if d not in p.full_prompt]
            assert not missing, (
                f"Pool item '{p.id}' missing dimensions: {missing}"
            )

    def test_all_prompts_have_nonempty_preview(self):
        """Every pool item has a non-empty preview."""
        for p in PROMPT_POOL:
            assert len(p.preview) > 10

    def test_pool_covers_diverse_styles(self):
        """Pool covers at least 20 distinct genre keywords in titles."""
        titles_lower = " ".join(p.title.lower() for p in PROMPT_POOL)
        genres = [
            "jazz", "trap", "house", "ambient", "funk", "techno",
            "folk", "classical", "afrobeats", "drum", "bossa",
            "synthwave", "psytrance", "reggaeton", "flamenco",
            "gamelan", "zen", "qawwali", "cumbia", "tango",
            "klezmer", "baroque", "bluegrass", "gospel", "rumba",
            "gnawa", "raga", "dancehall", "calypso", "gregorian",
        ]
        found = [g for g in genres if g in titles_lower]
        assert len(found) >= 20, f"Only found genres: {found}"

    def test_prompt_by_id_index_complete(self):
        """PROMPT_BY_ID contains every item in PROMPT_POOL."""
        assert len(PROMPT_BY_ID) == len(PROMPT_POOL)
        for item in PROMPT_POOL:
            assert item.id in PROMPT_BY_ID
            assert PROMPT_BY_ID[item.id] is item

    def test_placeholder_count(self):
        """Seed data has at least 3 placeholders."""
        assert len(PLACEHOLDERS) >= 3

    def test_derive_budget_state_directly(self):
        """Unit-test the helper without hitting the API."""
        from app.api.routes.maestro_ui import _derive_budget_state

        assert _derive_budget_state(5.0) == "normal"
        assert _derive_budget_state(1.0) == "normal"
        assert _derive_budget_state(0.99) == "low"
        assert _derive_budget_state(0.25) == "low"
        assert _derive_budget_state(0.24) == "critical"
        assert _derive_budget_state(0.01) == "critical"
        assert _derive_budget_state(0.0) == "exhausted"
        assert _derive_budget_state(-1.0) == "exhausted"
