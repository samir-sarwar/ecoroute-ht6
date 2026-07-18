"""Create durable training lifecycles and background jobs."""

from alembic import op

from ecoroute.db import models  # noqa: F401
from ecoroute.db.base import Base

revision = "0005_training_jobs"
down_revision = "0004_slm_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("training_runs", "training_run_events", "jobs"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("jobs", "training_run_events", "training_runs"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
