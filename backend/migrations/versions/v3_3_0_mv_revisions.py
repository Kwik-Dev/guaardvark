"""music_video_revisions: saved versions of a music video's creative state.

New table only (no ALTER of existing tables). ``db.create_all()`` on boot also
creates it; ``IF NOT EXISTS`` keeps this revision safe on databases where it
already did.

Revision ID: v3_3_0_mv_revisions
Revises: v2_6_1_baseline
Create Date: 2026-10-15
"""
from alembic import op

revision = "v3_3_0_mv_revisions"
down_revision = "v2_6_1_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS music_video_revisions (
            id SERIAL PRIMARY KEY,
            music_video_id INTEGER NOT NULL,
            label VARCHAR(255),
            reason VARCHAR(32) NOT NULL DEFAULT 'manual',
            snapshot JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT fk_mv_revision_music_video_id FOREIGN KEY (music_video_id)
                REFERENCES music_videos (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_music_video_revisions_music_video_id "
        "ON music_video_revisions (music_video_id)"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS music_video_revisions")
