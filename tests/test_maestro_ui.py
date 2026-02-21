"""Tests for Maestro Default UI endpoints (app/api/routes/maestro_ui.py).

Covers placeholders, prompt chips, prompt cards, single template lookup,
budget status derivation, auth requirements, and camelCase serialization.
"""

import pytest

from app.data.maestro_ui import ALL_TEMPLATE_IDS, CHIPS, CARDS, PLACEHOLDERS, TEMPLATES


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
# 2. GET /api/v1/maestro/prompts/chips
# ---------------------------------------------------------------------------


class TestPromptChips:

    @pytest.mark.anyio
    async def test_returns_chips(self, client, db_session):
        """Happy path — returns a list of chip objects."""
        resp = await client.get("/api/v1/maestro/prompts/chips")
        assert resp.status_code == 200
        data = resp.json()
        assert "chips" in data
        assert isinstance(data["chips"], list)

    @pytest.mark.anyio
    async def test_chip_count_in_sweet_spot(self, client, db_session):
        """6-10 chips is the sweet spot for the flow layout."""
        data = (await client.get("/api/v1/maestro/prompts/chips")).json()
        assert 6 <= len(data["chips"]) <= 10

    @pytest.mark.anyio
    async def test_chip_shape(self, client, db_session):
        """Every chip has the required camelCase fields."""
        data = (await client.get("/api/v1/maestro/prompts/chips")).json()
        for chip in data["chips"]:
            assert "id" in chip
            assert "title" in chip
            assert "icon" in chip
            assert "promptTemplateID" in chip
            assert "fullPrompt" in chip

    @pytest.mark.anyio
    async def test_chip_template_ids_are_resolvable(self, client, db_session):
        """Every chip's promptTemplateID maps to an existing template."""
        data = (await client.get("/api/v1/maestro/prompts/chips")).json()
        for chip in data["chips"]:
            assert chip["promptTemplateID"] in ALL_TEMPLATE_IDS, (
                f"Chip '{chip['id']}' references unknown template '{chip['promptTemplateID']}'"
            )

    @pytest.mark.anyio
    async def test_no_auth_required(self, client, db_session):
        """Chips endpoint is public."""
        resp = await client.get("/api/v1/maestro/prompts/chips")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_full_prompt_is_nonempty(self, client, db_session):
        """Every chip has a non-empty fullPrompt."""
        data = (await client.get("/api/v1/maestro/prompts/chips")).json()
        for chip in data["chips"]:
            assert len(chip["fullPrompt"]) > 10


# ---------------------------------------------------------------------------
# 3. GET /api/v1/maestro/prompts/cards
# ---------------------------------------------------------------------------


class TestPromptCards:

    @pytest.mark.anyio
    async def test_returns_cards(self, client, db_session):
        """Happy path — returns a list of card objects."""
        resp = await client.get("/api/v1/maestro/prompts/cards")
        assert resp.status_code == 200
        data = resp.json()
        assert "cards" in data
        assert isinstance(data["cards"], list)

    @pytest.mark.anyio
    async def test_card_count_in_sweet_spot(self, client, db_session):
        """3-5 cards is the sweet spot for the horizontal scroll."""
        data = (await client.get("/api/v1/maestro/prompts/cards")).json()
        assert 3 <= len(data["cards"]) <= 5

    @pytest.mark.anyio
    async def test_card_shape(self, client, db_session):
        """Every card has the required camelCase fields."""
        data = (await client.get("/api/v1/maestro/prompts/cards")).json()
        for card in data["cards"]:
            assert "id" in card
            assert "title" in card
            assert "description" in card
            assert "previewTags" in card
            assert "templateID" in card
            assert "sections" in card

    @pytest.mark.anyio
    async def test_cards_have_five_sections(self, client, db_session):
        """Each card has exactly 5 sections (STORI PROMPT SPEC v2)."""
        data = (await client.get("/api/v1/maestro/prompts/cards")).json()
        for card in data["cards"]:
            assert len(card["sections"]) == 5, (
                f"Card '{card['id']}' has {len(card['sections'])} sections, expected 5"
            )

    @pytest.mark.anyio
    async def test_section_headings_follow_spec(self, client, db_session):
        """Sections follow STORI PROMPT SPEC v2 headings."""
        expected = ["Style", "Arrangement", "Instruments", "Production Notes", "Creative Intent"]
        data = (await client.get("/api/v1/maestro/prompts/cards")).json()
        for card in data["cards"]:
            headings = [s["heading"] for s in card["sections"]]
            assert headings == expected, (
                f"Card '{card['id']}' headings {headings} != expected {expected}"
            )

    @pytest.mark.anyio
    async def test_preview_tags_max_three(self, client, db_session):
        """previewTags has at most 3 items."""
        data = (await client.get("/api/v1/maestro/prompts/cards")).json()
        for card in data["cards"]:
            assert len(card["previewTags"]) <= 3

    @pytest.mark.anyio
    async def test_card_template_ids_are_resolvable(self, client, db_session):
        """Every card's templateID maps to an existing template."""
        data = (await client.get("/api/v1/maestro/prompts/cards")).json()
        for card in data["cards"]:
            assert card["templateID"] in ALL_TEMPLATE_IDS, (
                f"Card '{card['id']}' references unknown template '{card['templateID']}'"
            )

    @pytest.mark.anyio
    async def test_no_auth_required(self, client, db_session):
        """Cards endpoint is public."""
        resp = await client.get("/api/v1/maestro/prompts/cards")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. GET /api/v1/maestro/prompts/{template_id}
