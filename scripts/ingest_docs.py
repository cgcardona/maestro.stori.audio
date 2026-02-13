#!/usr/bin/env python3
"""
Document ingestion script for RAG.

Parses HTML documentation files, chunks them, generates embeddings,
and stores them in Qdrant vector database.

Usage:
    python scripts/ingest_docs.py --docs-dir /path/to/docs
    
    # Or with custom Qdrant host:
    python scripts/ingest_docs.py --docs-dir ./docs --qdrant-host localhost
    
    # Use HuggingFace embeddings (requires STORI_HF_API_KEY or --hf-key):
    python scripts/ingest_docs.py --docs-dir ./docs --hf-key your_key
"""

import argparse
import asyncio
import os
import sys
import httpx
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

# HuggingFace embedding model (384 dimensions)
HF_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HF_EMBEDDING_DIM = 384


@dataclass
class Document:
    """A parsed documentation file."""
    id: str
    title: str
    source_file: str
    sections: list[dict]  # {"heading": str, "content": str}
    full_text: str


@dataclass
class Chunk:
    """A chunk of documentation for embedding."""
    doc_id: str
    doc_title: str
    heading: str
    content: str
    source_file: str
    chunk_index: int


def parse_html_doc(html_file: Path) -> Optional[Document]:
    """
    Parse an HTML documentation file into a Document.
    
    Args:
        html_file: Path to HTML file
        
    Returns:
        Document object or None if parsing fails
    """
    try:
        with open(html_file, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "lxml")
        
        # Extract title
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else html_file.stem.replace("-", " ").title()
        
        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        
        # Extract sections
        sections = []
        
        # Try to find semantic sections first
        section_elements = soup.find_all(["section", "article"])
        
        if section_elements:
            for section in section_elements:
                heading = section.find(["h2", "h3", "h4"])
                heading_text = heading.get_text(strip=True) if heading else "General"
                content = section.get_text(separator="\n", strip=True)
                
                if content and len(content) > 20:  # Skip empty/tiny sections
                    sections.append({
                        "heading": heading_text,
                        "content": content,
                    })
        else:
            # Fallback: split by headings
            current_heading = "Introduction"
            current_content = []
            
            body = soup.find("body") or soup
            
            for element in body.find_all(["h2", "h3", "p", "ul", "ol", "pre", "code", "div"]):
                if element.name in ["h2", "h3"]:
                    # Save previous section
                    if current_content:
                        content = "\n".join(current_content)
                        if len(content) > 20:
                            sections.append({
                                "heading": current_heading,
                                "content": content,
                            })
                    # Start new section
                    current_heading = element.get_text(strip=True)
                    current_content = []
                else:
                    text = element.get_text(separator="\n", strip=True)
                    if text:
                        current_content.append(text)
            
            # Don't forget last section
            if current_content:
                content = "\n".join(current_content)
                if len(content) > 20:
                    sections.append({
                        "heading": current_heading,
                        "content": content,
                    })
        
        # Get full text for fallback
        full_text = soup.get_text(separator="\n", strip=True)
        
        # If no sections found, use full text as one section
        if not sections and full_text:
            sections.append({
                "heading": title,
                "content": full_text,
            })
        
        if not sections:
            print(f"  ⚠️  No content found in {html_file.name}")
            return None
        
        return Document(
            id=html_file.stem,
            title=title,
            source_file=html_file.name,
            sections=sections,
            full_text=full_text,
        )
        
    except Exception as e:
        print(f"  ❌ Error parsing {html_file.name}: {e}")
        return None


