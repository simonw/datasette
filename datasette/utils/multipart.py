"""
Streaming multipart/form-data parser for ASGI applications.

Supports:
- Streaming parsing without buffering entire body in memory
- Files spill to disk above configurable threshold
- Security limits on request size, file size, field count
- Both multipart/form-data and application/x-www-form-urlencoded
"""

import re
import tempfile
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)
from urllib.parse import parse_qsl
import os


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
        return self._file.read(size)

    async def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position in file."""
        return self._file.seek(offset, whence)

    async def close(self) -> None:
        """Close the underlying file."""
        self._file.close()

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
        """Return number of unique keys."""
        return len(set(k for k, _ in self._data))

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
        max_file_size: int = 50 * 1024 * 1024,  # 50MB default
        max_request_size: int = 100 * 1024 * 1024,  # 100MB default
        max_fields: int = 1000,
        max_files: int = 100,
        max_field_size: int = 100 * 1024,  # 100KB for text fields
        max_memory_file_size: int = 1024 * 1024,  # 1MB before spill to disk
        handle_files: bool = False,
    ):
        self.boundary = b"--" + boundary
        self.end_boundary = self.boundary + b"--"
        self.max_file_size = max_file_size
        self.max_request_size = max_request_size
        self.max_fields = max_fields
        self.max_files = max_files
        self.max_field_size = max_field_size
        self.max_memory_file_size = max_memory_file_size
        self.handle_files = handle_files

        self.state = self.STATE_PREAMBLE
        self.buffer = b""
        self.total_bytes = 0
        self.field_count = 0
        self.file_count = 0
        self.current_part_size = 0

        self.form_data = FormData()

        # Current part state
        self.current_headers: Dict[str, str] = {}
        self.current_file: Optional[tempfile.SpooledTemporaryFile] = None
        self.current_body = b""
        self.current_name: Optional[str] = None
        self.current_filename: Optional[str] = None
        self.current_content_type: Optional[str] = None

    def feed(self, chunk: bytes) -> None:
        """Feed a chunk of data to the parser."""
        self.total_bytes += len(chunk)
        if self.total_bytes > self.max_request_size:
            raise MultipartParseError("Request body too large")

        self.buffer += chunk
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
        return True

    def _process_header(self) -> bool:
        """Parse part headers."""
        while True:
            # Look for end of header line
            crlf_idx = self.buffer.find(b"\r\n")
            lf_idx = self.buffer.find(b"\n")

            if crlf_idx == -1 and lf_idx == -1:
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
        # Parse Content-Disposition
        cd = self.current_headers.get("content-disposition", "")
        parsed = parse_content_disposition(cd)
        self.current_name = parsed.get("name")
        self.current_filename = parsed.get("filename")
        self.current_content_type = self.current_headers.get("content-type")
        self.current_part_size = 0

        if self.current_filename is not None:
            # It's a file
            if self.handle_files:
                self.file_count += 1
                if self.file_count > self.max_files:
                    raise MultipartParseError("Too many files")
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
            self.current_body = b""
            self.current_file = None

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
                self._write_body_data(safe_data)
                self.buffer = self.buffer[safe_len:]
            return False

        # Found boundary - write remaining body data
        body_data = self.buffer[:idx]
        self._write_body_data(body_data)

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
        return True

    def _write_body_data(self, data: bytes) -> None:
        """Write data to current part body."""
        if not data:
            return

        self.current_part_size += len(data)

        if self.current_filename is not None:
            # File data
            if self.handle_files:
                if self.current_part_size > self.max_file_size:
                    raise MultipartParseError("File too large")
                if self.current_file:
                    self.current_file.write(data)
            # else: discard file data
        else:
            # Field data
            if self.current_part_size > self.max_field_size:
                raise MultipartParseError("Field value too large")
            self.current_body += data

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
                value = self.current_body.decode("utf-8")
            except UnicodeDecodeError:
                value = self.current_body.decode("latin-1")
            self.form_data.append(self.current_name, value)

        # Reset part state
        self.current_file = None
        self.current_body = b""
        self.current_name = None
        self.current_filename = None
        self.current_content_type = None

    def finalize(self) -> FormData:
        """Finalize parsing and return form data."""
        # Process any remaining data
        self._process()
        return self.form_data


async def parse_form_data(
    receive: Callable,
    content_type: str,
    files: bool = False,
    max_file_size: int = 50 * 1024 * 1024,
    max_request_size: int = 100 * 1024 * 1024,
    max_fields: int = 1000,
    max_files: int = 100,
    max_field_size: int = 100 * 1024,
    max_memory_file_size: int = 1024 * 1024,
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
        body = b""
        total = 0
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > max_request_size:
                raise MultipartParseError("Request body too large")
            body += chunk
            if not message.get("more_body", False):
                break

        form_data = FormData()
        try:
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            pairs = parse_qsl(body.decode("latin-1"), keep_blank_values=True)

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
            max_field_size=max_field_size,
            max_memory_file_size=max_memory_file_size,
            handle_files=files,
        )

        # Stream body through parser
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            if chunk:
                parser.feed(chunk)
            if not message.get("more_body", False):
                break

        return parser.finalize()

    else:
        raise MultipartParseError(
            f"Unsupported Content-Type: {media_type}. "
            "Expected application/x-www-form-urlencoded or multipart/form-data"
        )
