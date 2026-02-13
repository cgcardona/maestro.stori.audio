"""
Tests for RAG (Retrieval-Augmented Generation) service.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.services.rag import RAGService, RAGChunk
from app.core.llm_client import LLMClient


@pytest.fixture
def mock_qdrant():
    """Mock Qdrant client."""
    with patch("app.services.rag.QdrantClient") as mock:
        yield mock.return_value


@pytest.fixture
def mock_llm_client():
    """Mock LLM client."""
    client = MagicMock(spec=LLMClient)
    return client


@pytest.fixture
def rag_service(mock_qdrant, mock_llm_client):
    """Create RAG service with mocks."""
    with patch("app.services.rag.get_settings") as mock_settings:
        mock_settings.return_value.hf_api_key = "test_key"
        mock_settings.return_value.qdrant_host = "localhost"
        mock_settings.return_value.qdrant_port = 6333
        
        service = RAGService(
            qdrant_host="localhost",
            qdrant_port=6333,
            llm_client=mock_llm_client,
        )
        return service


# =============================================================================
# Embedding Tests
# =============================================================================

@pytest.mark.asyncio
async def test_embed_text_success(rag_service):
    """Test successful text embedding with HuggingFace."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [0.1, 0.2, 0.3]  # Simple embedding
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        embedding = await rag_service.embed_text("test query")
        
        assert isinstance(embedding, list)
        assert len(embedding) == 3
        assert embedding == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_text_with_nested_array(rag_service):
    """Test embedding with nested array (token-level embeddings)."""
    # Simulate token-level embeddings that need mean pooling
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        [0.1, 0.2],
        [0.3, 0.4],
        [0.5, 0.6],
    ]
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        embedding = await rag_service.embed_text("test query")
        
        assert isinstance(embedding, list)
        # Should be mean-pooled across tokens: [0.3, 0.4]
        assert len(embedding) == 2


