import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_orpheusmissing():
    path = os.path.join(ROOT, "orpheusmissing")
    loader = SourceFileLoader("orpheusmissing_script", path)
    spec = importlib.util.spec_from_loader("orpheusmissing_script", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


orpheusmissing = _load_orpheusmissing()


def test_launch_writes_permalinks_and_argv():
    items = [
        SimpleNamespace(permalink="https://orpheus.network/torrents.php?id=1&torrentid=100"),
        SimpleNamespace(permalink="https://orpheus.network/torrents.php?id=2&torrentid=200"),
    ]
    seen = {}

    def runner(cmd):
        seen["cmd"] = list(cmd)
        with open(cmd[-1]) as handle:
            seen["urls"] = handle.read()

        class Result:
            returncode = 0

        return Result()

    code = orpheusmissing.launch_transcoder(
        items, "/tmp/orpheus-config", runner=runner
    )

    assert code == 0
    cmd = seen["cmd"]
    assert cmd[0] == sys.executable
    assert os.path.basename(cmd[1]) == "orpheusmorebetter"
    assert cmd[2:4] == ["--config", "/tmp/orpheus-config"]
    assert seen["urls"] == (
        "https://orpheus.network/torrents.php?id=1&torrentid=100\n"
        "https://orpheus.network/torrents.php?id=2&torrentid=200\n"
    )
    assert not os.path.exists(cmd[-1])


def test_transcode_as_found_launches_each_item_in_order():
    items = [
        SimpleNamespace(permalink="https://orpheus.network/torrents.php?id=1&torrentid=100"),
        SimpleNamespace(permalink="https://orpheus.network/torrents.php?id=2&torrentid=200"),
    ]
    calls = []

    def launch(batch, config_path, totp=None):
        calls.append(([item.permalink for item in batch], config_path, totp))
        return 1 if len(calls) == 1 else 0

    count, failed = orpheusmissing.transcode_as_found(
        iter(items), "/tmp/orpheus-config", totp="123456", launch=launch
    )

    assert count == 2
    assert failed == 1
    assert calls[0][0] == ["https://orpheus.network/torrents.php?id=1&torrentid=100"]
    assert calls[1][0] == ["https://orpheus.network/torrents.php?id=2&torrentid=200"]
    assert calls[0][1:] == ("/tmp/orpheus-config", "123456")
    assert calls[1][1:] == ("/tmp/orpheus-config", "123456")


def test_launch_passes_totp():
    items = [SimpleNamespace(permalink="https://orpheus.network/torrents.php?id=1&torrentid=100")]
    seen = {}

    def runner(cmd):
        seen["cmd"] = list(cmd)

        class Result:
            returncode = 3

        return Result()

    code = orpheusmissing.launch_transcoder(
        items, "/tmp/orpheus-config", totp="123456", runner=runner
    )

    assert code == 3
    assert seen["cmd"][2:6] == ["--config", "/tmp/orpheus-config", "-t", "123456"]
    assert seen["cmd"][-1] != "123456"
