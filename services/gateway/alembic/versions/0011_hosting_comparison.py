"""Add independent impact baselines and benchmark calibration metadata."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_hosting_comparison"
down_revision = "0010_azure_regional_endpoints"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("logical_models", "impact_baseline_endpoint_id"):
        op.add_column(
            "logical_models",
            sa.Column("impact_baseline_endpoint_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if not _has_column("model_endpoints", "calibration"):
        op.add_column("model_endpoints", sa.Column("calibration", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    if _has_column("model_endpoints", "calibration"):
        op.drop_column("model_endpoints", "calibration")
    if _has_column("logical_models", "impact_baseline_endpoint_id"):
        op.drop_column("logical_models", "impact_baseline_endpoint_id")
