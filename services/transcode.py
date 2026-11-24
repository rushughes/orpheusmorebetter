import errno
import json
import os
import os.path as path
import re
import shlex
import shutil
import signal
import subprocess
import unicodedata

from typing import Callable, Optional
import logging

LOGGER = logging.getLogger("transcode")


def match_key(name: str) -> str:
    """Normalize a filename so NFC and NFD spellings compare equal.

    Invisible formatting characters are stripped, then the name is composed
    to NFC. A Mac Samba client and the Linux disk often disagree on which
    form a name is stored in; this key treats those forms as the same name.
    """
    stripped = "".join(ch for ch in name if ch.isprintable())
    return unicodedata.normalize("NFC", stripped)


class RemoteShare:
    """SSH view of a Samba share, so names can be read as the server stores them."""

    def __init__(self, host: str, local_prefix: str, remote_prefix: str):
        self.host = host
        self.local_prefix = os.path.abspath(local_prefix)
        self.remote_prefix = remote_prefix.rstrip("/")

    def remote_for(self, local_path: str) -> str | None:
        """Map a local path under ``local_prefix`` to the remote prefix.

        Returns None when ``local_path`` is outside the configured prefix.
        The result still uses the local path's own spelling; call
        ``resolve_remote_path`` for the name that exists on the server.
        """
        local_path = os.path.abspath(local_path)
        prefix = self.local_prefix
        if local_path != prefix and not local_path.startswith(prefix + os.sep):
            return None
        rel = os.path.relpath(local_path, prefix)
        if rel == os.curdir:
            return self.remote_prefix
        return self.remote_prefix + "/" + "/".join(rel.split(os.sep))

import mutagen.flac

from . import tagging

import models.format
from models import Torrent, TorrentGroup, Format
from models.exceptions import TranscodeException, TranscodeDownmixException, UnknownSampleRateException

