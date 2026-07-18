"""Create gateway audit, impact, cache, and carbon evidence tables."""

from alembic import op

from ecoroute.db import models  # noqa: F401
from ecoroute.db.base import Base

revision = "0006_gateway_impact_cache"
down_revision = "0005_training_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in (
        "gateway_requests",
        "route_decisions",
        "model_attempts",
        "impact_records",
        "cache_entries",
        "carbon_readings",
    ):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in (
        "carbon_readings",
        "cache_entries",
        "impact_records",
        "model_attempts",
        "route_decisions",
        "gateway_requests",
    ):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
