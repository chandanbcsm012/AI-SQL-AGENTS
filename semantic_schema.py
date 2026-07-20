"""Semantic (embedding-based) schema retrieval via Qdrant -- an upgrade
path for agents/schema_retriever.py's lexical `_rank_tables` once a schema
grows past a few dozen tables, where keyword overlap stops being reliable.

Opt-in via SCHEMA_RETRIEVAL_BACKEND=semantic (default: "lexical", i.e. this
module isn't used at all unless explicitly enabled). Fails open to the
caller (returns None) on any error -- an unreachable Qdrant or embedding
model degrades to the existing lexical ranking, never breaks the query.

Re-embeds the current (already ACL-filtered) schema on every call rather
than maintaining a persistent index: at this project's table-count scale
that's cheap, and it sidesteps invalidation entirely (no stale-embedding
bugs when a table is added/dropped) since schema_context differs by user
anyway (auth.visible_tables()). A future optimization: skip re-embedding
when the schema's hash hasn't changed since the last upsert.
"""
import hashlib
import logging
import os

logger = logging.getLogger("semantic_schema")

COLLECTION_NAME = "schema_tables"
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

_client = None
_unavailable = False


def _get_client():
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        try:
            from qdrant_client import QdrantClient

            _client = QdrantClient(url=QDRANT_URL, timeout=2)
            _client.get_collections()  # cheap reachability check
        except Exception as e:
            logger.warning("qdrant_unavailable", extra={"error": str(e)})
            _unavailable = True
            return None
    return _client


def _table_text(entry: dict) -> str:
    cols = ", ".join(c["name"] for c in entry["columns"])
    return f"{entry['table']}({cols})"


def _point_id(table_name: str) -> int:
    return int(hashlib.sha256(table_name.encode()).hexdigest()[:15], 16)


def _ensure_collection(client, vector_size: int) -> None:
    from qdrant_client.models import Distance, VectorParams

    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


def semantic_rank_tables(question: str, schema: list[dict], top_k: int = 4) -> list[dict] | None:
    """Returns the top_k semantically-closest tables, or None if Qdrant/the
    embedding model isn't available (caller should fall back to lexical)."""
    if not schema:
        return schema

    client = _get_client()
    if client is None:
        return None

    try:
        from qdrant_client.models import PointStruct

        from model_factory import get_embedding_model

        embedder = get_embedding_model()
        table_texts = [_table_text(e) for e in schema]
        table_vectors = embedder.embed_documents(table_texts)
        _ensure_collection(client, len(table_vectors[0]))

        points = [
            PointStruct(id=_point_id(entry["table"]), vector=vector, payload={"table": entry["table"]})
            for entry, vector in zip(schema, table_vectors)
        ]
        client.upsert(collection_name=COLLECTION_NAME, points=points)

        question_vector = embedder.embed_query(question)
        hits = client.query_points(
            collection_name=COLLECTION_NAME,
            query=question_vector,
            limit=top_k,
            query_filter={"must": [{"key": "table", "match": {"any": [e["table"] for e in schema]}}]},
        ).points

        by_table = {e["table"]: e for e in schema}
        ranked = [by_table[h.payload["table"]] for h in hits if h.payload["table"] in by_table]
        return ranked or schema[:top_k]
    except Exception as e:
        logger.warning("semantic_rank_failed", extra={"error": str(e)})
        return None
