"""Enable pgvector and create the workspace boundary."""

from alembic import op

from ecoroute.db import models  # noqa: F401
from ecoroute.db.base import Base

revision = "0001_base"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.tables["workspaces"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["workspaces"].drop(bind=op.get_bind(), checkfirst=True)
    op.execute("DROP EXTENSION IF EXISTS vector")