# ---------------------------------------------------------------------------


class TestPromptTemplate:

    @pytest.mark.anyio
    async def test_returns_template(self, client, db_session):
        """Happy path — returns a template by ID."""
        resp = await client.get("/api/v1/maestro/prompts/lofi_chill")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "lofi_chill"
        assert "title" in data
        assert "fullPrompt" in data
        assert "sections" in data

    @pytest.mark.anyio
    async def test_template_not_found(self, client, db_session):
        """Unknown template_id returns 404 with detail."""
        resp = await client.get("/api/v1/maestro/prompts/nonexistent_template")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Template not found"

    @pytest.mark.anyio
    async def test_all_eleven_template_ids_resolvable(self, client, db_session):
        """All 11 template IDs from chips + cards are resolvable."""
        expected_ids = {
            "lofi_chill", "dark_trap", "jazz_trio", "synthwave",
            "cinematic", "funk_groove", "ambient", "deep_house",
            "full_production", "beat_lab", "mood_piece",
        }
        for tid in expected_ids:
            resp = await client.get(f"/api/v1/maestro/prompts/{tid}")
            assert resp.status_code == 200, f"Template '{tid}' returned {resp.status_code}"
            assert resp.json()["id"] == tid

    @pytest.mark.anyio
    async def test_template_has_five_sections(self, client, db_session):
        """Every template has exactly 5 sections."""
        for tid in ALL_TEMPLATE_IDS:
            data = (await client.get(f"/api/v1/maestro/prompts/{tid}")).json()
            assert len(data["sections"]) == 5, (
                f"Template '{tid}' has {len(data['sections'])} sections"
            )

    @pytest.mark.anyio
    async def test_template_sections_have_heading_and_content(self, client, db_session):
        """Each section has heading and content strings."""
        data = (await client.get("/api/v1/maestro/prompts/dark_trap")).json()
        for section in data["sections"]:
            assert "heading" in section
            assert "content" in section
            assert isinstance(section["heading"], str)
            assert isinstance(section["content"], str)

    @pytest.mark.anyio
    async def test_no_auth_required(self, client, db_session):
        """Template endpoint is public."""
        resp = await client.get("/api/v1/maestro/prompts/lofi_chill")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. GET /api/v1/maestro/budget/status
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


# ---------------------------------------------------------------------------
# Budget state derivation (unit tests)
# ---------------------------------------------------------------------------


