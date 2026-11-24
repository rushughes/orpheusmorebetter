#!/usr/bin/env python3
"""Discovery / reporting for FLAC releases missing MP3 V0 / 320 transcodes.

This module is deliberately self-contained: it does not modify or depend on the
transcode/upload path used by the main ``orpheusmorebetter`` script. It only
reads from the Orpheus API to answer "which FLACs in my history are missing a
V0/320 transcode?" -- no local files, no transcoding.

See docs/SPEC-missing-transcodes.md for the design rationale.
"""

import csv
import io
import logging
from dataclasses import dataclass
from typing import Callable, Iterator

from models import Torrent, TorrentGroup, Format

LOGGER = logging.getLogger("report")


# ---------------------------------------------------------------------------
# Helpers copied from the main script (kept local so we never edit working code)
# ---------------------------------------------------------------------------

def get_torrent_group(api, groupid: int) -> TorrentGroup:
    """Fetch a torrent group and coerce it into a ``TorrentGroup`` model.

    This duplicates the group-construction boilerplate from the main
    ``orpheusmorebetter`` loop on purpose, so the working script stays
    untouched (see spec section 5).
    """
    tg_d = api.request_ajax("torrentgroup", id=groupid)

    artist_info = tg_d["group"]["musicInfo"]
    group = TorrentGroup(
        **tg_d["group"],
        torrent=tg_d["torrents"],
        composers=artist_info["composers"],
        dj=artist_info["dj"],
        artists=artist_info["artists"],
        with_artists=artist_info["with"],
        conductor=artist_info["conductor"],
        remixed_by=artist_info["remixedBy"],
        producer=artist_info["producer"],
    )
    for t in group.torrent:
        t.groupId = group.id
    return group


def formats_needed(
    group: TorrentGroup, torrent: Torrent, requested_formats: set[Format]
) -> set[Format]:
    """Which of the requested formats are missing for this torrent's edition.

    Copied verbatim from the ``orpheusmorebetter`` script (spec section 5).
    """

    def same_edition(t: Torrent):
        return t.media == torrent.media \
            and t.remasterYear == torrent.remasterYear \
            and t.remasterTitle == torrent.remasterTitle \
            and t.remasterRecordLabel == torrent.remasterRecordLabel \
            and t.remasterCatalogueNumber == torrent.remasterCatalogueNumber

    other_torrents = filter(same_edition, group.torrent)
    current_formats = set(
        Format(t.format, t.encoding, None, None) for t in other_torrents
    )

    needed_formats = torrent.allowed_transcodes.difference(current_formats)
    needed_formats = needed_formats.intersection(requested_formats)

    return needed_formats


# ---------------------------------------------------------------------------
# Report model + discovery
# ---------------------------------------------------------------------------

@dataclass
class MissingItem:
    group_id: int
    torrent_id: int
    artist: str
    group_name: str
    year: int
    media: str
    edition: str
    missing: set[Format]
    permalink: str

    @property
    def missing_names(self) -> list[str]:
        # stable ordering for output
        return sorted(f.long_name or f.name for f in self.missing)


def find_missing(
    api,
    mode: str,
    media: set[str],
    requested_formats: set[Format],
    skip: set[str] | None = None,
    on_examined: Callable[[int], None] | None = None,
    cache=None,
    formats_key: str | None = None,
    refresh: bool = False,
) -> Iterator[MissingItem]:
    """Stream releases in the user's history that are missing requested formats.

    Reuses ``WhatAPI.get_candidates`` for the paginated crawl of the user's
    history, then for each FLAC candidate fetches its group and computes what's
    missing. Yields one ``MissingItem`` per release that is missing at least one
    requested format.

    When ``cache`` is a ``MissingCache`` and ``refresh`` is false, torrent ids
    already stored as complete for ``formats_key`` are added to ``skip`` so
    their group is not fetched again. Missing rows are not skipped: they are
    fetched again and rewritten. ``refresh`` ignores stored complete ids and
    rewrites every row that is examined. A group that fails to fetch is left
    unchanged in the cache.
    """
    if cache is not None and not formats_key:
        raise ValueError("formats_key is required when cache is set")

    if cache is not None and not refresh:
        cached = cache.complete_ids(formats_key)
        if cached:
            LOGGER.info(
                "Skipping %d complete release(s) already in the cache",
                len(cached),
            )
        skip = cached if skip is None else set(skip) | cached
    elif refresh:
        LOGGER.info("Refreshing cache; rechecking releases marked complete")

    base = api.base_url.rstrip("/")

    for groupid, torrentid in api.get_candidates(mode, skip=skip, media=media):
        try:
            group = get_torrent_group(api, groupid)
        except Exception as e:  # noqa: BLE001 - keep scanning on a single failure
            LOGGER.warning("Could not fetch group %s: %s", groupid, e)
            continue

        matches = [t for t in group.torrent if t.id == torrentid]
        if not matches:
            LOGGER.warning(
                "Torrent %s not found in group %s, skipping", torrentid, groupid
            )
            continue
        torrent = matches[0]

        needed = formats_needed(group, torrent, requested_formats)
        permalink = f"{base}/torrents.php?id={group.id}&torrentid={torrent.id}"

        if on_examined is not None:
            on_examined(torrentid)

        if not needed:
            if cache is not None:
                cache.mark_complete(
                    torrent_id=torrent.id,
                    group_id=group.id,
                    formats_key=formats_key,
                    artist=group.formatted_artist_string,
                    group_name=group.name,
                    year=group.year,
                    media=torrent.media,
                    edition=torrent.formatted_media_info,
                    permalink=permalink,
                )
            continue

        item = MissingItem(
            group_id=group.id,
            torrent_id=torrent.id,
            artist=group.formatted_artist_string,
            group_name=group.name,
            year=group.year,
            media=torrent.media,
            edition=torrent.formatted_media_info,
            missing=needed,
            permalink=permalink,
        )
        if cache is not None:
            cache.mark_missing(item, formats_key)
        LOGGER.info(
            "  Missing %s: %s - %s (%s) %s",
            ", ".join(item.missing_names),
            item.artist,
            item.group_name,
            item.year,
            item.edition,
        )
        yield item


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_text(items: list[MissingItem]) -> str:
    if not items:
        return "No releases missing the requested formats. Nice and tidy.\n"

    lines = [
        f"{len(items)} release(s) missing requested formats:",
        "",
    ]
    for it in items:
        lines.append(
            f"[{', '.join(it.missing_names)}] "
            f"{it.artist} - {it.group_name} ({it.year}) {it.edition}"
        )
        lines.append(f"    {it.permalink}")
    lines.append("")
    return "\n".join(lines)


def render_urls(items: list[MissingItem]) -> str:
    # one permalink per line -- consumable by `orpheusmorebetter <file>`
    return "".join(f"{it.permalink}\n" for it in items)


def render_csv(items: list[MissingItem]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "artist",
            "release",
            "year",
            "media",
            "edition",
            "missing",
            "group_id",
            "torrent_id",
            "permalink",
        ]
    )
    for it in items:
        writer.writerow(
            [
                it.artist,
                it.group_name,
                it.year,
                it.media,
                it.edition,
                " + ".join(it.missing_names),
                it.group_id,
                it.torrent_id,
                it.permalink,
            ]
        )
    return buf.getvalue()


RENDERERS = {
    "text": render_text,
    "urls": render_urls,
    "csv": render_csv,
}
