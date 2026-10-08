"""
Tests for request.form() multipart form data parsing.

Uses TDD approach - these tests are written first, then implementation follows.
"""

import asyncio
import base64
import json
import threading
from collections import namedtuple

import pytest
from multipart_form_data_conformance import get_tests_dir

from datasette.utils.asgi import BadRequest, Request
from datasette.utils.multipart import MultipartParseError, parse_form_data


@pytest.fixture
def upload_files(monkeypatch):
    from datasette.utils import multipart

    files = []
    original = multipart.tempfile.SpooledTemporaryFile

    def create_file(*args, **kwargs):
        file = original(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(multipart.tempfile, "SpooledTemporaryFile", create_file)
    yield files
    for file in files:
        file.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "file_limit",
        "request_limit",
        "truncated",
        "disconnect",
        "receive_error",
        "cancel",
    ],
)
async def test_failed_upload_closes_completed_and_partial_files(upload_files, failure):
    # Complete one file and begin another, flushing the parser's 64 KiB batch.
    body = (
        b'--boundary\r\nContent-Disposition: form-data; name="one"; filename="one"\r\n\r\n'
        b'first\r\n--boundary\r\nContent-Disposition: form-data; name="two"; filename="two"\r\n\r\n'
        + b"x" * (64 * 1024)
    )
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": body, "more_body": True}
        if failure == "disconnect":
            return {"type": "http.disconnect"}
        if failure == "receive_error":
            raise OSError("receive failed")
        if failure == "cancel":
            raise asyncio.CancelledError
        return {"type": "http.request", "body": b"x" * 100, "more_body": False}

    kwargs = {}
    if failure == "file_limit":
        kwargs["max_file_size"] = 1024
    if failure == "request_limit":
        kwargs["max_request_size"] = len(body)
    error = {
        "receive_error": OSError,
        "cancel": asyncio.CancelledError,
    }.get(failure, MultipartParseError)
    with pytest.raises(error):
        await parse_form_data(
            receive, "multipart/form-data; boundary=boundary", files=True, **kwargs
        )
    assert len(upload_files) == 2
    assert all(file.closed for file in upload_files)


