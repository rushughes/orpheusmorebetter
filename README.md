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
remote_host =
remote_path =
missing_cache =
```

If you have used `orpheusbetter`, `whatbetter`, or `redbetter`, this is an extremely similar config format.

`username` and `password` are your Orpheus login credentials.

`data_dir` is the directory where your source FLACs are stored.

`remote_host` and `remote_path` are optional. Set them when the files live on a Samba share mounted from a Linux server. `remote_host` is an SSH destination such as `rus@fileserver`. `remote_path` is `local_prefix=remote_prefix`, mapping the mount point to the directory on that server. Files the mount cannot open are copied over SSH into a temp directory, and the seeded originals are left unchanged. Torrents for transcodes written under that prefix are created on the server, so the names inside the torrent match the files on disk.

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

`missing_cache` is an optional SQLite file used by `orpheusmissing`. Leave it blank to rescan every album on every run. Set it to a path on the shared media volume so both machines skip albums already confirmed complete:

```
missing_cache = /Volumes/media/orpheus/orpheusmissing.sqlite
```

Albums that are still missing a requested format are looked up again each run. One machine should run `orpheusmissing` at a time; the database uses SQLite's rollback journal rather than WAL because the file lives on a network share.

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
remote_host =
remote_path =
missing_cache = /Volumes/media/orpheus/orpheusmissing.sqlite
```

Usage
-----

```bash
usage: orpheusmorebetter [-h] [-s] [-j THREADS] [--config CONFIG] [--cache CACHE]
                     [-U] [-E] [--version] [-m MODE] [-S] [-t TOTP]
                     [-o SOURCE]
                     [release_urls [release_urls ...]]

positional arguments:
  release_urls          a release URL, or an info hash, identifying the release
                        (default: None)

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

You don't need the full URL. If you only have the torrent id, a URL with just `torrentid` works too — the group id is looked up automatically:

```bash
orpheusmorebetter https://orpheus.network/torrents.php?torrentid=1000000
```

You can also identify a release by its info hash (40 hex characters), which is resolved against the API the same way:

```bash
orpheusmorebetter DEADBEEF1234567890ABCDEF1234567890ABCDEF
```

Any of these forms may be mixed together, passed multiple times, or listed one per line in a file given as the single argument.

Note that if you specify a particular release(s), orpheusmorebetter will ignore your configuration's media types and attempt to transcode the releases you have specified regardless of their media type. (so long as they are lossless types)

Your first time running orpheusmorebetter might take a while, but after it has successfully checked all eligible files it will go faster upon each consecutive run, as outputs are cached.

Non-FLAC lossless sources (auto-remux)
--------------------------------------

Some lossless releases ship their audio in a non-FLAC container -- for example FLAC or ALAC inside `.m4a`. Since orpheusmorebetter transcodes from `.flac`, it would otherwise skip these with "No FLAC files found".

When the source directory has no `.flac` files, orpheusmorebetter now probes the other files with `ffmpeg`/`ffprobe` and, if it finds **lossless** audio (FLAC or ALAC) in a container like `.m4a`, it losslessly remuxes them to FLAC in a temporary directory, transcodes from there, and deletes the temporary FLACs afterward. Cover art and other sidecar files are carried along. Lossy sources (e.g. AAC) are never remuxed.

Relatedly, some FLAC releases have non-standard **ID3 tags** bolted onto them (an `ID3` header and/or a trailing `TAG` block). These are invalid in FLAC and make the `flac` decoder fail with `LOST_SYNC`, even though the audio is fine. When orpheusmorebetter detects such files it stages a temporary, cleaned copy of the release (re-encoding the tainted FLACs losslessly with `ffmpeg` and symlinking the rest), transcodes from there, and cleans up afterward. The original seeded files are never modified.

Both behaviors require `ffmpeg`. Pass `--no-remux` to disable them.

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

`orpheusmissing` scans your Orpheus history for FLAC releases **missing** an MP3 V0 and/or 320 transcode, and runs `orpheusmorebetter` on each one as soon as it is found. The transcoder creates the files, builds the torrents, uploads them, and drops the torrent files in the watch directory, using the same config as a manual run. The history crawl continues after that release finishes.

It shares the configuration file (`~/.orpheusmorebetter/config`). The scan itself only reads the API. `orpheusmorebetter` then uses `data_dir`, `output_dir`, `torrent_dir`, and `remote_host` / `remote_path` exactly as it would if you passed it the URL list yourself.

```bash
usage: orpheusmissing [-h] [--config CONFIG]
                      [-m MODE] [--formats FORMATS] [--media MEDIA]
                      [-o OUTPUT] [--format-out {text,urls,csv}] [--refresh]
                      [--report-only] [-t TOTP] [--version] [-d]
```

Key options:

 - `-m, --mode` — which history to scan: `snatched` (default; things you've downloaded), `uploaded`, `both`, `seeding`, or `all`.
 - `--report-only` — list the backlog and stop, without transcoding or uploading. `--format-out` and `-o` apply to this list.
 - `--format-out` — with `--report-only`: `text` (default, human-readable), `csv`, or `urls` (one release URL per line).
 - `-o, --output` — with `--report-only`, write the report to a file instead of stdout (progress still goes to stderr).
 - `--formats` / `--media` — override the config values for this run.
 - `--refresh` — look up albums already marked complete and rewrite those cache rows. Use this when a format disappears from a release that was previously complete.

When `missing_cache` is set, albums confirmed complete for the current format set are not fetched again. Albums still missing a format are fetched on every run, and if the formats are now present the row is marked complete. A successful upload stays in the missing list until the next scan sees the new formats on the tracker. The history pages are still walked, so newly snatched releases are found. With the cache blank, every run fetches every album. The per-album lookup is rate-limited, so the first scan of a large history takes a while.

A plain run with nothing missing prints the empty report and does not start the transcoder. Each missing release starts its own `orpheusmorebetter` as soon as the scan finds it. A release that fails is logged, and the scan continues with the next one. `--report-only` is the manual version of that handoff:

```bash
# List the backlog without uploading
orpheusmissing --report-only -m snatched --format-out urls -o backlog.txt

# Same work the default command does for you
orpheusmorebetter backlog.txt
```

`orpheusmissing` cannot detect 24-bit or multichannel releases during the scan (those checks need the local FLAC). `orpheusmorebetter` still skips those when it opens the files.
