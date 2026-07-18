"""Add Azure regional deployment metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0010_azure_regional_endpoints"
down_revision = "0009_live_regional_carbon"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("model_endpoints", "azure_deployment_type"):
        op.add_column(
            "model_endpoints",
            sa.Column("azure_deployment_type", sa.String(length=40), nullable=True),
        )


def downgrade() -> None:
    if _has_column("model_endpoints", "azure_deployment_type"):
        op.drop_column("model_endpoints", "azure_deployment_type")