@pytest.mark.asyncio
async def test_unnamed_upload_is_closed(upload_files):
    body = (
        b'--boundary\r\nContent-Disposition: form-data; filename="ignored"\r\n\r\n'
        b"content\r\n--boundary--\r\n"
    )
    form = await parse_form_data(
        make_receive(body), "multipart/form-data; boundary=boundary", files=True
    )
    assert len(form) == 0
    assert len(upload_files) == 1
    assert upload_files[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_error", [False, True])
async def test_cancelled_upload_waits_for_worker(
    upload_files, monkeypatch, worker_error
):
    from datasette.utils.multipart import MultipartParser

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    original_feed = MultipartParser.feed

    def blocking_feed(self, chunk):
        original_feed(self, chunk)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        if worker_error:
            raise MultipartParseError("worker failed")

    monkeypatch.setattr(MultipartParser, "feed", blocking_feed)
    body = (
        b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="file"\r\n\r\n'
        + b"x" * (64 * 1024)
    )
    task = asyncio.create_task(
        parse_form_data(
            make_receive(body), "multipart/form-data; boundary=boundary", files=True
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        # A second cancellation must also leave cleanup waiting for the worker.
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert len(upload_files) == 1
        assert not upload_files[0].closed
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert upload_files[0].closed


def make_receive(body: bytes):
    """Create an async receive callable that yields body in chunks."""
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def make_chunked_receive(body: bytes, chunk_size: int = 64):
    """Create an async receive callable that yields body in small chunks."""
    offset = 0

    async def receive():
        nonlocal offset
        chunk = body[offset : offset + chunk_size]
        offset += chunk_size
        more_body = offset < len(body)
        return {"type": "http.request", "body": chunk, "more_body": more_body}

    return receive


def make_receive_with_noise(body: bytes):
    """
    Create an async receive callable that includes an unexpected ASGI message.

    The parser should ignore the unknown message type and continue.
    """
    messages = [
        {"type": "http.response.start", "status": 200, "headers": []},
        {"type": "http.request", "body": body, "more_body": False},
    ]
    index = 0

    async def receive():
        nonlocal index
        if index >= len(messages):
            return {"type": "http.request", "body": b"", "more_body": False}
        message = messages[index]
        index += 1
        return message

    return receive


def make_disconnect_receive(body: bytes, chunk_size: int = 64):
    """
    Create an async receive callable that disconnects mid-request.

    The parser should raise on the disconnect.
    """
    offset = 0
    disconnected = False

    async def receive():
        nonlocal offset, disconnected
        if disconnected:
            return {"type": "http.disconnect"}
        chunk = body[offset : offset + chunk_size]
        offset += chunk_size
        more_body = offset < len(body)
        if more_body:
            disconnected = True
        return {"type": "http.request", "body": chunk, "more_body": more_body}

    return receive


class TestFormUrlEncoded:
    """Test request.form() with application/x-www-form-urlencoded data."""

    @pytest.mark.asyncio
    async def test_basic_form_fields(self):
        """Basic URL-encoded form should be parseable via request.form()."""
        body = b"username=john&password=secret"
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()

        assert form["username"] == "john"
        assert form["password"] == "secret"

    @pytest.mark.asyncio
    async def test_form_with_multiple_values(self):
        """Multiple values for same key should be accessible via getlist()."""
        body = b"tag=python&tag=web&tag=api"
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()

        assert form["tag"] == "python"  # First value
        assert form.getlist("tag") == ["python", "web", "api"]

    @pytest.mark.asyncio
    async def test_empty_form(self):
        """Empty form should return empty FormData."""
        body = b""
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()

        assert len(form) == 0

    @pytest.mark.asyncio
    async def test_form_with_special_characters(self):
        """URL-encoded special characters should be decoded properly."""
        body = b"message=hello%20world&emoji=%F0%9F%91%8B"
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()

        assert form["message"] == "hello world"
        assert form["emoji"] == "👋"


class TestMultipartBasic:
    """Test request.form() with multipart/form-data (fields only, no files)."""

    @pytest.mark.asyncio
    async def test_single_text_field(self):
        """Single text field in multipart should be parseable."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="username"\r\n'
            b"\r\n"
            b"john_doe\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()

        assert form["username"] == "john_doe"

    @pytest.mark.asyncio
    async def test_multiple_text_fields(self):
        """Multiple text fields in multipart should all be accessible."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="first_name"\r\n'
            b"\r\n"
            b"John\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="last_name"\r\n'
            b"\r\n"
            b"Doe\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()

        assert form["first_name"] == "John"
        assert form["last_name"] == "Doe"

    @pytest.mark.asyncio
    async def test_file_discarded_when_files_false(self):
        """File content should be discarded when files=False (default)."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="title"\r\n'
            b"\r\n"
            b"My Document\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="doc.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"File content here\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="description"\r\n'
            b"\r\n"
            b"A sample document\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()  # files=False is default

        # Text fields should be present
        assert form["title"] == "My Document"
        assert form["description"] == "A sample document"
        # File should NOT be present
        assert "file" not in form

    @pytest.mark.asyncio
    async def test_chunked_body_parsing(self):
        """Multipart should work when body arrives in small chunks."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="username"\r\n'
            b"\r\n"
            b"john_doe\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        # Use small chunks to test streaming parser
        request = Request(scope, make_chunked_receive(body, chunk_size=16))

        form = await request.form()

        assert form["username"] == "john_doe"


class TestMultipartWithFiles:
    """Test request.form(files=True) for file uploads."""

    @pytest.mark.asyncio
    async def test_single_file_upload(self):
        """Single file upload should create UploadedFile object."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="document"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello, World!\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form(files=True)

        uploaded_file = form["document"]
        assert uploaded_file.filename == "test.txt"
        assert uploaded_file.content_type == "text/plain"
        assert await uploaded_file.read() == b"Hello, World!"
        assert uploaded_file.size == 13

    @pytest.mark.asyncio
    async def test_mixed_fields_and_files(self):
        """Mixed form fields and files should all be accessible."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="title"\r\n'
            b"\r\n"
            b"My Document\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="doc.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Document content\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="description"\r\n'
            b"\r\n"
            b"A sample\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form(files=True)

        # Text fields
        assert form["title"] == "My Document"
        assert form["description"] == "A sample"
        # File
        uploaded_file = form["file"]
        assert uploaded_file.filename == "doc.txt"
        assert await uploaded_file.read() == b"Document content"

    @pytest.mark.asyncio
    async def test_multiple_files_same_name(self):
        """Multiple files with same name should be accessible via getlist()."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="files"; filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"File A\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="files"; filename="b.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"File B\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form(files=True)

        files = form.getlist("files")
        assert len(files) == 2
        assert files[0].filename == "a.txt"
        assert files[1].filename == "b.txt"

    @pytest.mark.asyncio
    async def test_large_file_spills_to_disk(self):
        """Files larger than threshold should spill to temp file."""
        boundary = "----TestBoundary123"
        # Create a body larger than the in-memory threshold (1MB)
        large_content = b"x" * (2 * 1024 * 1024)  # 2MB
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="bigfile"; filename="large.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n" + large_content + b"\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form(files=True)

        uploaded_file = form["bigfile"]
        assert uploaded_file.size == len(large_content)
        # Content should still be readable
        content = await uploaded_file.read()
        assert content == large_content

    @pytest.mark.asyncio
    async def test_uploaded_file_seek_and_read(self):
        """UploadedFile should support seek and multiple reads."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello, World!\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form(files=True)
        uploaded_file = form["file"]

        # First read
        content1 = await uploaded_file.read()
        assert content1 == b"Hello, World!"

        # Seek back to start
        await uploaded_file.seek(0)

        # Second read
        content2 = await uploaded_file.read()
        assert content2 == b"Hello, World!"


class TestMultipartCleanup:
    """Test deterministic cleanup of uploaded files."""

    @pytest.mark.asyncio
    async def test_formdata_close_closes_uploaded_files(self):
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))
        form = await request.form(files=True)
        uploaded_file = form["file"]

        form.close()

        with pytest.raises(ValueError):
            await uploaded_file.read()

    @pytest.mark.asyncio
    async def test_formdata_async_context_manager_closes_files(self):
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))
        form = await request.form(files=True)
        uploaded_file = form["file"]

        async with form:
            pass

        with pytest.raises(ValueError):
            await uploaded_file.read()


