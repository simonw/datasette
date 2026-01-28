"""
Streaming multipart/form-data parser for ASGI applications.

Supports:
- Streaming parsing without buffering entire body in memory
- Files spill to disk above configurable threshold
- Security limits on request size, file size, field count
- Both multipart/form-data and application/x-www-form-urlencoded
"""

import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)
from urllib.parse import parse_qsl

# Centralized defaults for multipart/form-data parsing
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
DEFAULT_MAX_REQUEST_SIZE = 100 * 1024 * 1024  # 100MB
DEFAULT_MAX_FIELDS = 1000
DEFAULT_MAX_FILES = 100
# If max_parts is not specified, it defaults to max_fields + max_files
DEFAULT_MAX_PARTS: Optional[int] = None
DEFAULT_MAX_FIELD_SIZE = 100 * 1024  # 100KB
DEFAULT_MAX_MEMORY_FILE_SIZE = 1024 * 1024  # 1MB
DEFAULT_MAX_PART_HEADER_BYTES = 16 * 1024  # 16KB
DEFAULT_MAX_PART_HEADER_LINES = 100
DEFAULT_MIN_FREE_DISK_BYTES = 50 * 1024 * 1024  # 50MB


class MultipartParseError(Exception):
    """Raised when multipart parsing fails."""

    pass


