"""Create SLM profiles, policy documents, and versioned datasets."""

from alembic import op

from ecoroute.db import models  # noqa: F401
from ecoroute.db.base import Base

revision = "0004_slm_datasets"
down_revision = "0003_routing_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("slm_profiles", "policy_documents", "datasets", "dataset_examples"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("dataset_examples", "datasets", "policy_documents", "slm_profiles"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
