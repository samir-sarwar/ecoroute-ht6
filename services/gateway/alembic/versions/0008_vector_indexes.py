"""Add ANN indexes after the vector-bearing tables exist."""

from alembic import op

revision = "0008_vector_indexes"
down_revision = "0007_agents_benchmarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_dataset_examples_embedding_hnsw "
        "ON dataset_examples USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cache_entries_embedding_hnsw "
        "ON cache_entries USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cache_entries_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_dataset_examples_embedding_hnsw")