def chunk_document(doc: Document, max_chunk_chars: int = 2000, overlap_chars: int = 200) -> list[Chunk]:
    """
    Split document into overlapping chunks for embedding.
    
    Args:
        doc: Document to chunk
        max_chunk_chars: Maximum characters per chunk (~500 tokens)
        overlap_chars: Overlap between chunks for context continuity
        
    Returns:
        List of Chunk objects
    """
    chunks = []
    chunk_index = 0
    
    for section in doc.sections:
        heading = section["heading"]
        content = section["content"]
        
        # If section fits in one chunk, keep it intact
        if len(content) <= max_chunk_chars:
            chunks.append(Chunk(
                doc_id=doc.id,
                doc_title=doc.title,
                heading=heading,
                content=content,
                source_file=doc.source_file,
                chunk_index=chunk_index,
            ))
            chunk_index += 1
        else:
            # Split large sections with overlap
            start = 0
            while start < len(content):
                end = start + max_chunk_chars
                
                # Try to break at sentence boundary
                if end < len(content):
                    # Look for sentence end (.!?) followed by space
                    break_point = content.rfind(". ", start + max_chunk_chars // 2, end)
                    if break_point == -1:
                        break_point = content.rfind("! ", start + max_chunk_chars // 2, end)
                    if break_point == -1:
                        break_point = content.rfind("? ", start + max_chunk_chars // 2, end)
                    if break_point != -1:
                        end = break_point + 1
                
                chunk_text = content[start:end].strip()
                
                if chunk_text:
                    chunks.append(Chunk(
                        doc_id=doc.id,
                        doc_title=doc.title,
                        heading=heading,
                        content=chunk_text,
                        source_file=doc.source_file,
                        chunk_index=chunk_index,
                    ))
                    chunk_index += 1
                
                # Move start with overlap
                start = end - overlap_chars
    
    return chunks


async def embed_chunks_hf(
    chunks: list[Chunk],
    hf_api_key: str,
    batch_size: int = 50,
) -> list[tuple[Chunk, list[float]]]:
    """
    Generate embeddings for chunks using HuggingFace Inference API.
    
    Args:
        chunks: Chunks to embed
        hf_api_key: HuggingFace API key
        batch_size: Batch size for API calls
        
    Returns:
        List of (chunk, embedding) tuples
    """
    embeddings = []
    api_url = f"https://router.huggingface.co/hf-inference/models/{HF_EMBEDDING_MODEL}/pipeline/feature-extraction"
    headers = {"Authorization": f"Bearer {hf_api_key}"}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            
            # Create embedding text with heading for context
            texts = [f"{chunk.heading}\n\n{chunk.content}" for chunk in batch]
            
            print(f"  Embedding batch {i // batch_size + 1}/{(len(chunks) - 1) // batch_size + 1}...")
            
            response = await client.post(
                api_url,
                headers=headers,
                json={"inputs": texts, "options": {"wait_for_model": True}},
            )
            
            if response.status_code != 200:
                print(f"  ❌ HuggingFace API error: {response.status_code} - {response.text}")
                continue
            
            batch_embeddings = response.json()
            
            for chunk, emb in zip(batch, batch_embeddings):
                # HF returns nested array, take mean pooling
                if isinstance(emb[0], list):
                    # Token-level embeddings, mean pool
                    import numpy as np
                    emb = np.mean(emb, axis=0).tolist()
                embeddings.append((chunk, emb))
    
    return embeddings


def store_in_qdrant(
    embeddings: list[tuple[Chunk, list[float]]],
    qdrant: QdrantClient,
    collection_name: str = "stori_docs",
    embedding_dim: int = HF_EMBEDDING_DIM,
):
    """
    Store embeddings in Qdrant vector database.
    
    Args:
        embeddings: List of (chunk, embedding) tuples
        qdrant: Qdrant client
        collection_name: Name of collection
        embedding_dim: Dimension of embedding vectors
    """
    # Recreate collection
    print(f"  Creating collection '{collection_name}' (dim={embedding_dim})...")
    
    try:
        qdrant.delete_collection(collection_name)
    except Exception:
        pass  # Collection doesn't exist
    
    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=embedding_dim,
            distance=Distance.COSINE,
        ),
    )
    
    # Insert points
    points = []
    for i, (chunk, embedding) in enumerate(embeddings):
        points.append(PointStruct(
            id=i,
            vector=embedding,
            payload={
                "doc_id": chunk.doc_id,
                "doc_title": chunk.doc_title,
                "heading": chunk.heading,
                "content": chunk.content,
                "source_file": chunk.source_file,
                "chunk_index": chunk.chunk_index,
            },
        ))
    
    # Batch insert
    batch_size = 100
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        qdrant.upsert(collection_name=collection_name, points=batch)
    
    print(f"  ✅ Stored {len(points)} chunks in Qdrant")


async def main():
    parser = argparse.ArgumentParser(description="Ingest HTML docs into Qdrant for RAG")
    parser.add_argument("--docs-dir", required=True, help="Path to docs directory")
    parser.add_argument("--qdrant-host", default="localhost", help="Qdrant host")
    parser.add_argument("--qdrant-port", type=int, default=6333, help="Qdrant port")
    parser.add_argument("--hf-key", default=None, help="HuggingFace API key for embeddings (or STORI_HF_API_KEY)")
    args = parser.parse_args()
    
    docs_dir = Path(args.docs_dir)
    if not docs_dir.exists():
        print(f"❌ Docs directory not found: {docs_dir}")
        sys.exit(1)
    
    hf_key = args.hf_key or os.environ.get("HF_API_KEY") or os.environ.get("STORI_HF_API_KEY")
    if not hf_key:
        print("❌ HuggingFace API key required. Set STORI_HF_API_KEY or use --hf-key")
        sys.exit(1)
    embedding_dim = HF_EMBEDDING_DIM
    print("Using HuggingFace embeddings (all-MiniLM-L6-v2)")
    
    print(f"\n📚 Stori Docs Ingestion")
    print(f"=" * 50)
    print(f"Docs directory: {docs_dir}")
    print(f"Qdrant: {args.qdrant_host}:{args.qdrant_port}")
    print(f"Embedding dim: {embedding_dim}")
    print()
    
    # Find HTML files
    html_files = list(docs_dir.glob("*.html"))
    
    # Filter out index pages
    html_files = [f for f in html_files if f.name not in ["index.html", "docs.html"]]
    
    if not html_files:
        print(f"❌ No HTML files found in {docs_dir}")
        sys.exit(1)
    
    print(f"📄 Found {len(html_files)} HTML files")
    
    # Parse documents
    print("\n1️⃣ Parsing HTML documents...")
    documents = []
    for html_file in html_files:
        print(f"  Parsing {html_file.name}...")
        doc = parse_html_doc(html_file)
        if doc:
            documents.append(doc)
            print(f"    → {len(doc.sections)} sections")
    
    print(f"  ✅ Parsed {len(documents)} documents")
    
    # Chunk documents
    print("\n2️⃣ Chunking documents...")
    all_chunks = []
    for doc in documents:
        chunks = chunk_document(doc)
        all_chunks.extend(chunks)
        print(f"  {doc.source_file}: {len(chunks)} chunks")
    
    print(f"  ✅ Created {len(all_chunks)} chunks")
    
    # Generate embeddings
    print("\n3️⃣ Generating embeddings...")
    embeddings = await embed_chunks_hf(all_chunks, hf_key)
    print(f"  ✅ Generated {len(embeddings)} embeddings")
    
    # Store in Qdrant
    print("\n4️⃣ Storing in Qdrant...")
    qdrant = QdrantClient(host=args.qdrant_host, port=args.qdrant_port, check_compatibility=False)
    store_in_qdrant(embeddings, qdrant, embedding_dim=embedding_dim)
    
    # Verify
    print("\n5️⃣ Verification...")
    try:
        info = qdrant.get_collection("stori_docs")
        print(f"  Collection: stori_docs")
        print(f"  Points: {info.points_count}")
    except Exception as e:
        print(f"  Verification: {e}")
    
    print(f"\n✅ Done! RAG is ready.")
    print(f"   Total documents: {len(documents)}")
    print(f"   Total chunks: {len(all_chunks)}")


if __name__ == "__main__":
    asyncio.run(main())
