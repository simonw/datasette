"""
Command-line data sources: files, URLs and standard input.

``datasette data.csv https://example.com/b.json -`` turns each argument that
is not a SQLite database into a :class:`Source`. This module parses those
arguments, picks a table name for each one, and makes every source available
as a local file - downloading URLs and spooling standard input to temporary
files - because loaders read from a file path.

Argument syntax: ``[table:]source[:format]``

- ``source`` is a file path, an ``http://`` or ``https://`` URL, or ``-``
  for standard input. ``-`` always means standard input, even if a file
  called ``-`` exists (use ``./-`` for that file). ``stdin`` works too, as
  it does for ``sqlite-utils memory``, unless a file called ``stdin``
  exists.
- ``table:`` sets the table name. A ``:`` followed by ``//`` belongs to a
  URL (``https://...``) and a single letter followed by ``:\\`` is a
  Windows drive (``C:\\data.csv``), so neither is a table name. On Windows
  a letter followed by ``:/`` is a drive too; elsewhere ``t:/data/a.csv``
  is table ``t`` from ``/data/a.csv``.
- ``:format`` names the loader to use instead of sniffing the content, for
  example ``data.txt:csv`` or ``stdin:ndjson``. It is only recognized when
  the text after the last ``:`` is the name of a loader (or ``nl`` /
  ``jsonl``, for newline-delimited JSON), and never inside a URL's query
  string or fragment: ``https://example.com/q?f=a:csv`` is downloaded as
  typed. Formats are otherwise detected from the content, never from the
  file extension. (``-:csv`` parses too, but on the command line click
  takes anything starting with ``-`` for an option, so it needs ``--``
  before it: ``datasette -- -:csv``. ``stdin:csv`` does not.)
- An argument that is the path of an existing file is always used as that
  path, even if it contains colons.

Nothing here needs a Datasette instance.
"""

import asyncio
import contextlib
import dataclasses
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from collections.abc import Callable, Iterable

SQLITE_HEADER = b"SQLite format 3\x00"
# Zero-byte files with these extensions (or none) are empty SQLite databases,
# as they always have been for ``datasette empty.db``
SQLITE_EXTENSIONS = ("", ".db", ".sqlite", ".sqlite3")
STDIN_SPECS = ("-", "stdin")
STDIN_TABLE = "stdin"
URL_SCHEMES = ("http", "https")
# Format names accepted in addition to loader names, for compatibility with
# sqlite-utils (which calls newline-delimited JSON "nl")
FORMAT_ALIASES = {"nl": "ndjson", "jsonl": "ndjson"}
# Downloads bigger than this suggest --save, so they are not lost at exit
LARGE_DOWNLOAD_BYTES = 100 * 1024 * 1024

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
_URL_ANYWHERE_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
# /dev/fd/63 and /proc/self/fd/63, as made by bash's <(command)
_FD_PATH_RE = re.compile(r"^/(dev|proc/(self|\d+))/fd/\d+$")
PIPE_TABLE = "pipe"
# Looks like a filename: something, a dot, then a short extension
_FILENAME_RE = re.compile(r"^.+\.[A-Za-z0-9]{1,8}$")
_NOT_WORD_RE = re.compile(r"\W+")


class SourceError(Exception):
    """An argument cannot be used as a data source."""


class DownloadError(SourceError):
    """A URL could not be downloaded."""


@dataclasses.dataclass
class Source:
    """One data source from the command line."""

    #: The argument exactly as it was typed
    spec: str
    #: ``"file"``, ``"url"`` or ``"stdin"``
    kind: str
    #: The file path, the URL, or ``"-"``
    location: str
    #: Table name given with ``table:source``, if any
    name: str | None = None
    #: Format given with ``source:format``, if any (a loader name)
    format: str | None = None
    #: The table this source loads into, see :func:`assign_table_names`
    table: str | None = None
    #: Local file to load - the file itself or a temporary copy
    path: str | None = None
    #: ``path`` is a temporary copy (of a URL, stdin or a pipe)
    temporary: bool = False
    #: The loader that will read ``path``, once :func:`sniff` has run
    loader: object = None

    @property
    def label(self) -> str:
        """
        How to refer to this source in messages: a URL with any credentials
        redacted (see :func:`redact_url`), since messages end up on pages
        and in logs. Use :attr:`location` to fetch it.
        """
        if self.kind == "stdin":
            return "standard input"
        if self.kind == "url":
            return redact_url(self.location)
        return self.location

    def fingerprint(self):
        """
        Identifies the content: the file's path, size and modification time.
        For URLs, stdin and pipes - copied once per process or --reload
        session - the kind and location are enough.
        """
        if self.kind == "file":
            try:
                st = os.stat(self.location)
            except OSError:
                st = None
            if st is not None and stat.S_ISREG(st.st_mode):
                return [os.path.realpath(self.location), st.st_size, st.st_mtime_ns]
        return [self.kind, self.location]

    @property
    def needs_copy(self) -> bool:
        "URLs, standard input and pipes are copied to a local file first."
        if self.kind != "file":
            return True
        try:
            return not stat.S_ISREG(os.stat(self.location).st_mode)
        except OSError:
            return False