@dataclass
class UploadedFile:
    """
    Represents an uploaded file from a multipart form.

    Attributes:
        name: The form field name
        filename: The original filename from the upload
        content_type: The MIME type of the file
        size: Size in bytes
    """

    name: str
    filename: str
    content_type: Optional[str]
    size: int
    _file: tempfile.SpooledTemporaryFile = field(repr=False)

    async def read(self, size: int = -1) -> bytes:
        """Read file contents."""
        return await asyncio.to_thread(self._file.read, size)

    async def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position in file."""
        return await asyncio.to_thread(self._file.seek, offset, whence)

    async def close(self) -> None:
        """Close the underlying file."""
        await asyncio.to_thread(self._file.close)

    def close_sync(self) -> None:
        """Close the underlying file synchronously."""
        self._file.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    def __del__(self):
        try:
            self._file.close()
        except Exception:
            pass


class FormData:
    """
    Container for parsed form data, supporting both fields and files.

    Provides dict-like access with support for multiple values per key.
    """

    def __init__(self):
        self._data: List[Tuple[str, Union[str, UploadedFile]]] = []

    def append(self, key: str, value: Union[str, UploadedFile]) -> None:
        """Add a key-value pair."""
        self._data.append((key, value))

    def __getitem__(self, key: str) -> Union[str, UploadedFile]:
        """Get the first value for a key."""
        for k, v in self._data:
            if k == key:
                return v
        raise KeyError(key)

    def get(
        self, key: str, default: Any = None
    ) -> Optional[Union[str, UploadedFile]]:
        """Get the first value for a key, or default if not found."""
        try:
            return self[key]
        except KeyError:
            return default

    def getlist(self, key: str) -> List[Union[str, UploadedFile]]:
        """Get all values for a key."""
        return [v for k, v in self._data if k == key]

    def __contains__(self, key: str) -> bool:
        """Check if key exists."""
        return any(k == key for k, _ in self._data)

    def __len__(self) -> int:
        """Return number of items."""
        return len(self._data)

    def __iter__(self):
        """Iterate over unique keys."""
        seen = set()
        for k, _ in self._data:
            if k not in seen:
                seen.add(k)
                yield k

    def keys(self):
        """Return unique keys."""
        return list(self)

    def items(self) -> List[Tuple[str, Union[str, UploadedFile]]]:
        """Return all key-value pairs."""
        return list(self._data)

    def values(self) -> List[Union[str, UploadedFile]]:
        """Return all values."""
        return [v for _, v in self._data]

    def _uploaded_files(self) -> List[UploadedFile]:
        """Return UploadedFile instances contained in this form."""
        return [v for _, v in self._data if isinstance(v, UploadedFile)]

    def close(self) -> None:
        """
        Close any uploaded files.

        This provides deterministic cleanup for spooled temp files.
        """
        for uploaded in self._uploaded_files():
            try:
                uploaded.close_sync()
            except Exception:
                # Best-effort cleanup; ignore close errors
                pass

    async def aclose(self) -> None:
        """Asynchronously close any uploaded files."""
        for uploaded in self._uploaded_files():
            try:
                await uploaded.close()
            except Exception:
                # Best-effort cleanup; ignore close errors
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()


def parse_content_disposition(header: str) -> Dict[str, Optional[str]]:
    """
    Parse Content-Disposition header value.

    Returns dict with 'name', 'filename' keys (filename may be None).
    """
    result: Dict[str, Optional[str]] = {"name": None, "filename": None}

    # Split on semicolons, handling quoted strings
    parts = []
    current = ""
    in_quotes = False
    i = 0
    while i < len(header):
        char = header[i]
        if char == '"' and (i == 0 or header[i - 1] != "\\"):
            in_quotes = not in_quotes
            current += char
        elif char == ";" and not in_quotes:
            parts.append(current.strip())
            current = ""
        else:
            current += char
        i += 1
    if current.strip():
        parts.append(current.strip())

    for part in parts[1:]:  # Skip the "form-data" part
        if "=" not in part:
            continue

        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()

        # Handle filename* (RFC 5987 encoding)
        if key == "filename*":
            # Format: utf-8''encoded_filename or charset'language'encoded_filename
            if "'" in value:
                parts_star = value.split("'", 2)
                if len(parts_star) >= 3:
                    # charset = parts_star[0]
                    # language = parts_star[1]
                    encoded = parts_star[2]
                    # URL decode
                    try:
                        from urllib.parse import unquote

                        result["filename"] = unquote(encoded, encoding="utf-8")
                    except Exception:
                        pass
            continue

        # Remove quotes if present
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
            # Unescape backslash sequences
            value = value.replace('\\"', '"').replace("\\\\", "\\")

        if key == "name":
            result["name"] = value
        elif key == "filename":
            # Only set if filename* hasn't already set it
            if result["filename"] is None:
                # Strip path components (security)
                # Handle both Unix and Windows paths
                value = value.replace("\\", "/")
                if "/" in value:
                    value = value.rsplit("/", 1)[-1]
                result["filename"] = value

    return result


def parse_content_type(header: str) -> Tuple[str, Dict[str, str]]:
    """
    Parse Content-Type header value.

    Returns (media_type, parameters_dict).
    """
    parts = header.split(";")
    media_type = parts[0].strip().lower()
    params = {}

    for part in parts[1:]:
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            key = key.strip().lower()
            value = value.strip()
            # Remove quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            params[key] = value

    return media_type, params


class MultipartParser:
    """
    Streaming multipart/form-data parser.

    Processes the body chunk by chunk without loading everything into memory.
    """

    # Parser states
    STATE_PREAMBLE = 0
    STATE_HEADER = 1
    STATE_BODY = 2
    STATE_DONE = 3

    def __init__(
        self,
        boundary: bytes,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_request_size: int = DEFAULT_MAX_REQUEST_SIZE,
        max_fields: int = DEFAULT_MAX_FIELDS,
        max_files: int = DEFAULT_MAX_FILES,
        max_parts: Optional[int] = DEFAULT_MAX_PARTS,
        max_field_size: int = DEFAULT_MAX_FIELD_SIZE,
        max_memory_file_size: int = DEFAULT_MAX_MEMORY_FILE_SIZE,
        max_part_header_bytes: int = DEFAULT_MAX_PART_HEADER_BYTES,
        max_part_header_lines: int = DEFAULT_MAX_PART_HEADER_LINES,
        min_free_disk_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES,
        handle_files: bool = False,
    ):
        self.boundary = b"--" + boundary
        self.end_boundary = self.boundary + b"--"
        self.max_file_size = max_file_size
        self.max_request_size = max_request_size
        self.max_fields = max_fields
        self.max_files = max_files
        # If not specified, tie max_parts to the other cardinality limits
        if max_parts is None:
            max_parts = max_fields + max_files
        self.max_parts = max_parts
        self.max_field_size = max_field_size
        self.max_memory_file_size = max_memory_file_size
        self.max_part_header_bytes = max_part_header_bytes
        self.max_part_header_lines = max_part_header_lines
        self.min_free_disk_bytes = min_free_disk_bytes
        self.handle_files = handle_files

        self.state = self.STATE_PREAMBLE
        self.buffer = bytearray()
        self.total_bytes = 0
        self.field_count = 0
        self.file_count = 0
        self.part_count = 0
        self.current_part_size = 0
        self.current_header_bytes = 0
        self.current_header_lines = 0

        self.form_data = FormData()
        self._disk_check_interval_bytes = 1024 * 1024  # 1MB between disk checks
        self._bytes_since_disk_check = 0
        self._tempdir = tempfile.gettempdir()

        # Current part state
        self.current_headers: Dict[str, str] = {}
        self.current_file: Optional[tempfile.SpooledTemporaryFile] = None
        self.current_body = bytearray()
        self.current_name: Optional[str] = None
        self.current_filename: Optional[str] = None
        self.current_content_type: Optional[str] = None

    def feed(self, chunk: bytes) -> None:
        """Feed a chunk of data to the parser."""
        self.total_bytes += len(chunk)
        if self.total_bytes > self.max_request_size:
            raise MultipartParseError("Request body too large")

        self.buffer.extend(chunk)
        self._process()

    def _process(self) -> None:
        """Process buffered data."""
        while True:
            if self.state == self.STATE_PREAMBLE:
                if not self._process_preamble():
                    break
            elif self.state == self.STATE_HEADER:
                if not self._process_header():
                    break
            elif self.state == self.STATE_BODY:
                if not self._process_body():
                    break
            elif self.state == self.STATE_DONE:
                break

    def _process_preamble(self) -> bool:
        """Skip preamble and find first boundary."""
        # Look for boundary (could be at start or after preamble)
        # Try both \r\n prefixed and bare boundary at start
        idx = self.buffer.find(self.boundary)
        if idx == -1:
            # Keep potential partial boundary at end
            keep = len(self.boundary) - 1
            if len(self.buffer) > keep:
                self.buffer = self.buffer[-keep:]
            return False

        # Found boundary, skip to after it
        after_boundary = idx + len(self.boundary)

        # Check for end boundary
        if self.buffer[idx : idx + len(self.end_boundary)] == self.end_boundary:
            self.state = self.STATE_DONE
            return False

        # Skip CRLF or LF after boundary
        if after_boundary < len(self.buffer):
            if self.buffer[after_boundary : after_boundary + 2] == b"\r\n":
                after_boundary += 2
            elif self.buffer[after_boundary : after_boundary + 1] == b"\n":
                after_boundary += 1

        self.buffer = self.buffer[after_boundary:]
        self.state = self.STATE_HEADER
        self.current_headers = {}
        self.current_header_bytes = 0
        self.current_header_lines = 0
        return True

    def _process_header(self) -> bool:
        """Parse part headers."""
        while True:
            # Look for end of header line
            crlf_idx = self.buffer.find(b"\r\n")
            lf_idx = self.buffer.find(b"\n")

            if crlf_idx == -1 and lf_idx == -1:
                # Guard against unbounded header buffering if no newline is ever sent
                if len(self.buffer) > self.max_part_header_bytes:
                    raise MultipartParseError("Part headers too large")
                return False  # Need more data

            # Use whichever comes first
            if crlf_idx != -1 and (lf_idx == -1 or crlf_idx < lf_idx):
                idx = crlf_idx
                line_end_len = 2
            else:
                idx = lf_idx
                line_end_len = 1

            line = self.buffer[:idx]
            self.buffer = self.buffer[idx + line_end_len :]

            self.current_header_lines += 1
            self.current_header_bytes += idx + line_end_len
            if (
                self.current_header_lines > self.max_part_header_lines
                or self.current_header_bytes > self.max_part_header_bytes
            ):
                raise MultipartParseError("Part headers too large")

            if not line:
                # Empty line = end of headers
                self._start_body()
                self.state = self.STATE_BODY
                return True

            # Parse header
            try:
                line_str = line.decode("utf-8", errors="replace")
            except Exception:
                line_str = line.decode("latin-1")

            if ":" in line_str:
                name, _, value = line_str.partition(":")
                self.current_headers[name.strip().lower()] = value.strip()

    def _start_body(self) -> None:
        """Initialize body parsing for current part."""
        self.part_count += 1
        if self.part_count > self.max_parts:
            raise MultipartParseError("Too many parts")

        # Parse Content-Disposition
        cd = self.current_headers.get("content-disposition", "")
        parsed = parse_content_disposition(cd)
        self.current_name = parsed.get("name")
        self.current_filename = parsed.get("filename")
        self.current_content_type = self.current_headers.get("content-type")
        self.current_part_size = 0

        if self.current_filename is not None:
            # It's a file
            self.file_count += 1
            if self.file_count > self.max_files:
                raise MultipartParseError("Too many files")
            if self.handle_files:
                self.current_file = tempfile.SpooledTemporaryFile(
                    max_size=self.max_memory_file_size
                )
            else:
                # Will discard file content
                self.current_file = None
        else:
            # It's a text field
            self.field_count += 1
            if self.field_count > self.max_fields:
                raise MultipartParseError("Too many fields")
            self.current_body = bytearray()
            self.current_file = None

        # Check disk space before allocating a spooled temp file
        if self.current_filename is not None and self.handle_files:
            self._ensure_disk_space()

    def _process_body(self) -> bool:
        """Process body data for current part."""
        # Look for boundary in buffer
        # Need to handle boundary potentially split across chunks

        # The boundary is preceded by \r\n (or \n for lenient parsing)
        search_boundary = b"\r\n" + self.boundary

        idx = self.buffer.find(search_boundary)
        if idx == -1:
            # Try LF-only boundary (lenient)
            search_boundary_lf = b"\n" + self.boundary
            idx = self.buffer.find(search_boundary_lf)
            if idx != -1:
                search_boundary = search_boundary_lf

        if idx == -1:
            # No boundary found yet
            # Keep potential partial boundary at end of buffer
            safe_len = len(self.buffer) - len(search_boundary) - 1
            if safe_len > 0:
                safe_data = self.buffer[:safe_len]
                self._write_body_data(bytes(safe_data))
                self.buffer = self.buffer[safe_len:]
            return False

        # Found boundary - write remaining body data
        body_data = self.buffer[:idx]
        self._write_body_data(bytes(body_data))

        # Move past the boundary
        after_boundary = idx + len(search_boundary)

        # Check for end boundary
        remaining = self.buffer[after_boundary:]
        if remaining.startswith(b"--"):
            # End boundary
            self._finish_part()
            self.state = self.STATE_DONE
            return False

        # Skip CRLF or LF after boundary
        if remaining.startswith(b"\r\n"):
            after_boundary += 2
        elif remaining.startswith(b"\n"):
            after_boundary += 1

        self.buffer = self.buffer[after_boundary:]
        self._finish_part()
        self.state = self.STATE_HEADER
        self.current_headers = {}
        self.current_header_bytes = 0
        self.current_header_lines = 0
        return True

    def _write_body_data(self, data: bytes) -> None:
        """Write data to current part body."""
        if not data:
            return

        self.current_part_size += len(data)

        if self.current_filename is not None:
            # File data
            if self.current_part_size > self.max_file_size:
                raise MultipartParseError("File too large")
            if self.handle_files and self.current_file:
                self._bytes_since_disk_check += len(data)
                if self._bytes_since_disk_check >= self._disk_check_interval_bytes:
                    self._ensure_disk_space()
                    self._bytes_since_disk_check = 0
                self.current_file.write(data)
            # else: discard file data
        else:
            # Field data
            if self.current_part_size > self.max_field_size:
                raise MultipartParseError("Field value too large")
            self.current_body.extend(data)

    def _finish_part(self) -> None:
        """Finalize current part and add to form data."""
        if self.current_name is None:
            return

        if self.current_filename is not None:
            # File
            if self.handle_files and self.current_file:
                self.current_file.seek(0)
                uploaded = UploadedFile(
                    name=self.current_name,
                    filename=self.current_filename,
                    content_type=self.current_content_type,
                    size=self.current_part_size,
                    _file=self.current_file,
                )
                self.form_data.append(self.current_name, uploaded)
            # else: file was discarded
        else:
            # Text field
            try:
                value = bytes(self.current_body).decode("utf-8")
            except UnicodeDecodeError:
                value = bytes(self.current_body).decode("latin-1")
            self.form_data.append(self.current_name, value)

        # Reset part state
        self.current_file = None
        self.current_body = bytearray()
        self.current_name = None
        self.current_filename = None
        self.current_content_type = None

    def finalize(self) -> FormData:
        """Finalize parsing and return form data."""
        # Process any remaining data
        self._process()
        if self.state != self.STATE_DONE:
            raise MultipartParseError(
                "Truncated multipart body (missing closing boundary)"
            )
        return self.form_data

    def _ensure_disk_space(self) -> None:
        """
        Ensure there is enough free space on the temp filesystem.

        This is a best-effort guard against filling the disk with uploads.
        """
        if not self.handle_files:
            return
        if self.min_free_disk_bytes <= 0:
            return
        free_bytes = shutil.disk_usage(self._tempdir).free
        if free_bytes < self.min_free_disk_bytes:
            raise MultipartParseError("Insufficient disk space for uploads")


async def parse_form_data(
    receive: Callable,
    content_type: str,
    files: bool = False,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_request_size: int = DEFAULT_MAX_REQUEST_SIZE,
    max_fields: int = DEFAULT_MAX_FIELDS,
    max_files: int = DEFAULT_MAX_FILES,
    max_parts: Optional[int] = DEFAULT_MAX_PARTS,
    max_field_size: int = DEFAULT_MAX_FIELD_SIZE,
    max_memory_file_size: int = DEFAULT_MAX_MEMORY_FILE_SIZE,
    max_part_header_bytes: int = DEFAULT_MAX_PART_HEADER_BYTES,
    max_part_header_lines: int = DEFAULT_MAX_PART_HEADER_LINES,
    min_free_disk_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES,
) -> FormData:
    """
    Parse form data from an ASGI receive callable.

    Supports both application/x-www-form-urlencoded and multipart/form-data.

    Args:
        receive: ASGI receive callable
        content_type: Content-Type header value
        files: If True, store file uploads; if False, discard them
        max_file_size: Maximum size per file in bytes
        max_request_size: Maximum total request size in bytes
        max_fields: Maximum number of form fields
        max_files: Maximum number of file uploads
        max_field_size: Maximum size of a text field value
        max_memory_file_size: File size threshold before spilling to disk

    Returns:
        FormData object containing parsed fields and files
    """
    media_type, params = parse_content_type(content_type)

    if media_type == "application/x-www-form-urlencoded":
        # Read entire body for URL-encoded forms (they're typically small)
        body = bytearray()
        total = 0
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                raise MultipartParseError("Client disconnected during request body")
            if message_type is not None and message_type != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > max_request_size:
                raise MultipartParseError("Request body too large")
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        form_data = FormData()
        try:
            pairs = parse_qsl(bytes(body).decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            pairs = parse_qsl(bytes(body).decode("latin-1"), keep_blank_values=True)

        for key, value in pairs:
            form_data.append(key, value)

        return form_data

    elif media_type == "multipart/form-data":
        boundary = params.get("boundary")
        if not boundary:
            raise MultipartParseError("Missing boundary in Content-Type")

        parser = MultipartParser(
            boundary=boundary.encode("utf-8"),
            max_file_size=max_file_size,
            max_request_size=max_request_size,
            max_fields=max_fields,
            max_files=max_files,
            max_parts=max_parts,
            max_field_size=max_field_size,
            max_memory_file_size=max_memory_file_size,
            max_part_header_bytes=max_part_header_bytes,
            max_part_header_lines=max_part_header_lines,
            min_free_disk_bytes=min_free_disk_bytes,
            handle_files=files,
        )

        # Stream body through parser
        batch_target = 64 * 1024
        batch = bytearray()

        async def flush_batch() -> None:
            if batch:
                data = bytes(batch)
                batch.clear()
                await asyncio.to_thread(parser.feed, data)

        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                raise MultipartParseError("Client disconnected during request body")
            if message_type is not None and message_type != "http.request":
                continue
            chunk = message.get("body", b"")
            if chunk:
                batch.extend(chunk)
                if len(batch) >= batch_target:
                    await flush_batch()
            if not message.get("more_body", False):
                break

        await flush_batch()
        return await asyncio.to_thread(parser.finalize)

    else:
        raise MultipartParseError(
            f"Unsupported Content-Type: {media_type}. "
            "Expected application/x-www-form-urlencoded or multipart/form-data"
        )
