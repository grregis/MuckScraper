"""Add optional OIDC identity bindings without changing existing accounts."""
from alembic import op
import sqlalchemy as sa

revision = "a4c6e8b0d2f5"
down_revision = "a9c4e2f7b1d5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oidc_identities",
        sa.Column("identity_key", sa.String(64), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    )


def downgrade():
    op.drop_table("oidc_identities")