# Recognizing arguments -------------------------------------------------------


def is_sqlite_file(path: str) -> bool:
    """
    True if ``path`` is a SQLite database: it starts with the SQLite header,
    or it is a zero-byte file whose extension is one of ``.db``,
    ``.sqlite``, ``.sqlite3`` or none at all (SQLite treats an empty file as
    an empty database). Only regular files are read.
    """
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if st.st_size == 0:
        return os.path.splitext(path)[1].lower() in SQLITE_EXTENSIONS
    with open(path, "rb") as fp:
        return fp.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def is_url(text: str) -> bool:
    "True for anything that starts with ``scheme://``."
    return bool(_SCHEME_RE.match(text))


def maybe_source(arg: str) -> bool:
    """
    Could this argument be a data source rather than a SQLite database?
    True for URLs, ``-``, existing files that are not SQLite databases and
    non-existent paths containing a ``:``. Used before plugins are loaded,
    so it does not need to know the format names.
    """
    if arg == "-" or is_url(arg):
        return True
    if os.path.isdir(arg):
        return False
    if os.path.exists(arg):
        # Even a file called "stdin"
        return not is_sqlite_file(arg)
    return arg in STDIN_SPECS or ":" in arg


def _is_drive(text: str, colon: int, windows: bool) -> bool:
    # C:\data.csv - one letter, a colon, then a backslash. C:/data.csv only
    # on Windows: elsewhere t:/data/a.csv is table t from /data/a.csv
    if colon != 1 or not text[0].isalpha():
        return False
    return text[2:3] == "\\" or (windows and text[2:3] == "/")


def parse_source(
    spec: str, formats: Iterable[str] = (), *, windows: bool | None = None
) -> Source:
    """
    Parse ``[table:]source[:format]`` into a :class:`Source`. ``formats`` is
    the set of loader names that a ``:format`` suffix may use. The caller
    checks for existing files first: an argument that is the path of an
    existing file should be passed to ``Source(kind="file", ...)`` as-is.

    Raises :class:`SourceError` for an empty table name, an empty source or
    a URL scheme other than http and https.
    """
    formats = {f.lower() for f in formats}
    rest = spec
    format_ = None
    name = None
    # A trailing :format - unless that colon is in a URL's query string or
    # fragment, where it is part of the URL
    head, colon, tail = rest.rpartition(":")
    url = _URL_ANYWHERE_RE.search(head)
    if url and any(c in head[url.start() :] for c in "?#"):
        colon = ""
    if colon and head:
        key = FORMAT_ALIASES.get(tail.lower(), tail.lower())
        if key in formats:
            rest, format_ = head, key
    # A leading table:
    index = rest.find(":")
    if index != -1 and not rest[index + 1 :].startswith("//"):
        candidate = rest[:index]
        if windows is None:
            windows = os.name == "nt"
        if not _is_drive(rest, index, windows) and not (
            "/" in candidate or "\\" in candidate
        ):
            if not candidate.strip():
                raise SourceError(f"{spec}: the table name before ':' is empty")
            name, rest = candidate.strip(), rest[index + 1 :]
    if not rest:
        raise SourceError(f"{spec}: nothing to load after the table name")
    if rest in STDIN_SPECS:
        return Source(spec, "stdin", "-", name=name, format=format_)
    match = _SCHEME_RE.match(rest)
    if match:
        scheme = match.group(1).lower()
        if scheme not in URL_SCHEMES:
            raise SourceError(
                f"{spec}: {scheme}:// URLs are not supported, "
                "only http:// and https://"
            )
        return Source(spec, "url", rest, name=name, format=format_)
    return Source(spec, "file", rest, name=name, format=format_)


# Table names ----------------------------------------------------------------