# In most Unix shells, pipelines only report the return code of the
# last process. We need to know if any process in the transcode
# pipeline fails, not just the last one.
#
# This function constructs a pipeline of processes from a chain of
# commands just like a shell does, but it returns the status code (and
# stderr) of every process in the pipeline, not just the last one. The
# results are returned as a list of (code, stderr) pairs, one pair per
# process.
def run_pipeline(cmds: list[str]) -> list[tuple[int, str]]:
    # The Python executable (and its children) ignore SIGPIPE. (See
    # http://bugs.python.org/issue1652) Our subprocesses need to see
    # it.
    sigpipe_handler = signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    stdin = None
    last_proc = None
    procs = []
    try:
        for cmd in cmds:
            proc = subprocess.Popen(
                shlex.split(cmd),
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if last_proc is not None and last_proc.stdout is not None:
                # Ensure last_proc receives SIGPIPE if proc exits first
                last_proc.stdout.close()
            procs.append(proc)
            stdin = proc.stdout
            last_proc = proc
    finally:
        signal.signal(signal.SIGPIPE, sigpipe_handler)

    if last_proc is None:
        LOGGER.warning('No commands run.')
        return []

    _, stderr = last_proc.communicate()

    results: list[tuple[int, str]] = []
    for cmd, proc in zip(cmds[:-1], procs[:-1]):
        # wait() is OK here, despite use of PIPE above; these procs
        # are finished.
        proc.wait()
        results.append((proc.returncode, proc.stderr.read()))
    results.append((last_proc.returncode, stderr))
    return results


def locate(root: str, match_function: Callable[[str], bool], ignore_dotfiles:bool=True):
    """
    Yields all filenames within the root directory for which match_function returns True.
    """
    for path, _, files in os.walk(root):
        for filename in (
            os.path.abspath(os.path.join(path, filename))
            for filename in files
            if match_function(filename)
        ):
            if ignore_dotfiles and os.path.basename(filename).startswith("."):
                pass
            else:
                yield filename


def ext_matcher(*extensions: str) -> Callable[[str], bool]:
    """
    Returns a function which checks if a filename has one of the specified extensions. Expects a string in the format '.ext'.
    """
    return lambda f: os.path.splitext(f)[-1].lower() in extensions


# Non-FLAC containers that may hold lossless audio we can losslessly remux.
REMUXABLE_EXTENSIONS = (".m4a", ".mp4", ".m4b")
# Codecs inside those containers that are lossless and therefore safe to remux
# to FLAC without any generational quality loss. (AAC etc. are deliberately
# excluded -- transcoding a lossy source to FLAC/MP3 is not acceptable.)
LOSSLESS_REMUX_CODECS = {"flac", "alac"}

# Non-audio files worth carrying into a transcode (cover art, logs, etc.).
SIDECAR_EXTENSIONS = (
    ".cue", ".gif", ".jpeg", ".jpg", ".log",
    ".md5", ".nfo", ".pdf", ".png", ".sfv", ".txt",
)


def audio_codec(path: str) -> Optional[str]:
    """Return the first audio stream's codec name via ffprobe, or None if it
    can't be determined (ffprobe missing, or the file has no audio)."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stderr=subprocess.STDOUT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.decode(errors="replace").strip() or None


def find_remuxable(source_dir: str) -> list[str]:
    """Find non-FLAC files under source_dir that hold lossless audio we can
    losslessly remux to FLAC (e.g. FLAC or ALAC inside an .m4a container)."""
    remuxable: list[str] = []
    for f in locate(source_dir, ext_matcher(*REMUXABLE_EXTENSIONS)):
        if audio_codec(f) in LOSSLESS_REMUX_CODECS:
            remuxable.append(f)
    return sorted(remuxable)


def _encode_to_flac(src: str, out_path: str) -> None:
    """Losslessly (re-)encode src to a clean, standard FLAC at out_path using
    ffmpeg. Used both to remux non-FLAC containers and to strip junk (e.g. ID3
    tags) from FLACs that the flac CLI can't decode. The audio samples are
    preserved, so there is no quality loss."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-i", src,
        "-map", "0:a",
        "-c:a", "flac", "-compression_level", "8",
        "-map_metadata", "0",
        out_path,
    ]
    try:
        subprocess.check_output(command, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        raise TranscodeException(
            "ffmpeg is required to prepare non-standard sources but was not found"
        )
    except subprocess.CalledProcessError as e:
        raise TranscodeException(
            'Preparing "{0}" with ffmpeg failed: {1}'.format(src, e.output)
        )


def _stage_link(src: str, dst: str) -> None:
    """Make src available at dst cheaply: symlink if possible, else copy."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.symlink(os.path.abspath(src), dst)
    except OSError:
        shutil.copy(src, dst)


def _copy_sidecars(source_dir: str, dest_dir: str, link: bool = False) -> None:
    """Bring sidecar files (cover art, logs, cue sheets, ...) into dest_dir."""
    for side in locate(source_dir, ext_matcher(*SIDECAR_EXTENSIONS)):
        rel = os.path.relpath(side, source_dir)
        dst = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if link:
            _stage_link(side, dst)
        else:
            shutil.copy(side, dst)


def remux_to_flac(files: list[str], source_dir: str, dest_dir: str) -> list[str]:
    """Losslessly remux the given container files to FLAC under dest_dir,
    preserving their relative layout and copying over sidecar files (art, logs)
    so the eventual transcode is complete. Returns the FLAC paths created.

    Requires ffmpeg. The audio is re-encoded to the FLAC codec but the samples
    are identical to the (already lossless) source, so there is no quality loss.
    """
    outputs: list[str] = []
    for f in files:
        rel = os.path.relpath(f, source_dir)
        out_path = os.path.join(dest_dir, os.path.splitext(rel)[0] + ".flac")
        _encode_to_flac(f, out_path)
        outputs.append(out_path)

    _copy_sidecars(source_dir, dest_dir)
    return outputs


def flac_has_id3(path: str) -> bool:
    """Return True if a FLAC file carries non-standard ID3 tags -- an ID3v2
    header at the start or an ID3v1 tag at the end. Such tags are invalid in
    FLAC and make the flac CLI decoder fail with LOST_SYNC at end of stream,
    even though the audio itself is fine."""
    try:
        with open(path, "rb") as f:
            if f.read(3) == b"ID3":
                return True
            f.seek(-128, os.SEEK_END)
            if f.read(3) == b"TAG":
                return True
    except OSError:
        return False
    return False


def find_id3_flacs(source_dir: str) -> list[str]:
    """Find .flac files under source_dir that carry ID3 tags (and so won't
    decode with the flac CLI)."""
    return sorted(
        f for f in locate(source_dir, ext_matcher(".flac")) if flac_has_id3(f)
    )


def sanitize_flac_dir(source_dir: str, dest_dir: str) -> list[str]:
    """Stage a copy of a FLAC release under dest_dir with ID3-tainted files
    cleaned. ID3-tainted FLACs are re-encoded to clean FLAC with ffmpeg;
    already-clean FLACs (and sidecar files) are symlinked to avoid copying.
    The original files are never modified. Returns the cleaned FLAC paths."""
    cleaned: list[str] = []
    for f in locate(source_dir, ext_matcher(".flac")):
        rel = os.path.relpath(f, source_dir)
        out_path = os.path.join(dest_dir, rel)
        if flac_has_id3(f):
            _encode_to_flac(f, out_path)
            cleaned.append(out_path)
        else:
            _stage_link(f, out_path)

    _copy_sidecars(source_dir, dest_dir, link=True)
    return cleaned


def is_24bit(flac_dir: str) -> bool:
    """
    Returns True if any FLAC within flac_dir is 24 bit.
    """
    flacs = (
        mutagen.flac.FLAC(flac_file)
        for flac_file in locate(flac_dir, ext_matcher(".flac"))
    )
    return any(flac.info.bits_per_sample > 16 for flac in flacs)


def is_multichannel(flac_dir: str) -> bool:
    """
    Returns True if any FLAC within flac_dir is multichannel.
    """
    flacs = (
        mutagen.flac.FLAC(flac_file)
        for flac_file in locate(flac_dir, ext_matcher(".flac"))
    )
    return any(flac.info.channels > 2 for flac in flacs)


def needs_resampling(flac_dir: str):
    """
    Returns True if any FLAC within flac_dir needs resampling when
    transcoded.
    """
    return is_24bit(flac_dir)


def resample_rate(flac_dir: str) -> int | None:
    """
    Returns the rate to which the release should be resampled.
    """
    flacs = (
        mutagen.flac.FLAC(flac_file)
        for flac_file in locate(flac_dir, ext_matcher(".flac"))
    )
    original_rate: int = max(flac.info.sample_rate for flac in flacs) # type: ignore
    if original_rate % 44100 == 0:
        return 44100
    elif original_rate % 48000 == 0:
        return 48000
    else:
        return None


def transcode_commands(
    output_format: Format,
    resample: bool,
    needed_sample_rate: Optional[int],
    flac_file: str,
    transcode_file: str,
):
    """
    Return a list of transcode steps (one command per list element),
    which can be used to create a transcode pipeline for flac_file ->
    transcode_file using the specified output_format, plus any
    resampling, if needed.
    """
    if resample:
        flac_decoder = "sox {FLAC} -G -b 16 -t wav - rate -v -L {SAMPLERATE} dither"
    else:
        flac_decoder = "flac -dcs -- {FLAC}"

    transcoding_steps = [flac_decoder]

    encoder = output_format.encoder
    if encoder is None:
        raise TranscodeException(f'Missing encoder data for format {output_format.long_name}')

    match encoder.enc:
        case "lame":
            lame_encoder = "lame -S {OPTS} - {FILE}"
            transcoding_steps.append(lame_encoder)
        case "flac":
            flac_encoder = "flac {OPTS} -o {FILE} -"
            transcoding_steps.append(flac_encoder)
        case _:
            raise TranscodeException(f"Encoder out of valid range: {encoder}")

    transcode_args: dict[str, str | int | None] = {
        "FLAC": shlex.quote(flac_file),
        "FILE": shlex.quote(transcode_file),
        "OPTS": encoder.opts,
        "SAMPLERATE": needed_sample_rate,
    }

    if output_format.name == "FLAC" and resample:
        commands = [
            "sox {FLAC} -G -b 16 {FILE} rate -v -L {SAMPLERATE} dither".format(
                **transcode_args
            )
        ]
    else:
        commands = map(lambda cmd: cmd.format(**transcode_args), transcoding_steps)

    return commands

def transcode(flac_file: str, output_dir: str, output_format: Format) -> str:
    """
    Transcodes a FLAC file into another format.
    """
    # gather metadata from the flac file
    flac_info = mutagen.flac.FLAC(flac_file)
    sample_rate: int = flac_info.info.sample_rate # type: ignore
    bits_per_sample: int = flac_info.info.bits_per_sample # type: ignore
    resample: bool = sample_rate > 48000 or bits_per_sample > 16 # type: ignore

    # if resampling isn't needed then needed_sample_rate will not be used.
    needed_sample_rate = None

    if resample:
        if sample_rate % 44100 == 0:
            needed_sample_rate = 44100
        elif sample_rate % 48000 == 0:
            needed_sample_rate = 48000
        else:
            raise UnknownSampleRateException(
                'FLAC file "{0}" has a sample rate {1}, which is not 88.2, 176.4, 96, or 192kHz but needs resampling, this is unsupported'.format(flac_file, sample_rate)
            )

    if flac_info.info.channels > 2:
        raise TranscodeDownmixException(
            'FLAC file "{0}" has more than 2 channels, unsupported'.format(flac_file)
        )

    # determine the new filename
    transcode_basename = path.splitext(os.path.basename(flac_file))[0]
    transcode_basename = re.sub(r'[\?<>\\*\|":]', "_", transcode_basename)
    transcode_file = path.join(output_dir, transcode_basename)


    if output_format.encoder is not None:
        transcode_file += output_format.encoder.ext
    else:
        raise TranscodeException(f'Missing encoder data for format {output_format.long_name}')

    if not os.path.exists(path.dirname(transcode_file)):
        try:
            os.makedirs(path.dirname(transcode_file))
        except OSError as e:
            if e.errno == errno.EEXIST:
                # Harmless race condition -- another transcode process
                # beat us here.
                pass
            else:
                raise e

    commands = list(
        transcode_commands(
            output_format, resample, needed_sample_rate, flac_file, transcode_file
        )
    )
    results = run_pipeline(commands)

    # Check for problems. Because it's a pipeline, the earliest one is
    # usually the source. The exception is -SIGPIPE, which is caused
    # by "backpressure" due to a later command failing: ignore those
    # unless no other problem is found.
    last_sigpipe = None
    for cmd, (code, stderr) in zip(commands, results):
        if code:
            if code == -signal.SIGPIPE:
                last_sigpipe = (cmd, (code, stderr))
            else:
                raise TranscodeException(
                    'Transcode of file "{0}" failed: {1}'.format(flac_file, stderr)
                )
    if last_sigpipe:
        # XXX: this should probably never happen....
        raise TranscodeException(
            'Transcode of file "{0}" failed: SIGPIPE'.format(flac_file)
        )

    tagging.copy_tags(flac_file, transcode_file)
    (ok, msg) = tagging.check_tags(transcode_file)
    if not ok:
        raise TranscodeException("Tag check failed on transcoded file: {0}".format(msg))

    return transcode_file


def get_transcode_dir(flac_dir: str, output_dir: str, output_format: str, resample: bool) -> str:
    full_flac_dir = flac_dir
    transcode_dir = path.basename(flac_dir)
    flac_dir = transcode_dir

    def some_check(string: str):
        return string in flac_dir.upper() and (
            (flac_dir.upper().count("24") >= 2)
            or (not any(s in flac_dir for s in some_numbers))
        )

    def replace_insensitive(pattern: str, replacement: str, source: str):
        return re.sub(re.compile(pattern, re.I), replacement, source)

    # This is what happens when you spend your time transcoding 24 bit to 16 for
    # perfect FLACs.
    some_numbers = ("44", "88", "176", "48", "96", "192")
    list_of_flac = ["FLAC", "FLAC HD", "HD FLAC"]
    list_of_24_flac = [
        "FLAC 24-BIT",
        "FLAC-24BIT",
        "FLAC-24",
        "FLAC 24BIT",
        "FLAC 24 BIT",
        "FLAC, 24BIT",
        "FLAC, 24 BIT",
        "FLAC, 24-BIT",
        "FLAC 24",
        "FLAC24",
        "FLAC96",
        "24-BIT FLAC",
        "24-BIT LOSSLESS FLAC",
        "24BIT FLAC",
        "24 BIT FLAC",
        "24FLAC",
        "24 FLAC",
        "24 BITS",
        "24-BITS",
        "24BITS",
        "24BIT",
        "24 BIT",
        "24-BIT",
    ]

    for flac in list_of_flac:
        if flac in flac_dir.upper():
            transcode_dir = replace_insensitive(flac, output_format, transcode_dir)
            break

    for flac in list_of_24_flac:
        if some_check(flac):
            transcode_dir = replace_insensitive(flac, output_format, transcode_dir)
            break

    transcode_dir = f"{transcode_dir}({output_format})"
    if output_format != "FLAC":
        transcode_dir = replace_insensitive("FLAC", "", transcode_dir)

    if resample:
        rate = resample_rate(full_flac_dir)
        if rate == 44100:
            if "24" in flac_dir and "176.4" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("176.4", "44")
            elif "24" in flac_dir and "176 4" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("176 4", "44")
            elif "24" in flac_dir and "176" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("176", "44")
            elif "24" in flac_dir and "88.2" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("88.2", "44")
            elif "24" in flac_dir and "88 2" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("88 2", "44")
            elif "24" in flac_dir and "88" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("88", "44")
            elif "24" in flac_dir and "44.1" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("44.1", "44")
            elif "24" in flac_dir and "44 1" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("44 1", "44")
            elif "24" in flac_dir and "44" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
            else:
                transcode_dir += " [16-44]"
        elif rate == 48000:
            if "24" in flac_dir and "192" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("192", "48")
            elif "24" in flac_dir and "96" in flac_dir:
                # XXX: theoretically, this could replace part of the album title too.
                # e.g. "24 days in 96 castles - [24-96]" would become "16 days in 44 castles - [16-44]"
                transcode_dir = transcode_dir.replace("24", "16")
                transcode_dir = transcode_dir.replace("96", "48")
            elif "24" in flac_dir and "48" in flac_dir:
                transcode_dir = transcode_dir.replace("24", "16")
            else:
                transcode_dir += " [16-48]"

    return os.path.join(output_dir, transcode_dir)


def transcode_release(
    flac_dir: str,
    output_dir: str,
    output_format: Format,
    source_torrent: Torrent,
    source_torrent_group: TorrentGroup,
    edition: str | None = None,
):
    """
    Transcode a FLAC release into another format.
    """
    flac_dir = os.path.abspath(flac_dir)
    output_dir = os.path.abspath(output_dir)
    flac_files = list(locate(flac_dir, ext_matcher(".flac")))

    # Fail loudly on a source with no FLACs rather than silently producing an
    # empty "successful" transcode (e.g. a folder that actually holds .m4a).
    if not flac_files:
        raise TranscodeException(
            f'No FLAC files found in source directory "{flac_dir}"'
        )

    # check if we need to resample
    resample = needs_resampling(flac_dir)

    # check if we need to encode
    if output_format.name == 'FLAC' and not resample:
        # XXX: if output_dir is not the same as flac_dir, this may not
        # do what the user expects.
        if output_dir != os.path.dirname(flac_dir):
            logging.info(
                "Warning: no encode necessary, so files won't be placed in", output_dir
            )
        return flac_dir

    # make a new directory for the transcoded files
    #
    # NB: The cleanup code that follows this block assumes that
    # transcode_dir is a new directory created exclusively for this
    # transcode. Do not change this assumption without considering the
    # consequences!
    transcode_dir = os.path.join(output_dir, source_torrent_group.get_transcode_dirname(source_torrent, output_format, edition))
    logging.info("    " + transcode_dir)
    if not os.path.exists(transcode_dir):
        os.makedirs(transcode_dir)
    else:
        # Do NOT silently reuse a pre-existing directory. Different editions in
        # the same group can resolve to the same folder name (e.g. two versions
        # of a soundtrack, both bare "{CD}"); reusing it would package the wrong
        # edition's audio into this upload. Fail loudly instead -- pass a unique
        # --edition label to disambiguate.
        raise TranscodeException(
            f'Transcode output directory already exists: "{transcode_dir}". '
            f'This usually means another edition in the same group produced the '
            f'same folder name -- rerun with a unique --edition label, or remove '
            f'the stale directory.'
        )

    try:
        arg_list = [
            (
                filename,
                path.dirname(filename).replace(flac_dir, transcode_dir),
                output_format,
            )
            for filename in flac_files
        ]
        for filename, output_dir, output_format in arg_list:
            transcode(filename, output_dir, output_format)
            try:
                print_filename = filename.rsplit("/",1)[1]
            except ValueError:
                print_filename = filename
            LOGGER.info(f"      Processing: {print_filename}")

        # copy other files
        allowed_extensions = [
            ".cue", ".gif", ".jpeg", ".jpg",
            ".log", ".md5", ".nfo", ".pdf",
            ".png", ".sfv", ".txt",
        ]
        allowed_files = locate(flac_dir, ext_matcher(*allowed_extensions))
        for filename in allowed_files:
            new_dir = os.path.dirname(filename).replace(flac_dir, transcode_dir)
            if not os.path.exists(new_dir):
                os.makedirs(new_dir)
            shutil.copy(filename, new_dir)

        return transcode_dir

    except:
        # Cleanup.
        #
        # ASSERT: transcode_dir was created by this function and does
        # not contain anything other than the transcoded files!
        shutil.rmtree(transcode_dir)
        raise


# Runs on the server. Paths arrive on stdin as JSON so the remote shell never
# parses combining characters, spaces, or braces. mktorrent is invoked with an
# argument list, and its temp file is removed before the process exits.
_REMOTE_SCRIPT = r"""
import json
import os
import subprocess
import sys
import tempfile

def fail(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.exit(1)

try:
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
except Exception as exc:
    fail("bad request: %s" % exc)

op = req.get("op")
try:
    if op == "listdir":
        names = os.listdir(req["path"])
        sys.stdout.buffer.write(json.dumps(names, ensure_ascii=False).encode("utf-8"))
    elif op == "cat":
        with open(req["path"], "rb") as fh:
            sys.stdout.buffer.write(fh.read())
    elif op == "mktorrent":
        # mktorrent will not overwrite an existing file, so the output path
        # must not exist yet. The directory is removed before this process exits.
        tmpdir = tempfile.mkdtemp(prefix="omb_")
        tmp = os.path.join(tmpdir, "release.torrent")
        payload = b""
        try:
            command = ["mktorrent", "-p", "-a", req["announce"], "-o", tmp, req["directory"]]
            if req.get("source") is not None:
                command = [
                    "mktorrent", "-p", "-s", req["source"],
                    "-a", req["announce"], "-o", tmp, req["directory"],
                ]
            try:
                proc = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except FileNotFoundError:
                fail("mktorrent was not found on the server")
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace").strip()
                fail(err or "mktorrent failed")
            with open(tmp, "rb") as fh:
                payload = fh.read()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
        sys.stdout.buffer.write(payload)
    else:
        fail("unknown op")
except FileNotFoundError as exc:
    fail(str(exc))
except OSError as exc:
    fail(str(exc))
"""


def ssh_exec(host: str, request: bytes) -> bytes:
    """Run the remote helper on ``host``. ``request`` is the JSON payload on stdin."""
    remote = "python3 -c " + shlex.quote(_REMOTE_SCRIPT)
    try:
        completed = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host, remote],
            input=request,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        raise TranscodeException(
            "ssh is required to read files from the remote share but was not found"
        )
    if completed.returncode != 0:
        err = completed.stderr.decode("utf-8", "replace").strip()
        detail = err or "exit {0}".format(completed.returncode)
        raise TranscodeException('Remote command on {0} failed: {1}'.format(host, detail))
    return completed.stdout


def _remote_request(host: str, payload: dict) -> bytes:
    return ssh_exec(host, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def remote_listdir(host: str, remote_dir: str) -> list[str]:
    """Return the server's exact directory entries for ``remote_dir``."""
    raw = _remote_request(host, {"op": "listdir", "path": remote_dir})
    try:
        names = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TranscodeException(
            'Unexpected directory listing from {0}: {1}'.format(host, exc)
        )
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise TranscodeException('Unexpected directory listing from {0}'.format(host))
    return names


def resolve_remote_path(share: RemoteShare, local_path: str) -> str:
    """Return the on-server path for ``local_path``, spelling each component as stored."""
    mapped = share.remote_for(local_path)
    if mapped is None:
        raise TranscodeException(
            '"{0}" is outside the remote_path prefix {1}'.format(local_path, share.local_prefix)
        )
    if mapped == share.remote_prefix:
        return mapped
    rel = mapped[len(share.remote_prefix):].lstrip("/")
    current = share.remote_prefix
    for part in rel.split("/"):
        names = remote_listdir(share.host, current)
        if part in names:
            chosen = part
        else:
            target = match_key(part)
            matches = [name for name in names if match_key(name) == target]
            if len(matches) == 1:
                chosen = matches[0]
            elif len(matches) > 1:
                raise TranscodeException(
                    'Multiple remote names match "{0}" in "{1}"'.format(part, current)
                )
            else:
                raise TranscodeException(
                    'No remote file matches "{0}" in "{1}"'.format(part, current)
                )
        current = current + "/" + chosen
    return current


def remote_cat(host: str, remote_path: str, dest_path: str) -> None:
    """Copy ``remote_path`` onto ``dest_path``. Does not touch the remote file."""
    data = _remote_request(host, {"op": "cat", "path": remote_path})
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(data)


def file_readable(path: str) -> bool:
    """Return True when ``path`` can be opened and read through the local mount."""
    try:
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError:
        return False
    return True


def _iter_album_files(source_dir: str):
    for dirpath, dirnames, filenames in os.walk(source_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if filename.startswith("."):
                continue
            yield os.path.join(dirpath, filename)


def stage_unreadable(source_dir: str, dest_dir: str, share: RemoteShare | None) -> bool:
    """Stage ``source_dir`` into ``dest_dir`` when any file cannot be opened locally.

    Readable files are symlinked. Unreadable files are copied over SSH using the
    server's stored name. Nothing under ``source_dir`` is renamed or rewritten.
    Returns True when a copy was required. When every file opens, ``dest_dir``
    is left untouched and False is returned.
    """
    source_dir = os.path.abspath(source_dir)
    files = list(_iter_album_files(source_dir))
    unreadable = {src for src in files if not file_readable(src)}
    if not unreadable:
        return False
    if share is None:
        extra = len(unreadable) - 1
        message = 'Cannot open "{0}"'.format(next(iter(unreadable)))
        if extra:
            message += " and {0} more".format(extra)
        message += (
            ". Set remote_host and remote_path to copy them over SSH "
            "without changing the seeded files."
        )
        raise TranscodeException(message)

    LOGGER.info(
        "Copying %s file(s) over SSH from %s; seeded files stay unchanged",
        len(unreadable),
        share.host,
    )
    for src in files:
        rel = os.path.relpath(src, source_dir)
        dst = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if src in unreadable:
            remote_cat(share.host, resolve_remote_path(share, src), dst)
        else:
            _stage_link(src, dst)
    return True


def _remote_mktorrent(share: RemoteShare, remote_dir: str, announce: str, source: str | None) -> bytes:
    # The announce URL contains the passkey, so it stays in the request body
    # and out of the log.
    LOGGER.info("Creating torrent on %s from server filenames", share.host)
    return _remote_request(share.host, {
        "op": "mktorrent",
        "directory": remote_dir,
        "announce": announce,
        "source": source,
    })


def make_torrent(
    input_dir: str,
    output_dir: str,
    tracker: str,
    passkey: str,
    source: str | None,
    share: RemoteShare | None = None,
) -> str:
    torrent = os.path.join(output_dir, path.basename(input_dir)) + ".torrent"
    if not path.exists(path.dirname(torrent)):
        os.makedirs(path.dirname(torrent))
    tracker_url = f"{tracker}{passkey}/announce"
    if share is not None and share.remote_for(input_dir) is not None:
        remote_dir = resolve_remote_path(share, input_dir)
        data = _remote_mktorrent(share, remote_dir, tracker_url, source)
        with open(torrent, "wb") as fh:
            fh.write(data)
        return torrent
    if source == None:
        command = ["mktorrent", "-p", "-a", tracker_url, "-o", torrent, input_dir]
    else:
        command = ["mktorrent", "-p", "-s", source, "-a", tracker_url, "-o", torrent, input_dir]
    subprocess.check_output(command, stderr=subprocess.STDOUT)
    return torrent