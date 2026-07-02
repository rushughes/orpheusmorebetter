import os
import tempfile

import pytest

from models import TorrentGroup, Torrent
import models.format as fmt
import services.transcode as transcode
from models.exceptions import TranscodeException


def make_torrent(id, **overrides):
    base = dict(
        id=id, groupId=5, media="CD", format="FLAC", seeders=1, snatched=1,
        description="", remasterYear=2008, remasterTitle="",
        remasterRecordLabel="", remasterCatalogueNumber="", fileList="a",
        filePath="d", encoding="Lossless",
    )
    base.update(overrides)
    return Torrent(**base)


def make_group():
    return TorrentGroup(
        groupId=5, groupName="Quantum of Solace", groupYear=2008, torrent=[],
        composers=[], dj=[], artists=[{"id": 1, "name": "Various Artists"}],
        with_artists=[], conductor=[], remixed_by=[], producer=[],
    )


class TestEditionDirname:
    g = make_group()

    def test_no_edition_is_bare_media(self):
        name = self.g.get_transcode_dirname(make_torrent(1), fmt.MP3_320)
        assert name == "Various Artists - 2008 - Quantum of Solace {CD} [MP3 320]"

    def test_edition_injected(self):
        name = self.g.get_transcode_dirname(
            make_torrent(1), fmt.MP3_320, "Expanded 2CD"
        )
        assert "{CD ~ Expanded 2CD}" in name

    def test_edition_disambiguates_two_torrents(self):
        a = self.g.get_transcode_dirname(make_torrent(1), fmt.MP3_320, "Official")
        b = self.g.get_transcode_dirname(make_torrent(2), fmt.MP3_320, "Expanded 2CD")
        assert a != b

    def test_edition_respected_when_absent(self):
        t = make_torrent(1, remasterTitle="Deluxe", remasterYear=2015)
        name = self.g.get_transcode_dirname(t, fmt.MP3_320)
        assert "{CD ~ Deluxe 2015}" in name

    def test_edition_overrides_existing_remaster_title(self):
        t = make_torrent(1, remasterTitle="Deluxe", remasterYear=2015)
        name = self.g.get_transcode_dirname(t, fmt.MP3_320, "My Label")
        assert "{CD ~ My Label}" in name
        assert "Deluxe" not in name


class TestEmptyFlacGuard:
    def test_empty_source_raises(self):
        g = make_group()
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            # source dir has no .flac files at all
            with pytest.raises(TranscodeException, match="No FLAC files"):
                transcode.transcode_release(
                    src, out, fmt.MP3_320, make_torrent(1), g
                )