class TestBudgetStateDerivation:
    """Verify the threshold table is implemented exactly as specified."""

    @pytest.mark.anyio
    async def test_state_normal(self, client, db_session, test_user):
        """remaining >= 1.0 → normal."""
        from app.auth.tokens import create_access_token

        test_user.budget_cents = 500  # $5.00
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        headers = {"Authorization": f"Bearer {token}"}
        data = (await client.get("/api/v1/maestro/budget/status", headers=headers)).json()
        assert data["state"] == "normal"

    @pytest.mark.anyio
    async def test_state_low(self, client, db_session, test_user):
        """0.25 <= remaining < 1.0 → low."""
        from app.auth.tokens import create_access_token

        test_user.budget_cents = 50  # $0.50
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        headers = {"Authorization": f"Bearer {token}"}
        data = (await client.get("/api/v1/maestro/budget/status", headers=headers)).json()
        assert data["state"] == "low"

    @pytest.mark.anyio
    async def test_state_critical(self, client, db_session, test_user):
        """0 < remaining < 0.25 → critical."""
        from app.auth.tokens import create_access_token

        test_user.budget_cents = 10  # $0.10
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        headers = {"Authorization": f"Bearer {token}"}
        data = (await client.get("/api/v1/maestro/budget/status", headers=headers)).json()
        assert data["state"] == "critical"

    @pytest.mark.anyio
    async def test_state_exhausted_zero(self, client, db_session, test_user):
        """remaining == 0 → exhausted."""
        from app.auth.tokens import create_access_token

        test_user.budget_cents = 0
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        headers = {"Authorization": f"Bearer {token}"}
        data = (await client.get("/api/v1/maestro/budget/status", headers=headers)).json()
        assert data["state"] == "exhausted"

    @pytest.mark.anyio
    async def test_state_exhausted_negative(self, client, db_session, test_user):
        """remaining < 0 → exhausted (possible if deduction races)."""
        from app.auth.tokens import create_access_token

        test_user.budget_cents = -10
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        headers = {"Authorization": f"Bearer {token}"}
        data = (await client.get("/api/v1/maestro/budget/status", headers=headers)).json()
        assert data["state"] == "exhausted"

    @pytest.mark.anyio
    async def test_state_boundary_exactly_025(self, client, db_session, test_user):
        """remaining == 0.25 → low (not critical, since < 0.25 is critical)."""
        from app.auth.tokens import create_access_token

        test_user.budget_cents = 25  # $0.25
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        headers = {"Authorization": f"Bearer {token}"}
        data = (await client.get("/api/v1/maestro/budget/status", headers=headers)).json()
        assert data["state"] == "low"

    @pytest.mark.anyio
    async def test_state_boundary_exactly_100(self, client, db_session, test_user):
        """remaining == 1.0 → normal (not low, since < 1.0 is low)."""
        from app.auth.tokens import create_access_token

        test_user.budget_cents = 100  # $1.00
        await db_session.commit()
        token = create_access_token(user_id=test_user.id, expires_hours=1)
        headers = {"Authorization": f"Bearer {token}"}
        data = (await client.get("/api/v1/maestro/budget/status", headers=headers)).json()
        assert data["state"] == "normal"


# ---------------------------------------------------------------------------
# camelCase serialization
# ---------------------------------------------------------------------------


class TestCamelCaseSerialization:
    """Verify that wire-format JSON uses camelCase keys as the frontend expects."""

    @pytest.mark.anyio
    async def test_chips_camel_case(self, client, db_session):
        """Chip objects use promptTemplateID and fullPrompt, not snake_case."""
        data = (await client.get("/api/v1/maestro/prompts/chips")).json()
        chip = data["chips"][0]
        assert "promptTemplateID" in chip, "Expected promptTemplateID (capital ID)"
        assert "fullPrompt" in chip
        assert "prompt_template_id" not in chip
        assert "full_prompt" not in chip

    @pytest.mark.anyio
    async def test_cards_camel_case(self, client, db_session):
        """Card objects use templateID, previewTags, not snake_case."""
        data = (await client.get("/api/v1/maestro/prompts/cards")).json()
        card = data["cards"][0]
        assert "templateID" in card, "Expected templateID (capital ID)"
        assert "previewTags" in card
        assert "template_id" not in card
        assert "preview_tags" not in card

    @pytest.mark.anyio
    async def test_template_camel_case(self, client, db_session):
        """Template response uses fullPrompt, not full_prompt."""
        data = (await client.get("/api/v1/maestro/prompts/lofi_chill")).json()
        assert "fullPrompt" in data
        assert "full_prompt" not in data

    @pytest.mark.anyio
    async def test_budget_camel_case(self, client, auth_headers, test_user):
        """Budget response uses sessionsUsed, not sessions_used."""
        data = (await client.get(
            "/api/v1/maestro/budget/status", headers=auth_headers
        )).json()
        assert "sessionsUsed" in data
        assert "sessions_used" not in data


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


class TestDataIntegrity:
    """Cross-cutting checks to ensure chips, cards, and templates are consistent."""

    def test_all_chip_templates_exist(self):
        """Every chip's prompt_template_id references an existing template."""
        for chip in CHIPS:
            assert chip.prompt_template_id in TEMPLATES, (
                f"Chip '{chip.id}' references missing template '{chip.prompt_template_id}'"
            )

    def test_all_card_templates_exist(self):
        """Every card's template_id references an existing template."""
        for card in CARDS:
            assert card.template_id in TEMPLATES, (
                f"Card '{card.id}' references missing template '{card.template_id}'"
            )

    def test_chip_ids_unique(self):
        """All chip IDs are unique."""
        ids = [c.id for c in CHIPS]
        assert len(ids) == len(set(ids))

    def test_card_ids_unique(self):
        """All card IDs are unique."""
        ids = [c.id for c in CARDS]
        assert len(ids) == len(set(ids))

    def test_template_ids_unique(self):
        """All template dict keys match the template's own id field."""
        for key, tmpl in TEMPLATES.items():
            assert key == tmpl.id, f"Key '{key}' != template.id '{tmpl.id}'"

    def test_exactly_eleven_templates(self):
        """We have exactly 11 templates (8 chip + 3 card)."""
        assert len(TEMPLATES) == 11

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
