import sqlite3

import models.format as formats
from services import report
from services.missing_cache import MissingCache, formats_key
from tests.test_report import FakeAPI, group_response, torrent_dict


BOTH = {formats.MP3_V0, formats.MP3_320}
KEY = "320,V0"


def _groups():
    return {
        1: group_response(1, [torrent_dict(100, "FLAC", "Lossless")]),
        2: group_response(2, [
            torrent_dict(200, "FLAC", "Lossless"),
            torrent_dict(201, "MP3", "V0 (VBR)"),
            torrent_dict(202, "MP3", "320"),
        ]),
    }


def _scan(api, cache, requested=BOTH, refresh=False):
    api.requested_ids = []
    return list(report.find_missing(
        api, "snatched", set(), requested,
        cache=cache,
        formats_key=formats_key(requested),
        refresh=refresh,
    ))


class TestFormatsKey:
    def test_v0_and_320_sort_stably(self):
        assert formats_key(BOTH) == KEY

    def test_single_format(self):
        assert formats_key({formats.MP3_V0}) == "V0"


class TestMissingCache:
    def test_journal_is_rollback_not_wal(self, tmp_path):
        cache = MissingCache(str(tmp_path / "nested" / "cache.sqlite"))
        mode = cache._conn.execute("PRAGMA journal_mode").fetchone()[0]
        cache.close()
        assert mode.lower() == "delete"

    def test_skips_complete_and_rechecks_missing(self, tmp_path):
        cache = MissingCache(str(tmp_path / "cache.sqlite"))
        groups = _groups()
        api = FakeAPI(candidates=[(1, 100), (2, 200)], groups=groups)

        first = _scan(api, cache)
        assert [item.torrent_id for item in first] == [100]
        assert api.requested_ids == [1, 2]
        assert cache.complete_ids(KEY) == {"200"}

        second = _scan(api, cache)
        assert [item.torrent_id for item in second] == [100]
        # the complete album is not fetched again; the missing one is
        assert api.requested_ids == [1]

        groups[1] = group_response(1, [
            torrent_dict(100, "FLAC", "Lossless"),
            torrent_dict(101, "MP3", "V0 (VBR)"),
            torrent_dict(102, "MP3", "320"),
        ])
        third = _scan(api, cache)
        assert third == []
        assert api.requested_ids == [1]
        assert cache.complete_ids(KEY) == {"100", "200"}

        fourth = _scan(api, cache)
        assert fourth == []
        assert api.requested_ids == []
        cache.close()

    def test_different_formats_key_is_not_skipped(self, tmp_path):
        cache = MissingCache(str(tmp_path / "cache.sqlite"))
        api = FakeAPI(
            candidates=[(1, 100), (2, 200)], groups=_groups(),
        )
        _scan(api, cache)
        assert cache.complete_ids(KEY) == {"200"}

        only_v0 = _scan(api, cache, requested={formats.MP3_V0})
        assert api.requested_ids == [1, 2]
        assert [item.torrent_id for item in only_v0] == [100]
        assert cache.complete_ids("V0") == {"200"}
        # the original pair is unchanged
        assert cache.complete_ids(KEY) == set()
        cache.close()

    def test_refresh_refetches_complete(self, tmp_path):
        cache = MissingCache(str(tmp_path / "cache.sqlite"))
        api = FakeAPI(
            candidates=[(1, 100), (2, 200)], groups=_groups(),
        )
        _scan(api, cache)
        assert "200" in cache.complete_ids(KEY)

        refreshed = _scan(api, cache, refresh=True)
        assert 2 in api.requested_ids
        assert [item.torrent_id for item in refreshed] == [100]
        mode = cache._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "delete"
        # row was rewritten, still complete
        row = cache._conn.execute(
            "SELECT status, missing FROM examined WHERE torrent_id = 200"
        ).fetchone()
        assert row == ("complete", "")
        cache.close()


class TestSchema:
    def test_missing_row_keeps_report_fields(self, tmp_path):
        cache = MissingCache(str(tmp_path / "cache.sqlite"))
        api = FakeAPI(candidates=[(1, 100)], groups=_groups())
        _scan(api, cache)
        row = cache._conn.execute(
            """
            SELECT status, artist, group_name, year, missing, permalink
            FROM examined WHERE torrent_id = 100
            """
        ).fetchone()
        cache.close()
        assert row[0] == "missing"
        assert row[1] == "An Artist"
        assert row[2] == "A Record"
        assert row[3] == 2001
        assert "MP3 V0" in row[4] and "MP3 320" in row[4]
        assert "torrentid=100" in row[5]
        # busy timeout is set; opening the file directly still reads the row
        conn = sqlite3.connect(tmp_path / "cache.sqlite")
        assert conn.execute(
            "SELECT status FROM examined WHERE torrent_id = 100"
        ).fetchone()[0] == "missing"
        conn.close()