def sanitize_table_name(name: str) -> str:
    """
    Turn a filename stem or host name into a pleasant table name: runs of
    anything other than letters, digits and underscores become a single
    ``_``, so it can be used in SQL without quoting (unless it starts with a
    digit). ``sqlite_`` is reserved by SQLite, so such names get a leading
    ``_``. Returns ``"data"`` if nothing is left.
    """
    name = unicodedata.normalize("NFC", name)
    name = _NOT_WORD_RE.sub("_", name).strip("_")
    if not name:
        return "data"
    if name.lower().startswith("sqlite_"):
        name = "_" + name
    return name


def table_name_for_path(path: str) -> str:
    "The file's name without its extension, sanitized."
    base = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    stem = base.rsplit(".", 1)[0] if "." in base.lstrip(".") else base
    return sanitize_table_name(stem)


def table_name_for_url(url: str) -> str:
    """
    The last segment of the URL's path, without its extension, if it looks
    like a filename (``/data/events.csv`` - ``events``). Otherwise the host
    name (``https://example.com/api/`` - ``example_com``).
    """
    parts = urllib.parse.urlsplit(url)
    segment = urllib.parse.unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    if _FILENAME_RE.match(segment):
        return table_name_for_path(segment)
    return sanitize_table_name(parts.hostname or "data")


def suggested_table_name(source: Source) -> str:
    "The table name a source gets if it was not given one."
    if source.kind == "stdin":
        return STDIN_TABLE
    if source.kind == "url":
        return table_name_for_url(source.location)
    if _FD_PATH_RE.match(source.location):
        # <(command) gives a different number each time, which makes a
        # poor table name
        return PIPE_TABLE
    return table_name_for_path(source.location)


def validate_table_name(name: str, spec: str) -> None:
    if name.lower().startswith("sqlite_"):
        raise SourceError(
            f"{spec}: table names starting with 'sqlite_' are reserved by SQLite"
        )
    if "\x00" in name:
        raise SourceError(f"{spec}: table names cannot contain NUL characters")


def assign_table_names(sources: list[Source]) -> None:
    """
    Set ``source.table`` for every source. Names given with ``table:`` are
    used as they are and must be unique. Other sources get
    :func:`suggested_table_name`, with ``_2``, ``_3`` ... added when a name is
    already taken: ``a/events.csv b/events.csv`` gives ``events`` and
    ``events_2``. Names are compared case-insensitively, as SQLite does.
    """
    stdin = [source for source in sources if source.kind == "stdin"]
    if len(stdin) > 1:
        raise SourceError(
            f"{stdin[0].spec} and {stdin[1].spec} both read standard input, "
            "which can only be read once"
        )
    taken = {}
    for source in sources:
        if source.name is None:
            continue
        validate_table_name(source.name, source.spec)
        key = source.name.lower()
        if key in taken:
            raise SourceError(
                f"{taken[key].spec} and {source.spec} would both create "
                f"a table called {source.name!r}"
            )
        taken[key] = source
        source.table = source.name
    for source in sources:
        if source.name is not None:
            continue
        base = suggested_table_name(source)
        name, i = base, 2
        while name.lower() in taken:
            name = f"{base}_{i}"
            i += 1
        taken[name.lower()] = source
        source.table = name


# Making sources local ----------------------------------------------------------


ProgressCallback = Callable[[int, "int | None"], None]


def spool(stream, directory: str, prefix: str = "stdin-", progress=None) -> str:
    """
    Copy a binary stream (standard input, a pipe) to a new temporary file in
    ``directory`` and return its path. The file is removed if copying fails
    or is interrupted.
    """
    fd, path = tempfile.mkstemp(dir=directory, prefix=prefix)
    try:
        with os.fdopen(fd, "wb") as fp:
            done = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                fp.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, None)
    except BaseException:
        _unlink(path)
        raise
    return path


