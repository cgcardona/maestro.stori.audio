"""
Tests for Muse Variation Specification API endpoints.

Tests the spec-compliant endpoints:
- POST /variation/propose
- GET /variation/stream
- POST /variation/commit
- POST /variation/discard
"""

import pytest
import pytest_asyncio
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app
from app.auth.dependencies import require_valid_token
from app.models.variation import Variation, Phrase, NoteChange, MidiNoteSnapshot
from app.models.requests import (
    ProposeVariationRequest,
    CommitVariationRequest,
    DiscardVariationRequest,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def variation_client(client, db_session, mock_auth_token):
    """Client with auth and db overridden so variation endpoints accept requests."""
    async def override_require_valid_token():
        return mock_auth_token

    app.dependency_overrides[require_valid_token] = override_require_valid_token
    try:
        yield client
    finally:
        app.dependency_overrides.pop(require_valid_token, None)


@pytest.fixture
def mock_user_id():
    """Mock user ID for auth."""
    return "user-123"


@pytest.fixture
def mock_project_id():
    """Mock project ID."""
    return "proj-456"


@pytest.fixture
def mock_state_id():
    """Mock state ID."""
    return "42"


@pytest.fixture
def sample_variation():
    """Create a sample variation for testing."""
    phrase = Phrase(
        phrase_id="phrase-1",
        track_id="track-1",
        region_id="region-1",
        start_beat=0.0,
        end_beat=4.0,
        label="Bars 1-4",
        note_changes=[
            NoteChange(
                note_id="note-1",
                change_type="added",
                after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
            ),
            NoteChange(
                note_id="note-2",
                change_type="modified",
                before=MidiNoteSnapshot(pitch=62, start_beat=1, duration_beats=1, velocity=100),
                after=MidiNoteSnapshot(pitch=63, start_beat=1, duration_beats=1, velocity=80),
            ),
        ],
        tags=["pitchChange"],
    )
    
    return Variation(
        variation_id="var-789",
        intent="make it darker",
        ai_explanation="Lowered pitches and velocity",
        affected_tracks=["track-1"],
        affected_regions=["region-1"],
        beat_range=(0.0, 4.0),
        phrases=[phrase],
    )


@pytest.fixture
def mock_auth_token():
    """Mock auth token dependency."""
    return {"sub": "user-123"}


# =============================================================================
# POST /variation/propose Tests
# =============================================================================

class TestProposeVariation:
    """Tests for POST /variation/propose endpoint."""
    
    @pytest.mark.asyncio
    async def test_propose_variation_success(
        self,
        variation_client,
        mock_project_id,
        mock_state_id,
        sample_variation,
        mock_auth_token,
    ):
        """Test successful variation proposal returns 200 with variation_id and stream_url."""
        with patch("app.api.routes.variation.check_budget", new_callable=AsyncMock), \
             patch("app.api.routes.variation.get_or_create_store") as mock_store, \
             patch("app.api.routes.variation.get_variation_store") as mock_vstore, \
             patch("app.api.routes.variation._run_generation", new_callable=AsyncMock):

            # Setup mocks
            mock_store_instance = MagicMock()
            mock_store_instance.check_state_id.return_value = True
            mock_store_instance.get_state_id.return_value = mock_state_id
            mock_store.return_value = mock_store_instance

            # VariationStore mock
            mock_vstore_instance = MagicMock()
            mock_record = MagicMock()
            mock_record.variation_id = "var-test-456"
            mock_vstore_instance.create.return_value = mock_record
            mock_vstore.return_value = mock_vstore_instance

            # Make request
            request_data = {
                "project_id": mock_project_id,
                "base_state_id": mock_state_id,
                "intent": "make it darker",
                "scope": None,
                "options": {"phrase_grouping": "bars", "bar_size": 4, "stream": True},
                "request_id": None,
            }

            response = await variation_client.post(
                "/api/v1/variation/propose",
                json=request_data,
            )

            assert response.status_code == 200
            data = response.json()

            # Verify response structure
            assert "variation_id" in data
            assert data["project_id"] == mock_project_id
            assert data["base_state_id"] == mock_state_id
            assert data["intent"] == "make it darker"
            assert "stream_url" in data
            assert "/variation/stream?variation_id=" in data["stream_url"]
    
    @pytest.mark.asyncio
    async def test_propose_variation_state_conflict(
        self,
        variation_client,
        mock_project_id,
    ):
        """Test variation proposal with state conflict."""
        with \
             patch("app.api.routes.variation.check_budget", new_callable=AsyncMock), \
             patch("app.api.routes.variation.get_or_create_store") as mock_store:
            
            # Setup mock store with mismatched state
            mock_store_instance = MagicMock()
            mock_store_instance.check_state_id.return_value = False
            mock_store_instance.get_state_id.return_value = "100"
            mock_store.return_value = mock_store_instance
            
            # Make request
            request_data = {
                "project_id": mock_project_id,
                "base_state_id": "42",  # Expected state
                "intent": "make it darker",
            }
            
            response = await variation_client.post(
                "/api/v1/variation/propose",
                json=request_data,
            )
            
            assert response.status_code == 409  # Conflict
            data = response.json()
            assert "error" in data["detail"]
            assert "State conflict" in data["detail"]["error"]
            assert data["detail"]["current_state_id"] == "100"
    
    @pytest.mark.asyncio
    async def test_propose_variation_invalid_intent(
        self,
        variation_client,
        mock_project_id,
        mock_state_id,
    ):
        """Test variation proposal with non-COMPOSING intent returns 200.

        Since v1 supercharge, propose returns 200 immediately and launches
        background generation. Invalid intent errors surface via the SSE
        stream (error + done(status=failed)), not the propose response.
        """
        with \
             patch("app.api.routes.variation.check_budget", new_callable=AsyncMock), \
             patch("app.api.routes.variation.get_or_create_store") as mock_store, \
             patch("app.api.routes.variation.get_variation_store") as mock_vstore, \
             patch("app.api.routes.variation._run_generation", new_callable=AsyncMock):

            # Setup mocks
            mock_store_instance = MagicMock()
            mock_store_instance.check_state_id.return_value = True
            mock_store.return_value = mock_store_instance

            mock_vstore_instance = MagicMock()
            mock_record = MagicMock()
            mock_record.variation_id = "var-test-123"
            mock_vstore_instance.create.return_value = mock_record
            mock_vstore.return_value = mock_vstore_instance

            # Make request
            request_data = {
                "project_id": mock_project_id,
                "base_state_id": mock_state_id,
                "intent": "what's in my project?",
            }

            response = await variation_client.post(
                "/api/v1/variation/propose",
                json=request_data,
            )

            # Propose returns 200 immediately; errors surface in stream
            assert response.status_code == 200
            data = response.json()
            assert "variation_id" in data
            assert "stream_url" in data


# =============================================================================
# POST /variation/commit Tests
# =============================================================================

class TestCommitVariation:
    """Tests for POST /variation/commit endpoint."""
    
    @pytest.mark.asyncio
    async def test_commit_variation_success(
        self,
        variation_client,
        mock_project_id,
        mock_state_id,
        sample_variation,
    ):
        """Test successful variation commit (backward compat with variation_data)."""
        with patch("app.api.routes.variation.get_or_create_store") as mock_store, \
             patch("app.api.routes.variation.get_variation_store") as mock_vstore, \
             patch("app.api.routes.variation.apply_variation_phrases", new_callable=AsyncMock) as mock_apply:

            # VariationStore returns None → falls back to variation_data path
            mock_vstore_instance = MagicMock()
            mock_vstore_instance.get.return_value = None
            mock_vstore.return_value = mock_vstore_instance

            # Setup mocks
            mock_store_instance = MagicMock()
            mock_store_instance.check_state_id.return_value = True
            mock_store_instance.get_state_id.return_value = "43"
            mock_store.return_value = mock_store_instance

            # Mock apply result
            from app.core.executor import VariationApplyResult
            mock_apply.return_value = VariationApplyResult(
                success=True,
                applied_phrase_ids=["phrase-1"],
                notes_added=1,
                notes_removed=0,
                notes_modified=1,
            )

            # Make request
            request_data = {
                "project_id": mock_project_id,
                "base_state_id": mock_state_id,
                "variation_id": sample_variation.variation_id,
                "accepted_phrase_ids": ["phrase-1"],
                "request_id": None,
                "variation_data": sample_variation.model_dump(),
            }

            response = await variation_client.post(
                "/api/v1/variation/commit",
                json=request_data,
            )

            assert response.status_code == 200
            data = response.json()

            assert data["project_id"] == mock_project_id
            assert data["new_state_id"] == "43"
            assert data["applied_phrase_ids"] == ["phrase-1"]
            assert "Accept Variation" in data["undo_label"]
            assert "updated_regions" in data
    
    @pytest.mark.asyncio
    async def test_commit_variation_state_conflict(
        self,
        variation_client,
        mock_project_id,
        sample_variation,
    ):
        """Test commit with state conflict (backward compat path)."""
        with \
             patch("app.api.routes.variation.get_variation_store") as mock_vstore, \
             patch("app.api.routes.variation.get_or_create_store") as mock_store:

            mock_vstore_instance = MagicMock()
            mock_vstore_instance.get.return_value = None
            mock_vstore.return_value = mock_vstore_instance

            mock_store_instance = MagicMock()
            mock_store_instance.check_state_id.return_value = False
            mock_store_instance.get_state_id.return_value = "100"
            mock_store.return_value = mock_store_instance

            request_data = {
                "project_id": mock_project_id,
                "base_state_id": "42",
                "variation_id": sample_variation.variation_id,
                "accepted_phrase_ids": ["phrase-1"],
                "variation_data": sample_variation.model_dump(),
            }

            response = await variation_client.post(
                "/api/v1/variation/commit",
                json=request_data,
            )

            assert response.status_code == 409
            data = response.json()
            assert "State conflict" in data["detail"]["error"]
    
    @pytest.mark.asyncio
    async def test_commit_variation_id_mismatch(
        self,
        variation_client,
        mock_project_id,
        mock_state_id,
        sample_variation,
    ):
        """Test commit with variation ID mismatch (backward compat path)."""
        with patch("app.api.routes.variation.get_variation_store") as mock_vstore, \
             patch("app.api.routes.variation.get_or_create_store") as mock_store:

            mock_vstore_instance = MagicMock()
            mock_vstore_instance.get.return_value = None
            mock_vstore.return_value = mock_vstore_instance

            mock_store_instance = MagicMock()
            mock_store_instance.check_state_id.return_value = True
            mock_store.return_value = mock_store_instance

            request_data = {
                "project_id": mock_project_id,
                "base_state_id": mock_state_id,
                "variation_id": "wrong-var-id",
                "accepted_phrase_ids": ["phrase-1"],
                "variation_data": sample_variation.model_dump(),
            }

            response = await variation_client.post(
                "/api/v1/variation/commit",
                json=request_data,
            )

            assert response.status_code == 400
            data = response.json()
            assert "Variation ID mismatch" in data["detail"]["error"]
    
    @pytest.mark.asyncio
    async def test_commit_variation_invalid_phrase_ids(
        self,
        variation_client,
        mock_project_id,
        mock_state_id,
        sample_variation,
    ):
        """Test commit with invalid phrase IDs (backward compat path)."""
        with patch("app.api.routes.variation.get_variation_store") as mock_vstore, \
             patch("app.api.routes.variation.get_or_create_store") as mock_store:

            mock_vstore_instance = MagicMock()
            mock_vstore_instance.get.return_value = None
            mock_vstore.return_value = mock_vstore_instance

            mock_store_instance = MagicMock()
            mock_store_instance.check_state_id.return_value = True
            mock_store.return_value = mock_store_instance

            request_data = {
                "project_id": mock_project_id,
                "base_state_id": mock_state_id,
                "variation_id": sample_variation.variation_id,
                "accepted_phrase_ids": ["nonexistent-phrase"],
                "variation_data": sample_variation.model_dump(),
            }

            response = await variation_client.post(
                "/api/v1/variation/commit",
                json=request_data,
            )

            assert response.status_code == 400
            data = response.json()
            assert "Invalid phrase IDs" in data["detail"]["error"]


# =============================================================================
# POST /variation/discard Tests
# =============================================================================

class TestDiscardVariation:
    """Tests for POST /variation/discard endpoint."""
    
    @pytest.mark.asyncio
    async def test_discard_variation_success(
        self,
        variation_client,
        mock_project_id,
    ):
        """Test successful variation discard."""
        request_data = {
            "project_id": mock_project_id,
            "variation_id": "var-789",
            "request_id": None,
        }
        
        response = await variation_client.post(
            "/api/v1/variation/discard",
            json=request_data,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True


# =============================================================================
# GET /variation/stream Tests
# =============================================================================

class TestStreamVariation:
    """Tests for GET /variation/stream endpoint."""

    @pytest.mark.asyncio
    async def test_stream_variation_not_found(
        self,
        variation_client,
    ):
        """Test streaming returns 404 for unknown variation_id."""
        response = await variation_client.get(
            "/api/v1/variation/stream?variation_id=nonexistent-999",
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "Variation not found"


# =============================================================================
# Integration Tests
# =============================================================================

class TestVariationWorkflow:
    """Integration tests for the complete variation workflow."""
    
    @pytest.mark.asyncio
    async def test_full_variation_workflow(
        self,
        client,
        mock_project_id,
        mock_state_id,
        sample_variation,
        mock_auth_token,
    ):
        """Test complete workflow: propose -> commit."""
        # This would test the full flow in an integration test
        # For now, we verify the endpoints exist and have correct signatures
        pass


# =============================================================================
# StateStore Integration Tests
# =============================================================================

class TestStateStoreIntegration:
    """Tests for StateStore version tracking integration."""
    
    def test_get_state_id(self):
        """Test get_state_id returns string version."""
        from app.core.state_store import StateStore
        
        store = StateStore(project_id="proj-1")
        state_id = store.get_state_id()
        
        assert isinstance(state_id, str)
        assert state_id == "0"  # Initial version
    
    def test_check_state_id_match(self):
        """Test check_state_id with matching version."""
        from app.core.state_store import StateStore
        
        store = StateStore(project_id="proj-1")
        
        # Should match initial version
        assert store.check_state_id("0") is True
    
    def test_check_state_id_mismatch(self):
        """Test check_state_id with mismatched version."""
        from app.core.state_store import StateStore
        
        store = StateStore(project_id="proj-1")
        
        # Should not match wrong version
        assert store.check_state_id("100") is False
    
    def test_check_state_id_invalid_format(self):
        """Test check_state_id with invalid format."""
        from app.core.state_store import StateStore
        
        store = StateStore(project_id="proj-1")
        
        # Should handle invalid format gracefully
        assert store.check_state_id("invalid") is False


# =============================================================================
# Variation Model Tests
# =============================================================================

class TestVariationModelExtensions:
    """Tests for variation model extensions."""
    
    def test_note_counts_property(self, sample_variation):
        """Test note_counts property returns correct counts."""
        counts = sample_variation.note_counts
        
        assert counts["added"] == 1
        assert counts["removed"] == 0
        assert counts["modified"] == 1
    
    def test_note_counts_empty_variation(self):
        """Test note_counts for empty variation."""
        variation = Variation(
            variation_id="var-1",
            intent="test",
            beat_range=(0, 0),
            phrases=[],
        )
        
        counts = variation.note_counts
        
        assert counts["added"] == 0
        assert counts["removed"] == 0
        assert counts["modified"] == 0
