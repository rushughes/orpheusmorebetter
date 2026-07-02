Introduction
------------

`orpheusmorebetter` is a script designed to automatically upload missing album transcodes to Orpheus.

This software is able to scan through every FLAC you, the user, have ever downloaded or uploaded, determine which formats are missing, transcode the source FLAC to those formats, and upload the resulting files to Orpheus -- automatically.

Installation
------------

`orpheusmorebetter` was designed to work with 3.10+, but is best run on the newest version of Python 3 available. Downloads thereof can be found at https://www.python.org/downloads/.

To install all required Python packages, `cd` into the `orpheusmorebetter` directory and execute:

```bash
pip install -r requirements.txt
```

If you are on a seedbox or a system without access to superuser, try the following:

```bash
pip install --user -r requirements.txt
```

In addition to these Python packages, you will need to install several external dependencies: `mktorrent` 1.1+, `flac`, `lame`, and `sox`. `ffmpeg` is also recommended -- it is used to auto-remux non-FLAC lossless sources (see below), but is only required if you use that feature. The method of installing these programs varies depending on your operating system.

```bash
# For Ubuntu-based systems
apt install mktorrent flac lame sox ffmpeg
# For Arch-based systems
sudo pacman -S mktorrent flac lame sox ffmpeg
```

If you are on a seedbox and you lack the privilages to install packages, you could contact your provider to have these packages installed.

At this point you may execute the following command:

    $ orpheusmorebetter

The package will generate a configuration file located at `~/.orpheusmorebetter/config`, which should be edited immediately upon creation.

Configuration
-------------

Open the file `~/.orpheusmorebetter/config` in a text editor. You will see something like this:

```ini
[orpheus]
username =
password =
data_dir =
output_dir =
torrent_dir =
formats = flac, v0, 320
media = sacd, soundboard, web, dvd, cd, dat, vinyl, blu-ray
24bit_behaviour = 0
tracker = https://home.opsfet.ch/
api = https://orpheus.network/
mode = both
source = OPS
```

If you have used `orpheusbetter`, `whatbetter`, or `redbetter`, this is an extremely similar config format.

`username` and `password` are your Orpheus login credentials.

`data_dir` is the directory where your source FLACs are stored.

`output_dir` is the directory where your transcodes will be stored after creation. If the value is blank, `data_dir` will be used. You may also specify per format values such as `output_dir_320` or `output_dir_v0`, and `orpheusmorebetter` will redirect the outputs to the associated directory.

`torrent_dir` is the directory where the torrents associated with your transcodes will be created (i.e., your watch directory). Same per format settings as `output_dir` apply.

`formats` is a list of formats that you'd like to support. (If you don't want to upload V2, or any other specific format, just remove it from this list)

`media` is a list of lossless media types you want to consider for transcoding. The default value is all What.CD lossless formats, but if you want to transcode only CD and vinyl media, for example, you would set this to `cd, vinyl`.

`24bit_behaviour` defines what happens when the program encounters a FLAC that it thinks is 24 bits. Options:
 - `2` - Every FLAC that has a bits-per-sample property of 24 will be silently re-categorized. 
 - `1` - A prompt will appear. 
 - `0` - Ignore hese occurrences entirely. *This is the default.*

`tracker` is the base announce url to use in the torrent files.

`api` is the base url to use for API requests.

`mode` selects which list of torrents `orpheusmorebetter` will use to search for candidates. One of:

 - `snatched` - Your snatched torrents.
 - `uploaded` - Your uploaded torrents.
 - `both`     - Your uploaded and snatched torrents.
 - `seeding`  - Better.php for your seeding torrents.
 - `all`      - All transcode sources above.
 - `none`     - Disable scraping.

 `source` is the source flag to add to created torrents. Leave blank if you are running `mktorrent` 1.0.

You should end up with something like this:

```
[orpheus]
username = RequestBunny
password = clapton
data_dir = /srv/downloads
output_dir =
torrent_dir = /srv/torrents
formats = flac, v0, 320
media = cd, vinyl, web
24bit_behaviour = 0
tracker = https://home.opsfet.ch/
api = https://orpheus.network
mode = both
source = OPS
```

Usage
-----

```bash
usage: orpheusmorebetter [-h] [-s] [-j THREADS] [--config CONFIG] [--cache CACHE]
                     [-U] [-E] [--version] [-m MODE] [-S] [-t TOTP]
                     [-o SOURCE]
                     [release_urls [release_urls ...]]

positional arguments:
  release_urls          the URL where the release is located (default: None)

optional arguments:
  -h, --help            show this help message and exit
  -s, --single          only add one format per release (useful for getting
                        unique groups) (default: False)
  -j THREADS, --threads THREADS
                        number of threads to use when transcoding (default: 7)
  --config CONFIG       the location of the configuration file (default:
                        ~/.orpheusmorebetter/config)
  --cache CACHE         the location of the cache (default:
                        ~/.orpheusmorebetter/cache)
  -U, --no-upload       don't upload new torrents (in case you want to do it
                        manually) (default: False)
  -E, --no-24bit-edit   don't try to edit 24-bit torrents mistakenly labeled
                        as 16-bit (default: False)
  --version             show program's version number and exit
  -m MODE, --mode MODE  mode to search for transcode candidates; snatched,
                        uploaded, both, seeding, or all (default: None)
  -S, --skip            treats a torrent as already processed (default: False)
  -t TOTP, --totp TOTP  time based one time password for 2FA (default: None)
  -o SOURCE, --source SOURCE
                        the value to put in the source flag in created
                        torrents (default: None)
```

