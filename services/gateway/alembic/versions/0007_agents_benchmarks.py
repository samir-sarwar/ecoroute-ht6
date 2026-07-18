"""Create node-agent telemetry, control events, and benchmarks."""

from alembic import op

from ecoroute.db import models  # noqa: F401
from ecoroute.db.base import Base

revision = "0007_agents_benchmarks"
down_revision = "0006_gateway_impact_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("node_agents", "telemetry_samples", "optimization_events", "benchmarks"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("benchmarks", "optimization_events", "telemetry_samples", "node_agents"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