class TestMultipartEdgeCases:
    """Test edge cases in multipart parsing."""

    @pytest.mark.asyncio
    async def test_empty_file_upload(self):
        """Empty file (filename but no content) should be handled."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="empty.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form(files=True)

        uploaded_file = form["file"]
        assert uploaded_file.filename == "empty.txt"
        assert uploaded_file.size == 0
        assert await uploaded_file.read() == b""

    @pytest.mark.asyncio
    async def test_filename_with_path(self):
        """Filename containing path should extract just the filename."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="C:\\Users\\test\\doc.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"content\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form(files=True)

        # Should extract just the filename, not the full path
        uploaded_file = form["file"]
        assert uploaded_file.filename == "doc.txt"

    @pytest.mark.asyncio
    async def test_missing_content_type_header(self):
        """Missing content-type in request should raise BadRequest."""
        body = b"some body"
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest):
            await request.form()

    @pytest.mark.asyncio
    async def test_invalid_content_type(self):
        """Non-form content-type should raise BadRequest."""
        body = b'{"key": "value"}'
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/json"),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest):
            await request.form()

    @pytest.mark.asyncio
    async def test_missing_boundary(self):
        """Multipart without boundary should raise BadRequest."""
        body = b"some body"
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"multipart/form-data"),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest):
            await request.form()


class TestSecurityLimits:
    """Test security limits on form parsing."""

    @pytest.mark.asyncio
    async def test_max_fields_limit(self):
        """Should reject requests with too many fields."""
        boundary = "----TestBoundary123"
        # Create body with many fields
        parts = []
        for i in range(1001):  # Default max is 1000
            parts.append(
                f"------TestBoundary123\r\n"
                f'Content-Disposition: form-data; name="field{i}"\r\n'
                f"\r\n"
                f"value{i}\r\n"
            )
        parts.append("------TestBoundary123--\r\n")
        body = "".join(parts).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="(?i)too many"):
            await request.form(max_fields=1000)

    @pytest.mark.asyncio
    async def test_max_file_size_limit(self):
        """Should reject files exceeding size limit."""
        boundary = "----TestBoundary123"
        large_content = b"x" * (11 * 1024 * 1024)  # 11MB
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="big.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n" + large_content + b"\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="(?i)file.*too large|too large"):
            await request.form(files=True, max_file_size=10 * 1024 * 1024)

    @pytest.mark.asyncio
    async def test_max_request_size_limit(self):
        """Should reject requests exceeding total size limit."""
        boundary = "----TestBoundary123"
        large_content = b"x" * (6 * 1024 * 1024)  # 6MB
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="big.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n" + large_content + b"\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="(?i)too large|request.*too large"):
            await request.form(files=True, max_request_size=5 * 1024 * 1024)