Examples
--------

To transcode and upload every snatch you've ever downloaded along with all your uploads (this may take a while):

```bash
orpheusmorebetter
```

To transcode and upload a specific release (provided you have already downloaded the FLAC and it is located in your `data_dir`):

```bash
orpheusmorebetter https://orpheus.network/torrents.php?id=1000\&torrentid=1000000
```

Note that if you specify a particular release(s), orpheusmorebetter will ignore your configuration's media types and attempt to transcode the releases you have specified regardless of their media type. (so long as they are lossless types)

Your first time running orpheusmorebetter might take a while, but after it has successfully checked all eligible files it will go faster upon each consecutive run, as outputs are cached.

Non-FLAC lossless sources (auto-remux)
--------------------------------------

Some lossless releases ship their audio in a non-FLAC container -- for example FLAC or ALAC inside `.m4a`. Since orpheusmorebetter transcodes from `.flac`, it would otherwise skip these with "No FLAC files found".

When the source directory has no `.flac` files, orpheusmorebetter now probes the other files with `ffmpeg`/`ffprobe` and, if it finds **lossless** audio (FLAC or ALAC) in a container like `.m4a`, it losslessly remuxes them to FLAC in a temporary directory, transcodes from there, and deletes the temporary FLACs afterward. Cover art and other sidecar files are carried along. Lossy sources (e.g. AAC) are never remuxed.

This requires `ffmpeg`. Pass `--no-remux` to disable the behavior and keep the original "no FLAC files" skip.

Disambiguating editions (`--edition`)
-------------------------------------

Two different torrents in the same group (e.g. a standard soundtrack and an "Expanded" edition) can produce the **same** transcode folder name when neither carries a distinct edition/remaster title — both end up as `… {CD} [MP3 320]`. To keep them apart, pass `--edition` with a meaningful label:

```bash
orpheusmorebetter --edition "Expanded 2CD" "https://orpheus.network/torrents.php?id=92430\&torrentid=1966628"
# -> …Quantum of Solace {CD ~ Expanded 2CD} [MP3 320]
```

The label is injected into the edition segment and overrides the release's own remaster title in the folder name. If two editions would still collide (e.g. you forget the label), orpheusmorebetter now **stops with an error** rather than silently packaging the other edition's audio into your upload.

Listing missing transcodes (`orpheusmissing`)
----------------------------------------------

`orpheusmissing` is a read-only companion command. It scans your Orpheus history and reports which FLAC releases are **missing** an MP3 V0 and/or 320 transcode — without transcoding or uploading anything. Use it to see your backlog before committing to a (potentially long) transcode run.

It shares the same configuration file as `orpheusmorebetter` (`~/.orpheusmorebetter/config`), so no extra setup is needed. It only reads from the API — it does not touch your local FLACs, `data_dir`, or any external tools (`lame`, `sox`, etc.).

```bash
usage: orpheusmissing [-h] [--config CONFIG] [--cache CACHE] [--no-cache]
                      [-m MODE] [--formats FORMATS] [--media MEDIA]
                      [-o OUTPUT] [--format-out {text,urls,csv}] [-t TOTP]
                      [--version] [-d]
```

Key options:

 - `-m, --mode` — which history to scan: `snatched` (default; things you've downloaded), `uploaded`, `both`, `seeding`, or `all`.
 - `--format-out` — `text` (default, human-readable), `csv`, or `urls` (one release URL per line).
 - `-o, --output` — write the report to a file instead of stdout (progress still goes to stderr).
 - `--formats` / `--media` — override the config values for this run.
 - `--cache` — location of the report cache. This is a **separate** cache from the transcode tool's (default `~/.orpheusmorebetter/report_cache`), so listing releases never marks them as "processed" for `orpheusmorebetter`. Use `--no-cache` to re-check everything.

The `urls` output format is designed to feed straight back into `orpheusmorebetter`, which accepts a file of release URLs:

```bash
# 1. Discover everything you've snatched that's missing a V0/320
orpheusmissing -m snatched --format-out urls -o backlog.txt

# 2. Transcode and upload the whole backlog
orpheusmorebetter backlog.txt
```

Note: `orpheusmissing` is purely API-based, so it cannot detect 24-bit or multichannel releases (those checks need the local FLAC). A small number of releases in the URL list may therefore be skipped by `orpheusmorebetter` when it inspects the actual files — this is expected.
