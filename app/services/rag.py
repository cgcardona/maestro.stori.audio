"""
RAG (Retrieval-Augmented Generation) service for documentation search.

This service powers the "thinking" state by:
1. Searching the vector database for relevant documentation chunks
2. Using retrieved context to generate helpful answers
3. Streaming responses via SSE
"""
from __future__ import annotations

import logging
import httpx
from dataclasses import dataclass
from typing import AsyncGenerator, Required, TypeGuard, TypedDict

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter

from app.config import get_settings
from app.contracts.llm_types import ChatMessage, SystemMessage, UserMessage
from app.core.llm_client import LLMClient

logger = logging.getLogger(__name__)

# HuggingFace embedding model (matches ingestion script)
HF_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class QdrantCollectionStats(TypedDict):
    """Live statistics for the Qdrant docs collection (success path)."""

    name: str
    vectors_count: int
    points_count: int
    status: str


class QdrantCollectionError(TypedDict):
    """Error payload returned when Qdrant collection info cannot be fetched."""

    error: str


def is_collection_stats(
    info: QdrantCollectionStats | QdrantCollectionError,
) -> TypeGuard[QdrantCollectionStats]:
    """Narrow a collection-info result to the success path."""
    return "error" not in info


@dataclass
class RAGChunk:
    """A chunk of documentation retrieved from vector search."""
    doc_id: str
    doc_title: str
    heading: str
    content: str
    source_file: str
    score: float


class RAGService:
    """
    RAG service for documentation-powered Q&A.
    
    Uses Qdrant for vector search and HuggingFace for embeddings.
    """
    
    COLLECTION_NAME = "stori_docs"
    
    def __init__(
        self,
        qdrant_host: str = "qdrant",
        qdrant_port: int = 6333,
        llm_client: LLMClient | None = None,
    ):
        """
        Initialize RAG service.
        
        Args:
            qdrant_host: Qdrant server hostname
            qdrant_port: Qdrant server port
            llm_client: LLM client for answer generation
        """
        self.qdrant = QdrantClient(
            host=qdrant_host, 
            port=qdrant_port,
            check_compatibility=False,  # Skip version check
        )
        self.llm_client = llm_client
        self._hf_api_key: str | None = None
        
    @property
    def hf_api_key(self) -> str | None:
        """Get HuggingFace API key."""
        if self._hf_api_key is None:
            settings = get_settings()
            self._hf_api_key = settings.hf_api_key
        return self._hf_api_key
    
    async def embed_text(self, text: str) -> list[float]:
        """
        Generate embedding for text using HuggingFace.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        api_url = f"https://router.huggingface.co/hf-inference/models/{HF_EMBEDDING_MODEL}/pipeline/feature-extraction"
        headers = {"Authorization": f"Bearer {self.hf_api_key}"}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                api_url,
                headers=headers,
                json={"inputs": text, "options": {"wait_for_model": True}},
            )
            
            if response.status_code != 200:
                logger.error(f"HuggingFace API error: {response.status_code}")
                raise Exception(f"Embedding API error: {response.status_code}")
            
            raw = response.json()
            # HF returns nested array for sentence-transformers, take mean pooling
            if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], list):
                import numpy as np
                embedding: list[float] = np.mean(raw, axis=0).tolist()
            else:
                embedding = raw if isinstance(raw, list) else []
            return embedding
    
    async def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.3,
    ) -> list[RAGChunk]:
        """
        Search for relevant documentation chunks.
        
        Args:
            query: User's question
            top_k: Number of results to return
            score_threshold: Minimum similarity score
            
        Returns:
            list of relevant documentation chunks
        """
        try:
            # Generate query embedding
            query_embedding = await self.embed_text(query)
            
            # Search Qdrant via REST API (avoids client version mismatch)
            settings = get_settings()
            search_url = f"http://{settings.qdrant_host}:{settings.qdrant_port}/collections/{self.COLLECTION_NAME}/points/search"
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    search_url,
                    json={
                        "vector": query_embedding,
                        "limit": top_k,
                        "score_threshold": score_threshold,
                        "with_payload": True,
                    },
                )
                
                if response.status_code != 200:
                    logger.error(f"Qdrant search failed: {response.status_code} - {response.text}")
                    return []
                
                data = response.json()
                results = data.get("result", [])
            
            # Convert to RAGChunk objects
            chunks = []
            for point in results:
                payload = point.get("payload", {})
                chunks.append(RAGChunk(
                    doc_id=payload.get("doc_id", ""),
                    doc_title=payload.get("doc_title", ""),
                    heading=payload.get("heading", ""),
                    content=payload.get("content", ""),
                    source_file=payload.get("source_file", ""),
                    score=point.get("score", 0.0),
                ))
            
            logger.info(f"RAG search returned {len(chunks)} chunks for: {query[:50]}...")
            return chunks
            
        except Exception as e:
            logger.error(f"RAG search failed: {e}")
            return []
    
    def _build_context(self, chunks: list[RAGChunk]) -> str:
        """Build context string from retrieved chunks."""
        if not chunks:
            return "No relevant documentation found."
        
        context_parts = []
        for chunk in chunks:
            context_parts.append(f"""
