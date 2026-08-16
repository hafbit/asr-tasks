"""Initial asr-tasks schema.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
    )
    op.create_index("ix_assets_sha256", "assets", ["sha256"])
    op.create_table(
        "glossaries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("hotwords", sa.JSON(), nullable=False),
        sa.Column("replacements", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "worker_states",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("model_ready", sa.Boolean(), nullable=False),
        sa.Column("current_job_id", sa.String(length=64), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "transcription_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.String(length=255), nullable=True),
        sa.Column("glossary_id", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("hotwords", sa.JSON(), nullable=False),
        sa.Column("replacements", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("total_seconds", sa.Float(), nullable=False),
        sa.Column("processed_seconds", sa.Float(), nullable=False),
        sa.Column("partial_text", sa.Text(), nullable=False),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("result_metadata", sa.JSON(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["glossary_id"], ["glossaries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_request_id"),
    )
    op.create_index(
        "ix_jobs_claim", "transcription_jobs", ["status", "lease_expires_at", "created_at"]
    )
    op.create_index("ix_transcription_jobs_created_at", "transcription_jobs", ["created_at"])
    op.create_index("ix_transcription_jobs_lease_owner", "transcription_jobs", ["lease_owner"])
    op.create_index("ix_transcription_jobs_status", "transcription_jobs", ["status"])
    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["transcription_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "transcription_segments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("replacements", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["transcription_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_segment_job_ordinal"),
    )


def downgrade() -> None:
    op.drop_table("transcription_segments")
    op.drop_table("job_events")
    op.drop_index("ix_transcription_jobs_status", table_name="transcription_jobs")
    op.drop_index("ix_transcription_jobs_lease_owner", table_name="transcription_jobs")
    op.drop_index("ix_transcription_jobs_created_at", table_name="transcription_jobs")
    op.drop_index("ix_jobs_claim", table_name="transcription_jobs")
    op.drop_table("transcription_jobs")
    op.drop_table("worker_states")
    op.drop_table("glossaries")
    op.drop_index("ix_assets_sha256", table_name="assets")
    op.drop_table("assets")
