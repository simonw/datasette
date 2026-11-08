"""
What the command line prints about imports on standard error.

While Datasette serves, a :class:`TerminalReporter` prints plain lines as
each background import starts, progresses and finishes. Loading before
serving (``--get``), :func:`describe_loaded`, :func:`loading_text` and
:func:`download_status` say the same things on a
:class:`~datasette.imports.sources.StatusLine`. Both kinds of line say
that a table was loaded with :func:`loaded_line`, and warn about a large
download with :func:`large_download_warning`.

Table and column names, titles, errors and notes can come from a file or
an upload: each goes into a line through
:func:`~datasette.imports.sources.one_line`, and every line is written
without control or formatting characters.

This module does not import :mod:`datasette.imports.background`, which
re-exports :class:`TerminalReporter`: the reporter reads only what an
import (a :class:`~datasette.imports.background.TableImport`) says, and its
registry's worker slots.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time

from datasette.imports import FINISHED_STATES
from datasette.imports.loaders import NOTE_LOG_ATTRIBUTE
from datasette.imports.sources import (
    LARGE_DOWNLOAD_BYTES,
    download_progress,
    human_size,
    one_line,
    safe_lines,
)


def plural(count: int, word: str) -> str:
    "``1 row``, ``2 rows``, ``1,200 rows``."
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def note_lines(notes) -> str:
    "The loader's notes, one indented line each, to follow a summary line."
    return "".join(f"\n  note: {one_line(note)}" for note in notes)


def loaded_line(
    label,
    table,
    *,
    rows: int,
    loader,
    seconds: float,
    where: str = "",
    created: bool = True,
    widenings=(),
    notes=(),
    waited: float = 0.0,
) -> str:
    """
    The line saying that an input was loaded into a table: ``Loaded 3 rows
    from dogs.csv (csv) into table dogs in 0.01s``, with ``where`` after the
    table name (`` in data.db``), how long it waited for an import worker if
    that was ``waited`` half a second or more, and a line for each column
    that changed type and each of the loader's notes. Or, if ``created`` is
    False, ``dogs.csv has no rows, so no table was created``.
    """
    label = one_line(label)
    notes = note_lines(notes)
    if not created:
        return f"{label} has no rows, so no table was created{notes}"
    line = (
        f"Loaded {plural(rows, 'row')} from {label} ({one_line(loader)}) "
        f"into table {one_line(table)}{where} in {seconds:.2f}s"
    )
    if waited >= 0.5:
        line += f", after waiting {waited:.1f}s for an import worker"
    for widening in widenings:
        line += (
            f"\n  column {one_line(widening.column)} changed from "
            f"{widening.from_type} to {widening.to_type} at row "
            f"{widening.at_row + 1:,}"
        )
    return line + notes


def large_download_warning(label, size: int, *, total_known: bool) -> str:
    """
    The warning that a download of ``size`` bytes into the scratch
    database - all of it, if ``total_known``, or so far - is lost when
    Datasette stops.
    """
    return (
        f"{one_line(label)} is {human_size(size)}"
        f"{'' if total_known else ' so far'}. The scratch database is deleted "
        "when Datasette stops: add --save data.db to keep the data."
    )


# Background imports ----------------------------------------------------------------


class TerminalReporter:
    """
    A registry listener that prints a line when each import starts, every
    ``interval`` seconds while it runs, and when it finishes - plain lines on
    standard error, suitable for a long-running server whose log is
    interleaved with uvicorn's (no progress bar rewriting itself in place).

    ``where`` is added after "into table NAME" (`` in data.db``).
    ``warn_large`` warns that big downloads are lost at exit (not with
    ``--save``). It reports on the imports it hears about: the command line
    passes it to
    :meth:`~datasette.imports.background.ImportRegistry.start` for each of
    its own inputs, so it leaves out uploads, which go into other databases
    and are not part of its summary.
    """

    def __init__(
        self,
        stream=None,
        *,
        interval: float = 10.0,
        where="",
        warn_large=True,
    ):
        self.stream = stream
        self.interval = interval
        self.where = where
        self.warn_large = warn_large
        self._last = {}
        self._warned = set()
        self._converting = set()
        self._started = None
        self._seen = set()

    def _write(self, text):
        # Every line without control or formatting characters, in case a
        # value that came from a file or an upload got in unescaped
        text = safe_lines(text)
        stream = self.stream if self.stream is not None else sys.stderr
        try:
            stream.write(text + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass

    def __call__(self, job, event):
        now = time.monotonic()
        if self._started is None:
            self._started = now
        self._seen.add(job.id)
        # Table names, titles, errors and notes can come from an upload or a
        # file: one line each, see one_line()
        title = one_line(job.title)
        where = f"table {one_line(job.table)}{self.where}"
        if event == "downloading":
            self._last[job.id] = now
            if job.kind == "stdin":
                self._write(f"Reading {title} for {where}")
            else:
                self._write(f"Downloading {title} for {where}")
        elif event == "waiting":
            if job.registry.slots.locked():
                self._write(f"Queued {title} for {where}: waiting for an import worker")
        elif event == "importing":
            self._last[job.id] = now
            size = f", {human_size(job.downloaded)}" if job.downloaded else ""
            if job.kind == "file" and job.source.path:
                with contextlib.suppress(OSError):
                    size = f", {human_size(os.path.getsize(job.source.path))}"
            self._write(
                f"Importing {title} ({one_line(job.loader)}{size}) into {where}"
            )
        elif event == "progress":
            if (
                self.warn_large
                and job.state == "downloading"
                and job.id not in self._warned
                and (job.download_size or job.downloaded or 0) > LARGE_DOWNLOAD_BYTES
            ):
                self._warned.add(job.id)
                self._write(
                    large_download_warning(
                        job.title,
                        job.download_size or job.downloaded,
                        total_known=bool(job.download_size),
                    )
                )
            if job.converting and job.id not in self._converting:
                # Every row is in, but the numbers stored in a column that
                # changed to text late on are still being converted: seconds
                # per million rows, so say why the import is not done yet
                self._converting.add(job.id)
                self._last[job.id] = now
                self._write(
                    f"  {one_line(job.describe())} (a column changed to text "
                    "after the first rows)"
                )
            elif now - self._last.get(job.id, now) >= self.interval:
                self._last[job.id] = now
                self._write(f"  {one_line(job.describe())} after {job.seconds:.0f}s")
        elif event in FINISHED_STATES:
            # The event that says it has finished: named after its state
            self._last.pop(job.id, None)
            self._converting.discard(job.id)
            self._write(self.finished_line(job))
            jobs = [j for j in job.registry.imports() if j.id in self._seen]
            if any(j.active for j in jobs):
                return
            if len(jobs) > 1:
                done = [j for j in jobs if j.state == "done"]
                rows = sum(j.rows for j in done)
                elapsed = now - self._started
                problems = len(jobs) - len(done)
                extra = f", {problems} failed or cancelled" if problems else ""
                self._write(
                    f"Imported {len(done)} of {len(jobs)} inputs "
                    f"({plural(rows, 'row')}) in {elapsed:.1f}s{extra}"
                )
            # The next import starts a batch of its own
            self._seen.clear()
            self._started = None

    def finished_line(self, job) -> str:
        if job.state == "done":
            return loaded_line(
                job.title,
                job.table,
                rows=job.rows,
                loader=job.loader,
                seconds=job.seconds,
                where=self.where,
                created=job.table_exists,
                widenings=job.widenings,
                notes=job.notes,
                waited=job.waited_seconds,
            )
        title = one_line(job.title)
        where = f"table {one_line(job.table)}{self.where}"
        return self._unfinished_line(job, title, where) + note_lines(job.notes)

    def _unfinished_line(self, job, title, where) -> str:
        kept = job.rows_kept or 0
        if job.on_error == "keep" and kept:
            outcome = f"; {where} has the first {plural(kept, 'row')}"
        elif job.on_error == "drop" and job.rows:
            outcome = f"; the {plural(job.rows, 'row')} imported were removed"
        else:
            outcome = ""
        if job.state == "failed":
            error = one_line(job.error)
            return f"Failed to import {title} into {where}: {error}{outcome}"
        if job.cancel_reason == "shutdown":
            return (
                f"Stopped importing {title} into {where}, because Datasette "
                f"is shutting down{outcome}"
            )
        return f"Cancelled import of {title} into {where}{outcome}"


# Loading before serving (--get) ----------------------------------------------------


def describe_loaded(loaded, where: str = "") -> str:
    """
    The line for a :class:`~datasette.imports.scratch.LoadedTable`: what
    :func:`loaded_line` says, or that a table was kept from the previous
    load of a ``--reload`` session.
    """
    if loaded.reused:
        return (
            f"Kept table {one_line(loaded.table)}{where} from the previous load: "
            f"{one_line(loaded.source.label)} has not changed"
        )
    return loaded_line(
        loaded.source.label,
        loaded.table,
        rows=loaded.rows,
        loader=loaded.loader,
        seconds=loaded.seconds,
        where=where,
        created=loaded.created,
        widenings=loaded.widenings,
        notes=loaded.notes,
    )


def loading_text(source, rows, import_status) -> str:
    "The --get status line while ``source`` loads."
    if import_status.finishing and import_status.backlog:
        # A column changed to text after the first rows: its stored numbers
        # are being turned back into the text they came from
        return f"Finishing {source.table}: {import_status.backlog:,} rows to convert"
    return f"Loading {source.label}: {rows:,} rows"


def download_status(source, status, warn):
    """
    A download progress callback for ``source`` that updates the
    :class:`~datasette.imports.sources.StatusLine` ``status``, and if
    ``warn`` says once that a large download is lost when Datasette stops.
    """
    progress = download_progress(source.label, status)
    warned = False

    def callback(done, total):
        nonlocal warned
        size = total or done
        if warn and not warned and size > LARGE_DOWNLOAD_BYTES:
            warned = True
            status.done(
                large_download_warning(source.label, size, total_known=bool(total))
            )
        progress(done, total)

    return callback


def _is_not_an_import_note(record):
    return not getattr(record, NOTE_LOG_ATTRIBUTE, None)


def print_notes_once():
    """
    The summary lines on stderr show the loaders' notes. A loader logs each
    one as a warning too, and with no logging configured Python's
    last-resort handler would print it again: leave notes out of that
    fallback. Handlers that are configured still get them.
    """
    if logging.lastResort is not None:
        logging.lastResort.addFilter(_is_not_an_import_note)


__all__ = [
    "TerminalReporter",
    "describe_loaded",
    "download_status",
    "large_download_warning",
    "loaded_line",
    "loading_text",
    "note_lines",
    "plural",
    "print_notes_once",
]
