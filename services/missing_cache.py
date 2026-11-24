"""Shared SQLite cache of albums orpheusmissing has already checked.

Complete torrents are skipped on later runs. Missing torrents are stored for
the record but looked up again, so a transcode that appears later is noticed.

The file is meant to live on a share both machines mount (see ``missing_cache``
in the config). Rollback journaling is used instead of WAL: WAL plus a network
filesystem is a common way to corrupt the database. Run one scan at a time.
"""

import os
import sqlite3
from datetime import datetime, timezone

from models.format import Format

from services.report import MissingItem


def formats_key(requested: set[Format]) -> str:
    """Stable id for the format set a row was checked against.

    ``{MP3 V0, MP3 320}`` becomes ``320,V0``. A different set does not reuse
    these rows.
    """
    labels = []
    for fmt in requested:
        if fmt.encoding == "V0 (VBR)":
            labels.append("V0")
        elif fmt.encoding == "320":
            labels.append("320")
        else:
            labels.append(fmt.long_name or fmt.name)
    return ",".join(sorted(labels))


class MissingCache:
    def __init__(self, path: str):
        self.path = os.path.expanduser(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # timeout is the busy wait, in seconds. Journal mode stays DELETE
        # (the SQLite default); do not enable WAL on a network share.
        self._conn = sqlite3.connect(self.path, timeout=30)
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS examined (
                torrent_id INTEGER PRIMARY KEY,
                group_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('complete', 'missing')),
                formats_key TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                artist TEXT,
                group_name TEXT,
                year INTEGER,
                media TEXT,
                edition TEXT,
                missing TEXT,
                permalink TEXT
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def complete_ids(self, key: str) -> set[str]:
        """Torrent ids confirmed complete for this format set, as strings.

        ``get_candidates`` compares skip entries with the id text from the
        history page, so these are strings rather than ints.
        """
        rows = self._conn.execute(
            """
            SELECT torrent_id FROM examined
            WHERE status = 'complete' AND formats_key = ?
            """,
            (key,),
        )
        return {str(row[0]) for row in rows}

    def mark_complete(
        self,
        *,
        torrent_id: int,
        group_id: int,
        formats_key: str,
        artist: str = "",
        group_name: str = "",
        year: int | None = None,
        media: str = "",
        edition: str = "",
        permalink: str = "",
    ) -> None:
        self._upsert(
            torrent_id=torrent_id,
            group_id=group_id,
            status="complete",
            formats_key=formats_key,
            artist=artist,
            group_name=group_name,
            year=year,
            media=media,
            edition=edition,
            missing="",
            permalink=permalink,
        )

    def mark_missing(self, item: MissingItem, formats_key: str) -> None:
        self._upsert(
            torrent_id=item.torrent_id,
            group_id=item.group_id,
            status="missing",
            formats_key=formats_key,
            artist=item.artist,
            group_name=item.group_name,
            year=item.year,
            media=item.media,
            edition=item.edition,
            missing=", ".join(item.missing_names),
            permalink=item.permalink,
        )

    def _upsert(
        self,
        *,
        torrent_id: int,
        group_id: int,
        status: str,
        formats_key: str,
        artist: str,
        group_name: str,
        year: int | None,
        media: str,
        edition: str,
        missing: str,
        permalink: str,
    ) -> None:
        checked_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO examined (
                torrent_id, group_id, status, formats_key, checked_at,
                artist, group_name, year, media, edition, missing, permalink
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(torrent_id) DO UPDATE SET
                group_id = excluded.group_id,
                status = excluded.status,
                formats_key = excluded.formats_key,
                checked_at = excluded.checked_at,
                artist = excluded.artist,
                group_name = excluded.group_name,
                year = excluded.year,
                media = excluded.media,
                edition = excluded.edition,
                missing = excluded.missing,
                permalink = excluded.permalink
            """,
            (
                torrent_id,
                group_id,
                status,
                formats_key,
                checked_at,
                artist,
                group_name,
                year,
                media,
                edition,
                missing,
                permalink,
            ),
        )
        self._conn.commit()
