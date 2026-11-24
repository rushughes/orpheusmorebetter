# Spec: `list-missing` — report downloaded FLACs missing MP3 V0/320

> Status: draft / investigation. No code written yet.
> Author: (spec generated from codebase investigation, 2026-07-01)

## 1. Goal

Add a **new, standalone command** that scans the user's Orpheus history
(uploaded and/or snatched torrents) and produces a **report of every FLAC
release that is missing an MP3 V0 and/or MP3 320 transcode**, without
transcoding or uploading anything.

The output should be:
1. Human-readable to eyeball what's transcodable, **and**
2. Machine-consumable as a URL list that can be fed straight back into the
   existing `orpheusmorebetter` tool to actually do the transcodes.

This is effectively a **dry-run / discovery** counterpart to the existing
transcode-and-upload flow.

## 2. How the existing system works (as-built)

### 2.1 Entry point
- Single executable script: [`orpheusmorebetter`](../orpheusmorebetter) (a Python
  script, not a package module). `main()` handles arg parsing, config, login,
  candidate discovery, transcode, and upload.
- Wired via [`pyproject.toml`](../pyproject.toml) `[project.scripts]`:
  `orpheusmorebetter = "orpheusmorebetter:main"`.
- Supporting code split into two packages:
  - `services/` — `whatapi.py` (Orpheus API + HTML scraping), `transcode.py`,
    `tagging.py`.
  - `models/` — `Torrent`, `TorrentGroup`, `Format`, `Artist`, `Encoder`.

### 2.2 Candidate discovery (the part we reuse)
`WhatAPI` in [`services/whatapi.py`](../services/whatapi.py):

- `__init__` logs in, then calls the `index` ajax action to capture
  `self.user_id`, `self.authkey`, `self.passkey`.
- `crawl_torrents_php(type, media_params, skip)` — **this is the key existing
  function.** It already does exactly the paginated crawl the new feature
  needs:
  - Builds `torrents.php?type={type}&userid={self.user_id}&format=FLAC`.
  - Loops `&page=N` until the next page link is absent
    (`done = "page={N+1}" not in content`).
  - Parses each `torrent_row`, extracts `(group_id, torrent_id)` from
    `torrents.php?id=..&torrentid=..` hrefs via regex.
  - Skips any `torrent_id` already in the `skip` set.
  - `type` is one of `"snatched"` (things you downloaded) or `"uploaded"`
    (things you uploaded).
- `get_candidates(mode, skip, media)` fans `crawl_torrents_php` out over modes:
  - `snatched` / `uploaded` / `both` / `all` / `seeding`.
  - `both` = snatched + uploaded. `seeding` uses `better.php`.
  - Yields `(group_id, torrent_id)` tuples. Only FLAC sources
    (hardcoded `&format=FLAC` in the URL).
- Rate limiting: at most 4 requests in any 10 seconds, and at most 10
  `torrentgroup` calls in any 60 seconds. A `Rate limit exceeded` response
  waits out that window and retries the same call.

### 2.3 "What formats are missing" logic (the other part we reuse)
In [`orpheusmorebetter`](../orpheusmorebetter) `formats_needed(group, torrent, requested_formats)`:

- Finds all torrents in the same group that are the **same edition**
  (same media + remaster year/title/label/catalogue number).
- `current_formats` = the `Format(name, encoding)` set already present for that
  edition.
- `needed = torrent.allowed_transcodes − current_formats`, then
  `∩ requested_formats`.
- `torrent.allowed_transcodes` (in [`models/torrent.py`](../models/torrent.py)) is
  `perfect_three = {FLAC, MP3_320, MP3_V0}`, unless the release is
  pre-emphasized (then `set()` — never transcode).
- `Format.__eq__`/`__hash__` compare only `(name, encoding)`, so set math works
  cleanly.

So for a FLAC torrent, `formats_needed` already returns exactly
"which of {320, V0} are missing for this edition" — which is the whole point of
the new command. **We can reuse it verbatim.**

### 2.4 Per-candidate data fetch
The main loop calls `api.request_ajax("torrentgroup", id=groupid)` and coerces
the response into a `TorrentGroup` (with nested `torrents` list). That single
call gives every sibling torrent in the group, which is all
`formats_needed` needs.

