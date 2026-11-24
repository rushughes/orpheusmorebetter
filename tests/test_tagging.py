import shutil
import subprocess

import pytest

from services import tagging

FFMPEG = shutil.which("ffmpeg")


def _silence(path):
    subprocess.check_call([
        FFMPEG, "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", "0.1", str(path),
    ])


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("A", False),
        ("01", True),
        ("A1", True),
        ("1A", False),
        ("AA1", False),
        ("A01", True),
        ("1/12", True),
        ("A/12", False),
        ("01/12", True),
        ("A1/12", True),
        ("a1/12", True),
        ("1A/12", False),
        ("Z9/10", True),
        ("B02/03", True),
        ("AA1/12", False),
        ("42/80", True),
        ("42/80/80", False),
    ],
)
def test_valid_fractional_tag(value, expected):
    assert tagging.valid_fractional_tag(value) is expected


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
class TestCopyTagsTracknumber:
    def _make(self, tmp_path, flac_tags):
        flac = tmp_path / "a.flac"
        mp3 = tmp_path / "a.mp3"
        _silence(flac)
        _silence(mp3)
        from mutagen.flac import FLAC
        f = FLAC(str(flac))
        f["artist"] = ["A"]
        f["album"] = ["B"]
        f["title"] = ["T"]
        for k, v in flac_tags.items():
            f[k] = [v]
        f.save()
        return str(flac), str(mp3)

    def test_fractional_tracknumber_plus_total(self, tmp_path):
        # regression: tracknumber already "42/80" plus tracktotal "80"
        # must not become "42/80/80"
        flac, mp3 = self._make(
            tmp_path, {"tracknumber": "42/80", "tracktotal": "80"}
        )
        tagging.copy_tags(flac, mp3)
        from mutagen.mp3 import EasyMP3
        assert EasyMP3(mp3)["tracknumber"] == ["42/80"]
        ok, msg = tagging.check_tags(mp3)
        assert ok, msg

    def test_bare_tracknumber_plus_total(self, tmp_path):
        flac, mp3 = self._make(
            tmp_path, {"tracknumber": "42", "tracktotal": "80"}
        )
        tagging.copy_tags(flac, mp3)
        from mutagen.mp3 import EasyMP3
        assert EasyMP3(mp3)["tracknumber"] == ["42/80"]
        ok, msg = tagging.check_tags(mp3)
        assert ok, msg

    def test_fractional_discnumber_plus_total(self, tmp_path):
        flac, mp3 = self._make(
            tmp_path,
            {"tracknumber": "1", "discnumber": "1/2", "disctotal": "2"},
        )
        tagging.copy_tags(flac, mp3)
        from mutagen.mp3 import EasyMP3
        assert EasyMP3(mp3)["discnumber"] == ["1/2"]
