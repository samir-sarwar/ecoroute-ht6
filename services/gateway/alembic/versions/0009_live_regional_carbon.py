"""Add live grid provenance and regional processing evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_live_regional_carbon"
down_revision = "0008_vector_indexes"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def _add_column(table: str, column: sa.Column[object]) -> None:
    # Older migrations create tables from the current ORM metadata. These
    # guards make both a fresh install and an upgrade from 0008 deterministic.
    if not _has_column(table, column.name):
        op.add_column(table, column)


def _drop_column(table: str, column: str) -> None:
    if _has_column(table, column):
        op.drop_column(table, column)


def _unique_constraints(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        str(item["name"])
        for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }


def upgrade() -> None:
    _add_column(
        "model_endpoints",
        sa.Column("grid_lookup_mode", sa.String(length=20), nullable=False, server_default="zone"),
    )
    _add_column(
        "model_endpoints", sa.Column("grid_data_center_provider", sa.String(length=100))
    )
    _add_column(
        "model_endpoints", sa.Column("grid_data_center_region", sa.String(length=100))
    )
    _add_column(
        "model_endpoints",
        sa.Column(
            "processing_location_evidence",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
    )
    _add_column(
        "model_endpoints",
        sa.Column(
            "grid_attribution", sa.String(length=40), nullable=False, server_default="unknown"
        ),
    )
    _add_column(
        "impact_records",
        sa.Column(
            "carbon_accounting_available", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    _add_column(
        "carbon_readings",
        sa.Column(
            "reading_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    _add_column(
        "carbon_readings",
        sa.Column(
            "lookup_key", sa.String(length=240), nullable=False, server_default="zone"
        ),
    )
    constraints = _unique_constraints("carbon_readings")
    if "uq_carbon_readings_zone" in constraints:
        op.drop_constraint("uq_carbon_readings_zone", "carbon_readings", type_="unique")
    if "uq_carbon_readings_lookup" not in constraints:
        op.create_unique_constraint(
            "uq_carbon_readings_lookup",
            "carbon_readings",
            ["zone", "observed_at", "source", "lookup_key"],
        )


def downgrade() -> None:
    constraints = _unique_constraints("carbon_readings")
    if "uq_carbon_readings_lookup" in constraints:
        op.drop_constraint("uq_carbon_readings_lookup", "carbon_readings", type_="unique")
    _drop_column("carbon_readings", "lookup_key")
    if "uq_carbon_readings_zone" not in _unique_constraints("carbon_readings"):
        op.create_unique_constraint(
            "uq_carbon_readings_zone",
            "carbon_readings",
            ["zone", "observed_at", "source"],
        )
    _drop_column("carbon_readings", "reading_metadata")
    _drop_column("impact_records", "carbon_accounting_available")
    _drop_column("model_endpoints", "grid_attribution")
    _drop_column("model_endpoints", "processing_location_evidence")
    _drop_column("model_endpoints", "grid_data_center_region")
    _drop_column("model_endpoints", "grid_data_center_provider")
    _drop_column("model_endpoints", "grid_lookup_mode")
