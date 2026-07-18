"""Create physical endpoints, logical aliases, and explicit pools."""

from alembic import op

from ecoroute.db import models  # noqa: F401
from ecoroute.db.base import Base

revision = "0002_model_registry"
down_revision = "0001_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("model_endpoints", "logical_models", "logical_model_endpoints"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("logical_model_endpoints", "logical_models", "model_endpoints"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