### 2.5 The transcode/upload path (the part we DON'T reuse)
Everything after `formats_needed` in the main loop — locating the local FLAC in
`data_dir`, 24-bit handling, multichannel check, `transcode_release`,
`make_torrent`, `api.upload`, cache write — is **out of scope** for the new
command. The report never needs local files, `sox`, `lame`, `flac`, or
`mktorrent`.

### 2.6 Important integration point: URL-list input
The existing tool already accepts a **file of release URLs** as its positional
arg (`orpheusmorebetter` lines ~311–319): if given a single path that is a
file, it reads one URL per line and parses `id` + `torrentid` query params.
This is the clean hand-off: the new command can emit such a file, and the user
runs `orpheusmorebetter that-file.txt` to transcode the backlog.

## 3. Proposed design

### 3.1 Command shape
Add a **second console entry point** so the working tool is untouched:

- New script/module: `orpheusmissing` (name TBD — alt: `orpheus-list-missing`).
- New `[project.scripts]` entry: `orpheusmissing = "orpheusmissing:main"`.
- Shares config, `services/`, and `models/` — **no changes to the existing
  `orpheusmorebetter` script.**

### 3.2 New library module
Add `services/report.py` (new file, no edits to existing services) exposing a
pure "find missing" routine, e.g.:

```
find_missing(api, mode, media, requested_formats, skip=None) -> Iterator[MissingItem]
```

where `MissingItem` is a small dataclass:
`{ group_id, torrent_id, artist, group_name, year, media, edition, missing: set[Format], permalink }`.

Internally it:
1. Iterates `api.get_candidates(mode, skip, media)` (reuse as-is).
2. For each `(groupid, torrentid)`, fetches the group via
   `request_ajax("torrentgroup", ...)` and builds a `TorrentGroup`
   (factor the group-construction boilerplate out of the main script into a
   shared helper — see §5, optional).
3. Computes `formats_needed(group, torrent, requested_formats)`.
4. If non-empty, yields a `MissingItem`.

Keeping this as a generator means the CLI can stream results (and the 5s rate
limit makes streaming output important for UX on large histories).

### 3.3 CLI behavior
```
orpheusmissing [--config PATH]
               [-m MODE] [--media ...] [--formats v0,320]
               [-o OUTPUT] [--format-out {text,csv,urls}]
               [--refresh] [-t TOTP]
```
- Reuses the existing config file (`~/.orpheusmorebetter/config`) for
  credentials, api endpoint, `media`, `formats`, `mode`.
- Default `mode`: **`snatched`** for this command (your "download history"),
  even though the config default is `both`. Overridable with `-m`. (Decided, §6.)
- `--format-out`:
  - `text` (default): grouped, readable console/file report.
  - `csv`: for spreadsheets.
  - `urls`: one `https://orpheus.network/torrents.php?id=G&torrentid=T` per line
    — directly consumable by `orpheusmorebetter <file>`.
- `-o/--output`: write report to a file instead of stdout.

### 3.4 Example run
```
$ orpheusmissing -m snatched --format-out urls -o backlog.txt
...crawls history, prints progress to stderr...
$ wc -l backlog.txt        # 137 releases missing V0/320
$ orpheusmorebetter backlog.txt   # transcode + upload the backlog
```

## 4. Data / API notes & edge cases

- **userid**: `crawl_torrents_php` hardcodes `self.user_id` (the logged-in
  user). Your example URL uses your own id (10918), so default = own user is
  correct. A `--userid` override is **dropped from v1** because it would require
  editing `crawl_torrents_php`, which violates the zero-edit constraint (§5).
  Can be revisited later.
- **snatched vs uploaded**: you pasted `type=uploaded` but called it "download
  history." In Orpheus/Gazelle terms, *downloaded* = **snatched**, which is the
  v1 default. `-m` overrides. (Decided, §6.)
- **FLAC-only**: crawl already filters `&format=FLAC`, so only lossless sources
  are considered — correct for "what can I transcode."
- **Pre-emphasized releases**: `allowed_transcodes` returns `set()`, so they
  are automatically excluded. Good.
