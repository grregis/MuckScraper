"""Add short-lived encrypted tokens for optional OIDC provider logout."""
from alembic import op
import sqlalchemy as sa

revision = "b8d2e4f6a901"
down_revision = "b3d7f1a9c2e8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oidc_logout_tokens",
        sa.Column("handle_hash", sa.String(64), primary_key=True),
        sa.Column("identity_key", sa.String(64), sa.ForeignKey("oidc_identities.identity_key", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_oidc_logout_tokens_expires_at", "oidc_logout_tokens", ["expires_at"])


def downgrade():
    op.drop_table("oidc_logout_tokens")