async def spool_in_thread(
    open_stream: Callable,
    directory: str,
    prefix: str = "stdin-",
    progress: "ProgressCallback | None" = None,
    *,
    progress_interval: float = 0.25,
) -> str:
    """
    :func:`spool` on a daemon thread of its own, for a stream that can block
    for as long as the program writing it likes: standard input, a pipe.
    ``open_stream()`` is called on that thread and returns a binary stream,
    which is closed afterwards unless it is ``sys.stdin.buffer``.

    The event loop never waits for a read. Cancelling returns at once and
    removes the partial file; the thread stops at its next read, or with
    the process (a read from a pipe cannot be interrupted, and a thread in
    the default executor would make ``asyncio.run()`` and interpreter exit
    wait for it). ``progress(bytes, None)`` is called on the event loop, at
    most every ``progress_interval`` seconds and once at the end.
    """
    import threading

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    stop = threading.Event()
    fd, path = tempfile.mkstemp(dir=directory, prefix=prefix)

    def call_on_loop(fn, *args):
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            # The loop has closed: nobody is waiting any more
            pass

    def report(done):
        if progress is not None and not future.done():
            progress(done, None)

    def settle(result, error):
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def work():
        stream = None
        try:
            with os.fdopen(fd, "wb") as fp:
                stream = open_stream()
                read = getattr(stream, "read1", stream.read)
                done = 0
                last = 0.0
                while not stop.is_set():
                    chunk = read(1024 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last >= progress_interval:
                        last = now
                        call_on_loop(report, done)
            if stop.is_set():
                _unlink(path)
                return
            call_on_loop(report, done)
            call_on_loop(settle, path, None)
        except BaseException as ex:  # noqa: BLE001 - handed to the loop
            _unlink(path)
            call_on_loop(settle, None, ex)
        finally:
            if stream is not None and stream is not getattr(sys.stdin, "buffer", None):
                with contextlib.suppress(Exception):
                    stream.close()

    thread = threading.Thread(
        target=work, name=f"datasette-spool-{prefix.rstrip('-')}", daemon=True
    )
    thread.start()
    try:
        return await future
    except BaseException:
        stop.set()
        _unlink(path)
        raise


async def download(
    url: str,
    directory: str,
    *,
    progress: ProgressCallback | None = None,
    timeout: float = 30.0,
    chunk_size: int = 256 * 1024,
) -> str:
    """
    Download ``url`` to a new temporary file in ``directory`` and return its
    path. Redirects are followed and ``Content-Encoding`` is decoded.
    ``progress(bytes_received, content_length_or_None)`` is called after
    every chunk. Raises :class:`DownloadError` for HTTP errors and network
    problems; the partial file is removed on any error and on cancellation.
    """
    import httpx2

    # Error messages end up on pages and in logs: never with credentials
    shown = redact_url(url)
    if urllib.parse.urlsplit(url).scheme.lower() not in URL_SCHEMES:
        raise DownloadError(f"{shown}: only http:// and https:// URLs are supported")
    from datasette.version import __version__

    fd, path = tempfile.mkstemp(dir=directory, prefix="download-")
    try:
        client = httpx2.AsyncClient(
            follow_redirects=True,
            timeout=httpx2.Timeout(timeout),
            headers={"user-agent": f"datasette/{__version__}"},
        )
        with os.fdopen(fd, "wb") as fp:
            async with client, client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise DownloadError(
                        f"{shown} returned HTTP {response.status_code} "
                        f"{response.reason_phrase}".rstrip()
                    )
                total = _content_length(response.headers)
                async for chunk in response.aiter_bytes(chunk_size):
                    fp.write(chunk)
                    if progress is not None:
                        progress(response.num_bytes_downloaded, total)
    except DownloadError:
        _unlink(path)
        raise
    except httpx2.HTTPError as ex:
        _unlink(path)
        # httpx can mention the URL, or the one a redirect led to
        message = redact_urls_in(str(ex)) or type(ex).__name__
        raise DownloadError(f"Could not download {shown}: {message}") from ex
    except BaseException:
        _unlink(path)
        raise
    return path


def _content_length(headers):
    try:
        value = int(headers.get("content-length", ""))
    except ValueError:
        return None
    return value if value >= 0 else None


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def use_local_file(source: Source) -> bool:
    """
    For a file source: set ``source.path`` to the file and return True if
    it is a regular file, return False if it is something that has to be
    copied first (a named pipe, ``/dev/fd/N``). Raises :class:`SourceError`
    if it does not exist or is a directory.
    """
    location = source.location
    try:
        mode = os.stat(location).st_mode
    except FileNotFoundError:
        raise SourceError(f"Path '{location}' does not exist.") from None
    if stat.S_ISDIR(mode):
        raise SourceError(f"{source.spec}: '{location}' is a directory")
    if stat.S_ISREG(mode):
        source.path = location
        return True
    return False


async def fetch(
    source: Source,
    directory: str,
    *,
    stdin=None,
    progress: ProgressCallback | None = None,
) -> None:
    """
    Make ``source.path`` a local file: the file itself, a download of the
    URL, or a copy of standard input (or of a pipe such as ``<(command)``,
    which can only be read once) in ``directory``. Raises
    :class:`SourceError` if a file does not exist.
    """
    if source.path is not None:
        return
    if source.kind == "url":
        source.path = await download(source.location, directory, progress=progress)
        source.temporary = True
    elif source.kind == "stdin":
        stream = stdin if stdin is not None else sys.stdin.buffer
        source.path = await spool_in_thread(
            lambda: stream, directory, "stdin-", progress
        )
        source.temporary = True
    else:
        location = source.location
        if not use_local_file(source):
            # A named pipe or /dev/fd/N: sniffing would consume it. Opening
            # a named pipe blocks until something writes to it, so that
            # happens on the spooling thread too
            source.path = await spool_in_thread(
                lambda: open(location, "rb"),  # noqa: SIM115 - closed by the thread
                directory,
                "pipe-",
                progress,
            )
            source.temporary = True


def check_stdin_available(stdin=None) -> None:
    "Refuse to wait for keyboard input when ``-`` was passed on a terminal."
    stream = stdin if stdin is not None else sys.stdin
    try:
        interactive = stream.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if interactive:
        raise SourceError(
            "'-' reads data from standard input, but standard input is a "
            "terminal. Pipe data in, e.g. cat data.csv | datasette -"
        )


def remove_temporary(sources: Iterable[Source]) -> None:
    "Delete the temporary copies made by :func:`fetch`."
    for source in sources:
        if source.temporary and source.path:
            _unlink(source.path)
            source.path = None
            source.temporary = False


# Progress reporting ----------------------------------------------------------


# Control and formatting characters: C0 and C1 controls (ESC starts the
# sequences that move a terminal's cursor or set its title, and an 8-bit
# CSI can do the same), bidirectional overrides and zero-width characters
# (which make text display as something else), and line and paragraph
# separators
_INVISIBLE_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}
_LINE_BREAKS_RE = re.compile(r"[\t\n\v\f\r\x1c-\x1e\x85  ]+")


