from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stream_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("published", sa.JSON(), nullable=False),
        sa.Column("archive", sa.JSON(), nullable=False),
        sa.Column("pins", sa.JSON(), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_table("stream_state")
