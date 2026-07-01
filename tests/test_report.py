from models import TorrentGroup, Torrent
import models.format as formats
from services import report


def make_torrent(id, fmt, encoding, **overrides):
    base = dict(
        id=id,
        media="CD",
        format=fmt,
        seeders=1,
        snatched=1,
        description="",
        remasterYear=2001,
        remasterTitle="",
        remasterRecordLabel="",
        remasterCatalogueNumber="",
        fileList="a.flac",
        filePath="dir",
        encoding=encoding,
    )
    base.update(overrides)
    return Torrent(**base)


def make_group(group_id, torrents):
    empty = dict(
        composers=[], dj=[], artists=[{"id": 1, "name": "An Artist"}],
        with_artists=[], conductor=[], remixed_by=[], producer=[],
    )
    return TorrentGroup(
        groupId=group_id, groupName="A Record", groupYear=2001,
        torrent=torrents, **empty,
    )


class TestFormatsNeeded:
    def test_flac_only_needs_both_mp3(self):
        flac = make_torrent(100, "FLAC", "Lossless")
        group = make_group(5, [flac])
        needed = report.formats_needed(
            group, flac, {formats.MP3_V0, formats.MP3_320}
        )
        assert needed == {formats.MP3_V0, formats.MP3_320}

    def test_existing_320_leaves_only_v0(self):
        flac = make_torrent(100, "FLAC", "Lossless")
        mp3 = make_torrent(101, "MP3", "320")
        group = make_group(5, [flac, mp3])
        needed = report.formats_needed(
            group, flac, {formats.MP3_V0, formats.MP3_320}
        )
        assert needed == {formats.MP3_V0}

    def test_complete_edition_needs_nothing(self):
        flac = make_torrent(100, "FLAC", "Lossless")
        v0 = make_torrent(101, "MP3", "V0 (VBR)")
        c320 = make_torrent(102, "MP3", "320")
        group = make_group(5, [flac, v0, c320])
        needed = report.formats_needed(
            group, flac, {formats.MP3_V0, formats.MP3_320}
        )
        assert needed == set()

    def test_different_edition_does_not_count(self):
        flac = make_torrent(100, "FLAC", "Lossless", remasterYear=2001)
        # an MP3 for a *different* remaster year shouldn't satisfy the need
        other = make_torrent(101, "MP3", "320", remasterYear=2010)
        group = make_group(5, [flac, other])
        needed = report.formats_needed(
            group, flac, {formats.MP3_V0, formats.MP3_320}
        )
        assert needed == {formats.MP3_V0, formats.MP3_320}

    def test_preemphasized_needs_nothing(self):
        flac = make_torrent(100, "FLAC", "Lossless",
                            remasterTitle="Remaster (Pre-Emphasis)")
        group = make_group(5, [flac])
        needed = report.formats_needed(
            group, flac, {formats.MP3_V0, formats.MP3_320}
        )
        assert needed == set()


class FakeAPI:
    """Minimal WhatAPI stand-in for find_missing."""

    base_url = "https://orpheus.network/"

    def __init__(self, candidates, groups):
        self._candidates = candidates          # list[(gid, tid)]
        self._groups = groups                  # {gid: ajax response dict}
        self.requested_ids = []

    def get_candidates(self, mode, skip=None, media=None):
        for gid, tid in self._candidates:
            if skip is not None and str(tid) in skip:
                continue
            yield gid, tid

    def request_ajax(self, action, **kwargs):
        assert action == "torrentgroup"
        gid = kwargs["id"]
        self.requested_ids.append(gid)
        return self._groups[gid]


def group_response(group_id, torrents):
    return {
        "group": {
            "groupId": group_id,
            "groupName": "A Record",
            "groupYear": 2001,
            "musicInfo": {
                "composers": [], "dj": [],
                "artists": [{"id": 1, "name": "An Artist"}],
                "with": [], "conductor": [], "remixedBy": [], "producer": [],
            },
        },
        "torrents": torrents,
    }


def torrent_dict(id, fmt, encoding):
    return dict(
        id=id, media="CD", format=fmt, seeders=1, snatched=1, description="",
        remasterYear=2001, remasterTitle="", remasterRecordLabel="",
        remasterCatalogueNumber="", fileList="a.flac", filePath="dir",
        encoding=encoding,
    )


class TestFindMissing:
    def test_yields_only_missing_and_records_examined(self):
        # group 1: flac only -> missing both
        # group 2: flac + v0 + 320 -> complete
        groups = {
            1: group_response(1, [torrent_dict(100, "FLAC", "Lossless")]),
            2: group_response(2, [
                torrent_dict(200, "FLAC", "Lossless"),
                torrent_dict(201, "MP3", "V0 (VBR)"),
                torrent_dict(202, "MP3", "320"),
            ]),
        }
        api = FakeAPI(candidates=[(1, 100), (2, 200)], groups=groups)

        examined = set()
        items = list(report.find_missing(
            api, "snatched", set(),
            {formats.MP3_V0, formats.MP3_320},
            on_examined=lambda tid: examined.add(tid),
        ))

        assert len(items) == 1
        assert items[0].torrent_id == 100
        assert items[0].group_id == 1
        assert set(items[0].missing_names) == {"MP3 V0", "MP3 320"}
        assert "torrents.php?id=1&torrentid=100" in items[0].permalink
        # both were examined even though only one was missing
        assert examined == {100, 200}


class TestRenderers:
    def _item(self):
        return report.MissingItem(
            group_id=1, torrent_id=100, artist="An Artist",
            group_name="A Record", year=2001, media="CD", edition="{CD}",
            missing={formats.MP3_V0, formats.MP3_320},
            permalink="https://orpheus.network/torrents.php?id=1&torrentid=100",
        )

    def test_urls(self):
        out = report.render_urls([self._item()])
        assert out == "https://orpheus.network/torrents.php?id=1&torrentid=100\n"

    def test_text_mentions_release(self):
        out = report.render_text([self._item()])
        assert "A Record" in out
        assert "torrentid=100" in out

    def test_text_empty(self):
        assert "No releases" in report.render_text([])

    def test_csv_has_header_and_row(self):
        out = report.render_csv([self._item()])
        assert "permalink" in out.splitlines()[0]
        assert "100" in out