def strip_invisible(text: str) -> str:
    "``text`` without control, formatting or line separator characters."
    if text.isprintable():
        return text
    return "".join(
        ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )


def one_line(text) -> str:
    """
    ``text`` made safe to print as part of a line on a terminal: tabs and
    line breaks become spaces, other control and formatting characters are
    removed. Table and column names, notes and errors can come from a file
    or an upload, and could otherwise move the cursor, set the window title
    or fake a line of the log.
    """
    text = str(text)
    if text.isprintable():
        return text
    return strip_invisible(_LINE_BREAKS_RE.sub(" ", text))


def safe_lines(text: str) -> str:
    "Each line of ``text`` made safe with :func:`one_line`."
    return "\n".join(one_line(line) for line in text.split("\n"))


def human_size(num_bytes: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            if unit == "bytes":
                return f"{int(num_bytes):,} bytes"
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"  # pragma: no cover


class StatusLine:
    """
    A line of progress on a stream (standard error). On a terminal it is
    rewritten in place at most every ``interval`` seconds; elsewhere only
    :meth:`done` writes anything, so logs are not flooded. Control and
    formatting characters are never written (see :func:`one_line`).
    """

    def __init__(self, stream=None, interval: float = 0.2):
        self.stream = stream if stream is not None else sys.stderr
        try:
            self.interactive = self.stream.isatty()
        except (AttributeError, ValueError):
            self.interactive = False
        self.interval = interval
        self._last = 0.0
        self._width = 0

    def update(self, text: str, force: bool = False) -> None:
        if not self.interactive:
            return
        now = time.monotonic()
        if not force and now - self._last < self.interval:
            return
        self._last = now
        text = one_line(text)
        padding = " " * max(0, self._width - len(text))
        self._width = len(text)
        self.stream.write("\r" + text + padding)
        self.stream.flush()

    def start(self, text: str) -> None:
        "Announce a step: rewritten in place on a terminal, a line elsewhere."
        if self.interactive:
            self.update(text, force=True)
        else:
            self.stream.write(one_line(text) + "\n")
            self.stream.flush()

    def clear(self) -> None:
        if self.interactive and self._width:
            self.stream.write("\r" + " " * self._width + "\r")
            self.stream.flush()
            self._width = 0

    def done(self, text: str) -> None:
        "Write ``text``: lines, without control or formatting characters."
        self.clear()
        self.stream.write(safe_lines(text) + "\n")
        self.stream.flush()


def download_progress(label: str, status: StatusLine) -> ProgressCallback:
    "A :func:`download` progress callback that updates ``status``."
    start = time.monotonic()

    def progress(done, total):
        elapsed = max(time.monotonic() - start, 1e-6)
        rate = human_size(done / elapsed) + "/s"
        if total:
            percent = min(100, done * 100 // total)
            status.update(
                f"Downloading {label}: {human_size(done)} of "
                f"{human_size(total)} ({percent}%) {rate}"
            )
        else:
            status.update(f"Downloading {label}: {human_size(done)} {rate}")

    return progress


# The --save command ------------------------------------------------------------


def quote_command(args: list[str]) -> str:
    "Quote a command line for the user's shell."
    if os.name == "nt":  # pragma: no cover
        return subprocess.list2cmdline(args)
    return shlex.join(args)


REDACTED = "REDACTED"
# Query string parameters whose values are hidden on the scratch database
# page: a word of the parameter's name (api_key, X-Amz-Signature,
# accessToken, sig ...) is one of these, or the name ends in key, token or
# secret
_SECRET_WORDS = {
    "key",
    "token",
    "secret",
    "apikey",
    "password",
    "passwd",
    "pwd",
    "pass",
    "auth",
    "authorization",
    "sig",
    "signature",
    "credential",
    "credentials",
    "session",
    "sessionid",
    "jwt",
    "bearer",
}
_WORD_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _secret_parameter(name: str) -> bool:
    words = {word.lower() for word in _WORD_RE.findall(name)}
    return bool(words & _SECRET_WORDS) or name.lower().endswith(
        ("key", "token", "secret")
    )


def redact_url(url: str) -> str:
    """
    ``url`` with credentials replaced by ``REDACTED``: the password in
    ``user:password@`` (or the whole user name if there is no password,
    since tokens are often passed that way), and the values of query
    string parameters whose names look like keys, tokens, signatures or
    passwords. Everything else is kept as typed.
    """
    parts = urllib.parse.urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        user, colon, _password = userinfo.partition(":")
        userinfo = f"{user}:{REDACTED}" if colon else REDACTED
        netloc = f"{userinfo}@{host}"
    query = parts.query
    if query:
        pieces = []
        for piece in query.split("&"):
            name, equals, value = piece.partition("=")
            if equals and value and _secret_parameter(urllib.parse.unquote(name)):
                piece = f"{name}={REDACTED}"
            pieces.append(piece)
        query = "&".join(pieces)
    if (netloc, query) == (parts.netloc, parts.query):
        return url
    return urllib.parse.urlunsplit(
        (parts.scheme, netloc, parts.path, query, parts.fragment)
    )


_URL_IN_TEXT_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")


def redact_urls_in(text: str) -> str:
    "``text`` with :func:`redact_url` applied to every URL in it."
    return _URL_IN_TEXT_RE.sub(lambda match: redact_url(match.group(0)), text)


def display_spec(source: Source) -> str:
    "The argument as typed, with any credentials in a URL redacted."
    if source.kind != "url":
        return source.spec
    redacted = redact_url(source.location)
    if redacted == source.location:
        return source.spec
    return source.spec.replace(source.location, redacted, 1)


def save_command(
    sources: list[Source],
    save_path: str = "data.db",
    *,
    extra_args: Iterable[str] = (),
    redact: bool = False,
) -> str:
    """
    The ``datasette ... --save data.db`` command that loads the same sources
    into a file, using the arguments as they were typed, followed by
    ``extra_args`` (``--plugins-dir`` options that provide formats).
    ``redact=True`` hides credentials in URLs, see :func:`redact_url`.
    """
    specs = [display_spec(s) if redact else s.spec for s in sources]
    return quote_command(["datasette", *specs, *extra_args, "--save", save_path])


def suggest_save_path(avoid: Iterable[str] = (), base: str = "data") -> str:
    """
    A file name for the ``--save`` suggestion that does not exist yet and
    is not one of the ``avoid`` paths: ``data.db``, else ``data_2.db`` ...
    Suggesting a file that exists would add tables to someone's database,
    or fail because it already has one of them.
    """
    avoid = set(map(_realpath, avoid)) - {None}
    candidate, i = f"{base}.db", 2
    while os.path.lexists(candidate) or _realpath(candidate) in avoid:
        candidate = f"{base}_{i}.db"
        i += 1
    return candidate


def _realpath(path: str) -> str | None:
    """
    ``os.path.realpath()``, or None for a relative path if the current
    directory has been deleted (then nothing exists relative to it).
    """
    try:
        return os.path.realpath(path)
    except OSError:
        return None


def remove_tree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
