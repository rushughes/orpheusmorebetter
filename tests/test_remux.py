import os

import services.transcode as transcode


class TestFindRemuxable:
    def _make_tree(self, tmp_path):
        # extensions the finder should consider, plus some it should ignore
        names = {
            "a.m4a": "flac",     # lossless -> remux
            "b.m4a": "alac",     # lossless -> remux
            "c.m4a": "aac",      # lossy    -> skip
            "d.mp4": "flac",     # lossless -> remux
            "e.flac": "flac",    # already flac, wrong ext for finder -> skip
            "f.txt": None,       # not audio -> skip
        }
        for name in names:
            (tmp_path / name).write_bytes(b"x")
        return names

    def test_only_lossless_containers(self, tmp_path, monkeypatch):
        names = self._make_tree(tmp_path)

        def fake_codec(path):
            return names[os.path.basename(path)]

        monkeypatch.setattr(transcode, "audio_codec", fake_codec)

        found = [os.path.basename(f) for f in transcode.find_remuxable(str(tmp_path))]
        assert set(found) == {"a.m4a", "b.m4a", "d.mp4"}

    def test_none_when_all_lossy(self, tmp_path, monkeypatch):
        (tmp_path / "only.m4a").write_bytes(b"x")
        monkeypatch.setattr(transcode, "audio_codec", lambda p: "aac")
        assert transcode.find_remuxable(str(tmp_path)) == []


class TestConstants:
    def test_flac_not_in_remuxable_extensions(self):
        # we only remux non-FLAC containers
        assert ".flac" not in transcode.REMUXABLE_EXTENSIONS

    def test_lossy_codecs_excluded(self):
        assert "aac" not in transcode.LOSSLESS_REMUX_CODECS
        assert {"flac", "alac"} <= transcode.LOSSLESS_REMUX_CODECS
