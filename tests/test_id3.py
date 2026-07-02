import os

import services.transcode as transcode


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


class TestFlacHasId3:
    def test_id3v2_header(self, tmp_path):
        p = tmp_path / "a.flac"
        _write(p, b"ID3\x03\x00" + b"\x00" * 300)
        assert transcode.flac_has_id3(str(p))

    def test_id3v1_trailer(self, tmp_path):
        p = tmp_path / "b.flac"
        # last 128 bytes must start with b"TAG"
        _write(p, b"fLaC" + b"\x00" * 100 + b"TAG" + b"\x00" * 125)
        assert transcode.flac_has_id3(str(p))

    def test_clean_flac(self, tmp_path):
        p = tmp_path / "c.flac"
        _write(p, b"fLaC" + b"\x00" * 300)
        assert not transcode.flac_has_id3(str(p))

    def test_short_file_no_crash(self, tmp_path):
        p = tmp_path / "d.flac"
        _write(p, b"fLaC")  # smaller than 128 bytes
        assert not transcode.flac_has_id3(str(p))


class TestFindId3Flacs:
    def test_finds_only_tainted(self, tmp_path):
        _write(tmp_path / "clean.flac", b"fLaC" + b"\x00" * 300)
        _write(tmp_path / "id3.flac", b"ID3\x03" + b"\x00" * 300)
        found = [os.path.basename(f) for f in transcode.find_id3_flacs(str(tmp_path))]
        assert found == ["id3.flac"]


class TestSanitizeFlacDir:
    def test_cleans_tainted_symlinks_clean(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        _write(src / "clean.flac", b"fLaC" + b"\x00" * 300)
        _write(src / "dirty.flac", b"ID3\x03" + b"\x00" * 300)
        _write(src / "cover.jpg", b"x")
        dest = tmp_path / "dest"
        dest.mkdir()

        encoded = []

        def fake_encode(s, out):
            encoded.append(os.path.basename(out))
            with open(out, "wb") as f:
                f.write(b"fLaC-clean")

        monkeypatch.setattr(transcode, "_encode_to_flac", fake_encode)

        cleaned = transcode.sanitize_flac_dir(str(src), str(dest))

        # only the dirty one is re-encoded
        assert encoded == ["dirty.flac"]
        assert [os.path.basename(c) for c in cleaned] == ["dirty.flac"]
        # the clean flac is symlinked, not rewritten
        assert os.path.islink(str(dest / "clean.flac"))
        # sidecar carried along and readable
        assert (dest / "cover.jpg").exists()