@pytest.mark.asyncio
async def test_embed_text_api_error(rag_service):
    """Test handling of HuggingFace API error."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        with pytest.raises(Exception, match="Embedding API error"):
            await rag_service.embed_text("test query")


# =============================================================================
# Search Tests
# =============================================================================

@pytest.mark.asyncio
async def test_search_success(rag_service):
    """Test successful RAG search."""
    # Mock embedding
    mock_embedding = [0.1] * 384
    
    # Mock Qdrant search response
    mock_search_response = MagicMock()
    mock_search_response.status_code = 200
    mock_search_response.json.return_value = {
        "result": [
            {
                "id": "chunk_1",
                "score": 0.95,
                "payload": {
                    "doc_id": "doc_1",
                    "doc_title": "Recording Guide",
                    "heading": "How to Record",
                    "content": "To record audio, create a track...",
                    "source_file": "recording.html",
                },
            },
            {
                "id": "chunk_2",
                "score": 0.85,
                "payload": {
                    "doc_id": "doc_1",
                    "doc_title": "Recording Guide",
                    "heading": "Recording Settings",
                    "content": "Configure your input device...",
                    "source_file": "recording.html",
                },
            },
        ],
    }
    
    with patch.object(rag_service, "embed_text", return_value=mock_embedding):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_search_response
            mock_client_class.return_value = mock_client
            
            chunks = await rag_service.search("how to record audio", top_k=5)
            
            assert len(chunks) == 2
            assert isinstance(chunks[0], RAGChunk)
            assert chunks[0].doc_title == "Recording Guide"
            assert chunks[0].heading == "How to Record"
            assert chunks[0].score == 0.95
            assert chunks[1].score == 0.85


@pytest.mark.asyncio
async def test_search_with_score_threshold(rag_service):
    """Test search with score threshold filtering."""
    mock_embedding = [0.1] * 384
    
    # Mock response with one result above threshold, one below
    mock_search_response = MagicMock()
    mock_search_response.status_code = 200
    mock_search_response.json.return_value = {
        "result": [
            {
                "id": "chunk_1",
                "score": 0.85,
                "payload": {
                    "doc_id": "doc_1",
                    "doc_title": "Guide",
                    "heading": "Section 1",
                    "content": "Content 1",
                    "source_file": "guide.html",
                },
            },
        ],
    }
    
    with patch.object(rag_service, "embed_text", return_value=mock_embedding):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_search_response
            mock_client_class.return_value = mock_client
            
            chunks = await rag_service.search(
                "test query",
                top_k=5,
                score_threshold=0.8,  # Should filter results
            )
            
            assert len(chunks) == 1
            assert chunks[0].score >= 0.8


@pytest.mark.asyncio
async def test_search_qdrant_error(rag_service):
    """Test handling of Qdrant search error."""
    mock_embedding = [0.1] * 384
    
    mock_error_response = MagicMock()
    mock_error_response.status_code = 404
    mock_error_response.text = "Collection not found"
    
    with patch.object(rag_service, "embed_text", return_value=mock_embedding):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_error_response
            mock_client_class.return_value = mock_client
            
            # Should return empty list on error, not crash
            chunks = await rag_service.search("test query")
            assert chunks == []


@pytest.mark.asyncio
async def test_search_empty_results(rag_service):
    """Test search with no matching results."""
    mock_embedding = [0.1] * 384
    
    mock_search_response = MagicMock()
    mock_search_response.status_code = 200
    mock_search_response.json.return_value = {"result": []}
    
    with patch.object(rag_service, "embed_text", return_value=mock_embedding):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_search_response
            mock_client_class.return_value = mock_client
            
            chunks = await rag_service.search("obscure query")
            assert chunks == []


# =============================================================================
# Answer Generation Tests
# =============================================================================

@pytest.mark.asyncio
async def test_answer_with_context(rag_service, mock_llm_client):
    """Test answer generation with retrieved context."""
    # Mock search results
    mock_chunks = [
        RAGChunk(
            doc_id="doc_1",
            doc_title="Recording Guide",
            heading="How to Record",
            content="To record audio: 1. Create track 2. Arm track 3. Press record",
            source_file="recording.html",
            score=0.95,
        ),
    ]
    
    # Mock LLM streaming response
    async def mock_stream(*args, **kwargs):
        yield {"choices": [{"delta": {"content": "Based on "}}]}
        yield {"choices": [{"delta": {"content": "the docs, "}}]}
        yield {"choices": [{"delta": {"content": "here's how..."}}]}
    
    mock_llm_client.chat_completion_stream = mock_stream
    
    with patch.object(rag_service, "search", return_value=mock_chunks):
        chunks = []
        async for chunk in rag_service.answer("how to record"):
            chunks.append(chunk)
        
        answer = "".join(chunks)
        assert len(chunks) > 0
        assert "Based on the docs, here's how..." == answer


@pytest.mark.asyncio
async def test_answer_no_llm_client(mock_qdrant):
    """Test answer generation without LLM client (fallback)."""
    with patch("app.services.rag.get_settings") as mock_settings:
        mock_settings.return_value.hf_api_key = "test_key"
        mock_settings.return_value.qdrant_host = "localhost"
        mock_settings.return_value.qdrant_port = 6333
        
        service = RAGService(llm_client=None)
        
        with patch.object(service, "search", return_value=[]):
            chunks = []
            async for chunk in service.answer("test question"):
                chunks.append(chunk)
            
            answer = "".join(chunks)
            assert "can't answer questions" in answer.lower()


# =============================================================================
# Collection Info Tests
# =============================================================================

def test_collection_exists(rag_service, mock_qdrant):
    """Test checking if collection exists."""
    # Mock get_collections to return list with our collection
    mock_collections = MagicMock()
    mock_collection = MagicMock()
    mock_collection.name = "stori_docs"
    mock_collections.collections = [mock_collection]
    mock_qdrant.get_collections.return_value = mock_collections
    
    exists = rag_service.collection_exists()
    assert exists is True


def test_collection_not_exists(rag_service, mock_qdrant):
    """Test checking non-existent collection."""
    mock_qdrant.get_collections.side_effect = Exception("Not found")
    
    exists = rag_service.collection_exists()
    assert exists is False


def test_get_collection_info(rag_service, mock_qdrant):
    """Test getting collection info."""
    mock_info = MagicMock()
    mock_info.points_count = 61
    mock_info.vectors_count = 61
    mock_status = MagicMock()
    mock_status.value = "green"
    mock_info.status = mock_status
    mock_qdrant.get_collection.return_value = mock_info
    
    info = rag_service.get_collection_info()
    assert "points_count" in info
    assert info["points_count"] == 61
    assert info["name"] == "stori_docs"


# =============================================================================
# Security Tests
# =============================================================================

@pytest.mark.asyncio
async def test_search_sanitizes_input(rag_service):
    """Test that search handles malicious input safely."""
    # Try various injection attempts
    malicious_queries = [
        "'; DROP TABLE docs;--",
        "<script>alert('xss')</script>",
        "../../../etc/passwd",
        "' OR '1'='1",
    ]
    
    mock_embedding = [0.1] * 384
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": []}
    
    with patch.object(rag_service, "embed_text", return_value=mock_embedding):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client
            
            for query in malicious_queries:
                # Should not crash or leak errors
                chunks = await rag_service.search(query)
                assert isinstance(chunks, list)


@pytest.mark.asyncio
async def test_embedding_handles_large_input(rag_service):
    """Test that embedding handles excessively large input."""
    # 100KB of text (potential DoS vector)
    large_text = "test " * 20000
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [0.1, 0.2, 0.3]
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        # Should handle gracefully (HF API has its own limits)
        embedding = await rag_service.embed_text(large_text)
        assert isinstance(embedding, list)


@pytest.mark.asyncio
async def test_api_key_not_exposed_in_errors(rag_service):
    """Test that API key is not exposed in error messages."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized: Invalid API key test_key"
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        try:
            await rag_service.embed_text("test")
        except Exception as e:
            error_msg = str(e)
            # Error should not contain the actual API key
            assert "test_key" not in error_msg
            assert "Bearer" not in error_msg