- **Multichannel / 24-bit**: the report will NOT detect these, because it does
  not touch local files (those checks live in `transcode.is_multichannel` /
  `is_24bit` and need the FLAC on disk). So the URL list may contain a few
  releases the transcode step will later skip. Acceptable for a discovery tool;
  document it. **Decided: no local checks in v1** (purely API-based).
- **Rate limiting cost**: one `torrentgroup` ajax call per candidate, at most
  10 per minute. A 500-item history takes about an hour. Mitigations:
  - A separate SQLite file, path set by the `missing_cache` config key (blank
    disables it). Albums stored as **complete** for the current format set are
    skipped on later runs. Albums still **missing** a format are fetched again
    every run, and promoted to complete once the formats are present. This does
    not use orpheusmorebetter's pickle "seen" set. `--refresh` rechecks
    complete rows (a format removed from a complete release stays hidden until
    then). History pages are still crawled so new snatches are found.
  - Print progress/results as they stream in.
- **Local availability (optional column)**: could annotate each item with
  whether its FLAC exists in `data_dir` (so the user knows which are
  immediately transcodable vs. need re-download). Cheap, filesystem-only. Nice
  to have; propose as a `--check-local` flag.

## 5. Code placement (DECIDED — new code only, zero edits to working code)

**Hard constraint for v1: do not edit any existing file except to add the new
`pyproject.toml` entry point.** No changes to `orpheusmorebetter`, `whatapi.py`,
or the models. Everything new lives in `services/report.py` and the new
`orpheusmissing` script.

- **`get_torrent_group` helper → put it in `services/report.py`.** Do NOT add a
  method to `whatapi.py`. The report module owns its own local helper that takes
  a `WhatAPI` instance, calls `api.request_ajax("torrentgroup", id=groupid)`,
  and builds a `TorrentGroup` (duplicating the ~15 lines currently inline in the
  main loop, lines ~346–362). Duplication is accepted to keep the working file
  untouched.
- **`formats_needed` → copy it into `services/report.py`.** It's tiny and pure;
  copy it verbatim rather than importing from the extension-less
  `orpheusmorebetter` script. The two copies are allowed to diverge; a later
  cleanup pass may consolidate (e.g. onto a `TorrentGroup` method) but that is
  explicitly out of scope for v1.

## 6. Resolved decisions

All open questions are now decided:

1. **Default mode → `snatched`** (your "download history"). Overridable with
   `-m` (`snatched` / `uploaded` / `both` / `seeding` / `all`).
2. **Cache → `missing_cache` in the shared config**, blank by default. When
   set (typically on the shared media volume, e.g.
   `/Volumes/media/orpheus/orpheusmissing.sqlite`), complete torrents are
   skipped and missing torrents are rechecked. `--refresh` rechecks complete
   rows. The file is independent of the transcode tool's pickle cache.
3. **Primary output → human-readable text report** (default). CSV and `urls`
   remain available via `--format-out` for chaining/spreadsheets, but `text` is
   the default and the main deliverable.
4. **Purely API-based** — no local FLAC checks in v1. No `--check-local`, no
   `data_dir` dependency, no 24-bit/multichannel detection (those need local
   files). Documented caveat: the emitted URL list may contain a few releases
   the transcode step later skips.

## 7. Work breakdown (for the implementation pass — not done here)

1. `services/report.py`: `MissingItem` dataclass + `find_missing(...)` generator
   (+ local `get_torrent_group` helper + copied/adapted `formats_needed`).
2. `orpheusmissing` script: arg parsing (mirror config-loading from the existing
   script), login via `WhatAPI`, drive `find_missing`, render output
   (text/csv/urls), optional `-o` file + cache.
3. `pyproject.toml`: add the `orpheusmissing` entry point (the ONLY edit to an
   existing file in v1).
4. Tests: unit-test `formats_needed`/`find_missing` against fixture torrentgroup
   JSON (mirrors existing `tests/test_format.py` / `tests/test_transcode.py`
   style). No network in tests — mock `WhatAPI`.
5. README: document the new command and the `orpheusmissing → orpheusmorebetter`
   chaining workflow.

## 8. Explicitly out of scope
- Any change to transcode/upload logic.
- Editing the `orpheusmorebetter` script's behavior (v1 adds only new files +
  one `pyproject.toml` entry).
- Detecting 24-bit / multichannel (needs local FLACs).
```
