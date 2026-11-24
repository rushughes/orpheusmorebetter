import json
import os
import unicodedata

import services.transcode as transcode
from models.exceptions import TranscodeException


def test_match_key_treats_nfc_and_nfd_as_equal():
    nfc = "Tr\u00e4ume"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    assert transcode.match_key(nfc) == transcode.match_key(nfd)


def test_remote_for_swaps_prefix_and_ignores_other_trees():
    share = transcode.RemoteShare("rus@romanoff", "/Volumes/media/", "/srv/disk/media/")
    assert share.local_prefix == "/Volumes/media"
    assert share.remote_prefix == "/srv/disk/media"
    assert share.remote_for("/Volumes/media") == "/srv/disk/media"
    assert (
        share.remote_for("/Volumes/media/downloads/Album")
        == "/srv/disk/media/downloads/Album"
    )
    assert share.remote_for("/Volumes/media2/Album") is None


def _listdir(tree):
    def fake_ssh(host, request):
        req = json.loads(request.decode())
        assert req["op"] == "listdir"
        assert host == "host"
        return json.dumps(tree[req["path"]]).encode()

    return fake_ssh


def test_resolve_uses_server_nfc_when_local_name_is_nfd(monkeypatch):
    nfc = "Tr\u00e4ume.flac"
    nfd = unicodedata.normalize("NFD", nfc)
    monkeypatch.setattr(transcode, "ssh_exec", _listdir({
        "/disk": ["Album"],
        "/disk/Album": [nfc],
    }))
    share = transcode.RemoteShare("host", "/Volumes/media", "/disk")
    local = "/Volumes/media/Album/" + nfd
    assert transcode.resolve_remote_path(share, local) == "/disk/Album/" + nfc


def test_resolve_uses_server_nfd_when_local_name_is_nfc(monkeypatch):
    nfc = "Tr\u00e4ume.flac"
    nfd = unicodedata.normalize("NFD", nfc)
    monkeypatch.setattr(transcode, "ssh_exec", _listdir({
        "/disk": ["Album"],
        "/disk/Album": [nfd],
    }))
    share = transcode.RemoteShare("host", "/Volumes/media", "/disk")
    local = "/Volumes/media/Album/" + nfc
    assert transcode.resolve_remote_path(share, local) == "/disk/Album/" + nfd


def test_stage_symlinks_readable_and_fetches_unreadable(tmp_path, monkeypatch):
    src = tmp_path / "share" / "Album"
    src.mkdir(parents=True)
    (src / "good.flac").write_bytes(b"good-bytes")
    (src / "bad.flac").write_bytes(b"original-bytes")
    before = {p.name: (p.read_bytes(), p.is_symlink()) for p in src.iterdir()}

    share = transcode.RemoteShare("rus@romanoff", str(tmp_path / "share"), "/srv/media")

    def fake_ssh(host, request):
        req = json.loads(request.decode())
        if req["op"] == "listdir":
            listing = {
                "/srv/media": ["Album"],
                "/srv/media/Album": ["bad.flac", "good.flac"],
            }
            return json.dumps(listing[req["path"]]).encode()
        if req["op"] == "cat":
            assert req["path"] == "/srv/media/Album/bad.flac"
            assert host == "rus@romanoff"
            return b"server-bytes"
        raise AssertionError(req["op"])

    monkeypatch.setattr(transcode, "file_readable", lambda path: not path.endswith("bad.flac"))
    monkeypatch.setattr(transcode, "ssh_exec", fake_ssh)

    dest = tmp_path / "dest"
    dest.mkdir()
    assert transcode.stage_unreadable(str(src), str(dest), share) is True
    assert os.path.islink(dest / "good.flac")
    assert (dest / "bad.flac").read_bytes() == b"server-bytes"
    assert not (dest / "bad.flac").is_symlink()
    after = {p.name: (p.read_bytes(), p.is_symlink()) for p in src.iterdir()}
    assert after == before


