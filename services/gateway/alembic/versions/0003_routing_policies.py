"""Create immutable routing-policy versions."""

from alembic import op

from ecoroute.db import models  # noqa: F401
from ecoroute.db.base import Base

revision = "0003_routing_policies"
down_revision = "0002_model_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["routing_policies"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["routing_policies"].drop(bind=op.get_bind(), checkfirst=True)
