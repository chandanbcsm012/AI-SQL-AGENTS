"""These hit the real Qdrant + Ollama embedding model (infra/docker-compose.yml)
since semantic_schema.py has no meaningful mock-able seam -- skip cleanly if
that infra isn't running rather than failing the whole suite."""
import pytest

import semantic_schema

SCHEMA = [
    {"table": "customer", "columns": [{"name": "customer_id", "type": "INTEGER"}, {"name": "full_name", "type": "TEXT"}]},
    {"table": "product", "columns": [{"name": "product_id", "type": "INTEGER"}, {"name": "category", "type": "TEXT"}]},
    {"table": "orders", "columns": [{"name": "order_id", "type": "INTEGER"}, {"name": "order_date", "type": "TEXT"}]},
]


def _require_qdrant():
    if semantic_schema._get_client() is None:
        pytest.skip("Qdrant not reachable at QDRANT_URL -- see infra/docker-compose.yml")


def test_semantic_rank_returns_relevant_table_first():
    _require_qdrant()
    ranked = semantic_schema.semantic_rank_tables("how many products are there?", SCHEMA, top_k=2)
    assert ranked is not None
    assert ranked[0]["table"] == "product"


def test_semantic_rank_handles_empty_schema():
    _require_qdrant()
    assert semantic_schema.semantic_rank_tables("anything", []) == []