def test_stage_leaves_dest_empty_when_every_file_opens(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.flac").write_bytes(b"a")
    dest = tmp_path / "dest"
    dest.mkdir()

    def fail_ssh(host, request):
        raise AssertionError("ssh should not be used")

    monkeypatch.setattr(transcode, "ssh_exec", fail_ssh)
    assert transcode.stage_unreadable(str(src), str(dest), None) is False
    assert list(dest.iterdir()) == []
    assert (src / "a.flac").read_bytes() == b"a"


def test_stage_requires_remote_config(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.flac").write_bytes(b"a")
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setattr(transcode, "file_readable", lambda path: False)
    try:
        transcode.stage_unreadable(str(src), str(dest), None)
        raise AssertionError("expected TranscodeException")
    except TranscodeException as exc:
        assert "remote_host" in str(exc)
        assert "a.flac" in str(exc)
    assert list(dest.iterdir()) == []
    assert (src / "a.flac").read_bytes() == b"a"


def test_make_torrent_uses_remote_runner_for_mapped_dir(tmp_path, monkeypatch):
    root = tmp_path / "share"
    album = root / "Album"
    album.mkdir(parents=True)
    out = tmp_path / "out"
    share = transcode.RemoteShare("rus@romanoff", str(root), "/srv/media")
    seen = []

    def fake_ssh(host, request):
        req = json.loads(request.decode())
        seen.append(req)
        if req["op"] == "listdir":
            assert req["path"] == "/srv/media"
            return json.dumps(["Album"]).encode()
        if req["op"] == "mktorrent":
            assert host == "rus@romanoff"
            assert req["directory"] == "/srv/media/Album"
            assert req["announce"] == "https://tracker/KEY/announce"
            assert req["source"] == "OPS"
            return b"remote-torrent"
        raise AssertionError(req["op"])

    def fake_local(command, stderr=None):
        raise AssertionError("local mktorrent should not run")

    monkeypatch.setattr(transcode, "ssh_exec", fake_ssh)
    monkeypatch.setattr(transcode.subprocess, "check_output", fake_local)

    result = transcode.make_torrent(
        str(album), str(out), "https://tracker/", "KEY", "OPS", share
    )
    assert open(result, "rb").read() == b"remote-torrent"
    assert any(req["op"] == "mktorrent" for req in seen)


def test_make_torrent_uses_local_mktorrent_when_unmapped(tmp_path, monkeypatch):
    album = tmp_path / "album"
    album.mkdir()
    out = tmp_path / "out"
    share = transcode.RemoteShare("host", "/Volumes/media", "/srv/media")
    calls = []

    def fake_local(command, stderr=None):
        calls.append(command)
        torrent_path = command[command.index("-o") + 1]
        # Already NFC, so make_torrent must leave the bytes unchanged.
        with open(torrent_path, "wb") as fh:
            fh.write(_nfc_torrent())
        return b""

    def fake_ssh(host, request):
        raise AssertionError("ssh should not be used")

    monkeypatch.setattr(transcode.subprocess, "check_output", fake_local)
    monkeypatch.setattr(transcode, "ssh_exec", fake_ssh)

    result = transcode.make_torrent(
        str(album), str(out), "https://tracker/", "KEY", "OPS", share
    )
    assert calls[0][0] == "mktorrent"
    assert "-p" in calls[0]
    assert calls[0][calls[0].index("-s") + 1] == "OPS"
    assert result.endswith(album.name + ".torrent")
    assert open(result, "rb").read() == _nfc_torrent()


def _torrent(name: str, filename: str, pieces: bytes = b"\x11" * 20) -> bytes:
    info = {
        b"files": [{b"length": 3, b"path": [filename.encode("utf-8")]}],
        b"name": name.encode("utf-8"),
        b"piece length": 16384,
        b"pieces": pieces,
    }
    return transcode.bencode({
        b"announce": b"https://tracker/announce",
        b"info": info,
    })


def _nfc_torrent() -> bytes:
    return _torrent("Album", "01 Track.mp3")


def test_nfc_torrent_paths_composes_decomposed_names(tmp_path):
    nfc_name = "11. La Profondit\u00e0.mp3"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    pieces = b"\xab" * 20
    torrent = tmp_path / "release.torrent"
    torrent.write_bytes(_torrent("Le onde", nfd_name, pieces))

    transcode.nfc_torrent_paths(str(torrent))

    data, consumed = transcode.bdecode(torrent.read_bytes())
    assert consumed == torrent.stat().st_size
    info = data[b"info"]
    assert info[b"pieces"] == pieces
    assert info[b"files"][0][b"path"] == [nfc_name.encode("utf-8")]
    assert info[b"name"] == "Le onde".encode("utf-8")


def test_nfc_torrent_paths_leaves_composed_torrent_unchanged(tmp_path):
    raw = _nfc_torrent()
    torrent = tmp_path / "release.torrent"
    torrent.write_bytes(raw)

    transcode.nfc_torrent_paths(str(torrent))

    assert torrent.read_bytes() == raw


def test_remote_script_compiles():
    compile(transcode._REMOTE_SCRIPT, "<remote>", "exec")