class TestMultipartStrictnessAndLimits:
    """Tests that enforce stricter ASGI and multipart behaviors."""

    @pytest.mark.asyncio
    async def test_multipart_truncated_body_is_error(self):
        """Truncated multipart without closing boundary should raise."""
        boundary = "----TestBoundary123"
        # Missing the final closing boundary line
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="field"\r\n'
            b"\r\n"
            b"value\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="Truncated multipart body"):
            await request.form()

    @pytest.mark.asyncio
    async def test_disconnect_mid_body_is_error(self):
        """Client disconnect during body streaming should raise."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="field"\r\n'
            b"\r\n"
            b"value\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_disconnect_receive(body, chunk_size=16))

        with pytest.raises(BadRequest, match="disconnected"):
            await request.form()

    @pytest.mark.asyncio
    async def test_unknown_asgi_message_type_is_ignored(self):
        """Unexpected ASGI message types should be ignored."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="field"\r\n'
            b"\r\n"
            b"value\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive_with_noise(body))

        form = await request.form()
        assert form["field"] == "value"

    @pytest.mark.asyncio
    async def test_max_files_enforced_even_when_files_false(self):
        """File count limits should apply even when file handling is disabled."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="f1"; filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"a\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="f2"; filename="b.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"b\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="Too many files"):
            await request.form(files=False, max_files=1)

    @pytest.mark.asyncio
    async def test_max_parts_limit(self):
        """Total part count should be bounded."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="a"\r\n'
            b"\r\n"
            b"1\r\n"
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="b"\r\n'
            b"\r\n"
            b"2\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="Too many parts"):
            await request.form(max_parts=1)

    @pytest.mark.asyncio
    async def test_max_file_size_enforced_even_when_files_false(self):
        """File size limits should apply even when file handling is disabled."""
        boundary = "----TestBoundary123"
        big_content = b"x" * 2048
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="big.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n" + big_content + b"\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="File too large"):
            await request.form(files=False, max_file_size=1024)

    @pytest.mark.asyncio
    async def test_part_header_limits(self):
        """Overly large part headers should be rejected."""
        boundary = "----TestBoundary123"
        huge_header_value = "x" * 5000
        body = (
            b"------TestBoundary123\r\n"
            + f'Content-Disposition: form-data; name="field"; foo="{huge_header_value}"\r\n'.encode()
            + b"\r\n"
            + b"value\r\n"
            + b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="headers too large"):
            await request.form(max_part_header_bytes=1024)

    @pytest.mark.asyncio
    async def test_insufficient_disk_space_rejects_upload(self, monkeypatch):
        """Uploads should be rejected when free disk is below the floor."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }

        DiskUsage = namedtuple("DiskUsage", ("total", "used", "free"))
        monkeypatch.setattr(
            "datasette.utils.multipart.shutil.disk_usage",
            lambda path: DiskUsage(total=100, used=95, free=5),
        )

        request = Request(scope, make_receive(body))
        with pytest.raises(BadRequest, match="Insufficient disk space"):
            await request.form(files=True, min_free_disk_bytes=50)

    @pytest.mark.asyncio
    async def test_low_disk_space_does_not_block_field_only_forms(self, monkeypatch):
        """Low disk space should not reject multipart forms with no file parts."""
        boundary = "----TestBoundary123"
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="field"\r\n'
            b"\r\n"
            b"value\r\n"
            b"------TestBoundary123--\r\n"
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }

        DiskUsage = namedtuple("DiskUsage", ("total", "used", "free"))
        monkeypatch.setattr(
            "datasette.utils.multipart.shutil.disk_usage",
            lambda path: DiskUsage(total=100, used=99, free=1),
        )

        request = Request(scope, make_receive(body))
        form = await request.form(files=True, min_free_disk_bytes=50)
        assert form["field"] == "value"

    @pytest.mark.asyncio
    async def test_headers_without_newline_hit_header_byte_limit(self):
        """Headers that never terminate should still hit the header byte limit."""
        boundary = "----TestBoundary123"
        huge = b"x" * 5000
        # No CRLF is included after the header line
        body = (
            b"------TestBoundary123\r\n"
            b'Content-Disposition: form-data; name="field"; foo="' + huge + b'"'
        )
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            ],
        }
        request = Request(scope, make_receive(body))

        with pytest.raises(BadRequest, match="headers too large"):
            await request.form(max_part_header_bytes=1024)


class TestFormDataLenSemantics:
    """Test that FormData.__len__ reflects number of items, not unique keys."""

    @pytest.mark.asyncio
    async def test_len_counts_items(self):
        body = b"tag=python&tag=web&tag=api"
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
        }
        request = Request(scope, make_receive(body))

        form = await request.form()
        assert len(form) == 3


# Conformance test suite using multipart-form-data-conformance

# Tests where our parser intentionally differs from strict spec for security/practicality
# Our parser sanitizes filenames (strips paths) while the conformance suite expects raw
FILENAME_SANITIZATION_TESTS = {
    "026-filename-with-backslash",  # We preserve backslashes but they test expects raw
    "029-filename-path-traversal",  # We strip path components for security
}

# Tests for optional/lenient features we don't implement
OPTIONAL_TESTS = {
    "085-header-folding",  # Obsolete header folding feature
}

# Tests for malformed input where we're lenient instead of erroring
LENIENT_PARSING_TESTS = {
    "203-missing-content-disposition",
    "204-invalid-content-disposition",
}


def load_conformance_test_cases():
    """Load all test cases from multipart-form-data-conformance."""
    tests_dir = get_tests_dir()
    test_cases = []

    for category_dir in sorted(tests_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        for test_dir in sorted(category_dir.iterdir()):
            if not test_dir.is_dir():
                continue
            test_json = test_dir / "test.json"
            headers_json = test_dir / "headers.json"
            input_raw = test_dir / "input.raw"

            if not all(f.exists() for f in [test_json, headers_json, input_raw]):
                continue

            with open(test_json) as f:
                test_spec = json.load(f)
            with open(headers_json) as f:
                headers = json.load(f)
            with open(input_raw, "rb") as f:
                body = f.read()

            test_id = test_spec["id"]

            # Add marks for tests we handle differently
            marks = []
            if test_id in FILENAME_SANITIZATION_TESTS:
                marks.append(
                    pytest.mark.xfail(reason="Parser sanitizes filenames for security")
                )
            elif test_id in OPTIONAL_TESTS:
                marks.append(
                    pytest.mark.xfail(reason="Optional feature not implemented")
                )
            elif test_id in LENIENT_PARSING_TESTS:
                marks.append(
                    pytest.mark.xfail(reason="Parser is lenient with malformed input")
                )

            test_cases.append(
                pytest.param(
                    test_spec,
                    headers,
                    body,
                    id=test_id,
                    marks=marks,
                )
            )

    return test_cases


CONFORMANCE_TEST_CASES = load_conformance_test_cases()


@pytest.mark.parametrize("test_spec,headers,body", CONFORMANCE_TEST_CASES)
@pytest.mark.asyncio
async def test_conformance(test_spec, headers, body):
    """
    Run conformance test cases from multipart-form-data-conformance.

    Each test case specifies:
    - headers: HTTP headers including Content-Type with boundary
    - body: Raw multipart body bytes
    - expected: Expected parse result (valid/invalid, parts list)
    """
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }
    request = Request(scope, make_receive(body))

    expected = test_spec["expected"]

    if not expected["valid"]:
        # Should raise an error for invalid input
        with pytest.raises((BadRequest, ValueError)):
            await request.form(files=True)
        return

    async with await request.form(files=True) as form:
        # Verify each expected part
        for i, expected_part in enumerate(expected["parts"]):
            name = expected_part["name"]

            # Get value(s) for this name
            values = form.getlist(name)

            # Find the value at the correct index for this name
            # (handles multiple values with same name)
            same_name_count = sum(1 for p in expected["parts"][:i] if p["name"] == name)

            if same_name_count >= len(values):
                pytest.fail(
                    f"Expected part {name} at index {same_name_count} but only {len(values)} found"
                )

            value = values[same_name_count]

            # Determine expected content
            if "body_base64" in expected_part:
                expected_content = base64.b64decode(expected_part["body_base64"])
            elif "body_text" in expected_part:
                expected_content = expected_part["body_text"].encode("utf-8")
            else:
                expected_content = None

            # Check for file vs field
            # A part is a file if it has a filename OR filename_star
            is_file = (
                expected_part.get("filename") is not None
                or expected_part.get("filename_star") is not None
            )

            if is_file:
                # It's a file
                assert hasattr(value, "filename"), f"Expected file for {name}"

                # Check filename - use filename_star if present, else filename
                expected_filename = expected_part.get(
                    "filename_star"
                ) or expected_part.get("filename")
                if expected_filename:
                    assert (
                        value.filename == expected_filename
                    ), f"Filename mismatch: expected {expected_filename!r}, got {value.filename!r}"

                if expected_part.get("content_type"):
                    assert value.content_type == expected_part["content_type"]

                content = await value.read()
                assert (
                    len(content) == expected_part["body_size"]
                ), f"Size mismatch: expected {expected_part['body_size']}, got {len(content)}"
                if expected_content is not None:
                    assert content == expected_content
            else:
                # It's a text field
                if hasattr(value, "filename"):
                    pytest.fail(f"Expected text field for {name}, got file")

                if expected_content is not None:
                    # For text fields, value is a string
                    try:
                        expected_text = expected_content.decode("utf-8")
                    except UnicodeDecodeError:
                        expected_text = expected_content.decode("latin-1")
                    assert (
                        value == expected_text
                    ), f"Value mismatch: expected {expected_text!r}, got {value!r}"