## {chunk.doc_title} - {chunk.heading}
(Source: {chunk.source_file})

{chunk.content}
""")
        
        return "\n---\n".join(context_parts)
    
    async def answer(
        self,
        question: str,
        model: str = "anthropic/claude-3.5-sonnet",
    ) -> AsyncGenerator[str, None]:
        """
        Generate an answer using RAG.
        
        Args:
            question: User's question
            model: LLM model to use for answer generation
            
        Yields:
            Answer text chunks (for streaming)
        """
        # 1. Search for relevant documentation
        chunks = await self.search(question, top_k=5)
        context = self._build_context(chunks)
        
        # 2. Build prompt with context
        system_prompt = """You are Stori - infinite music machine — a DAW (Digital Audio Workstation) that helps users create and produce music.

Your role:
- Answer questions helpfully and naturally
- Use the documentation context below when relevant
- Provide general guidance when the docs don't cover the topic
- Be concise and practical
- Suggest keyboard shortcuts when relevant
- Keep responses focused and actionable

Documentation Context:
""" + context

        user_prompt = f"""User question: {question}

Please provide a clear, helpful answer. Use the documentation context when it's relevant, and supplement with general knowledge when needed."""

        # 3. Generate answer via LLM (streaming)
        if self.llm_client:
            messages: list[ChatMessage] = [
                SystemMessage(role="system", content=system_prompt),
                UserMessage(role="user", content=user_prompt),
            ]
            
            async for chunk in self.llm_client.chat_completion_stream(
                messages=messages,
                tools=None,  # No tools for Q&A
            ):
                if chunk["type"] == "content_delta":
                    yield chunk["text"]
        else:
            # Fallback: non-streaming response
            yield "I'm sorry, I can't answer questions right now. Please try again later."
    
    async def answer_simple(
        self,
        question: str,
        model: str = "anthropic/claude-3.5-sonnet",
    ) -> str:
        """
        Generate a complete answer (non-streaming).
        
        Args:
            question: User's question
            model: LLM model to use
            
        Returns:
            Complete answer text
        """
        chunks = []
        async for chunk in self.answer(question, model):
            chunks.append(chunk)
        return "".join(chunks)
    
    def collection_exists(self) -> bool:
        """Check if the docs collection exists."""
        try:
            collections = self.qdrant.get_collections().collections
            return any(c.name == self.COLLECTION_NAME for c in collections)
        except Exception:
            return False
    
    def get_collection_info(self) -> QdrantCollectionStats | QdrantCollectionError:
        """Get information about the docs collection."""
        try:
            info = self.qdrant.get_collection(self.COLLECTION_NAME)
            return {
                "name": self.COLLECTION_NAME,
                "vectors_count": getattr(info, "vectors_count", getattr(info, "indexed_vectors_count", 0)),
                "points_count": info.points_count if info.points_count is not None else 0,
                "status": getattr(info.status, "value", str(info.status)),
            }
        except Exception as e:
            return {"error": str(e)}


# Singleton instance
_rag_service: RAGService | None = None


def get_rag_service(llm_client: LLMClient | None = None) -> RAGService:
    """Get or create the RAG service singleton."""
    global _rag_service
    
    if _rag_service is None:
        settings = get_settings()
        _rag_service = RAGService(
            qdrant_host=settings.qdrant_host,
            qdrant_port=settings.qdrant_port,
            llm_client=llm_client,
        )
    elif llm_client and _rag_service.llm_client is None:
        _rag_service.llm_client = llm_client
    
    return _rag_service
