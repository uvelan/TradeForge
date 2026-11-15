from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("feed", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("price_basis", sa.String(), nullable=False),
        sa.Column("price_scale", sa.Integer(), nullable=False),
        sa.Column("start_utc", sa.BigInteger(), nullable=False),
        sa.Column("end_utc", sa.BigInteger(), nullable=False),
        sa.Column("acquired_utc", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("validation_status", sa.String(), nullable=False),
        sa.CheckConstraint("end_utc > start_utc AND row_count > 0 AND price_scale > 0"),
    )
    op.create_index("ix_dataset_series", "dataset_versions", ["provider", "feed", "symbol", "timeframe"])


def downgrade():
    op.drop_table("dataset_versions")
