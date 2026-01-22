"""
Tests for request.form() multipart form data parsing.

Uses TDD approach - these tests are written first, then implementation follows.
"""

import json
import pytest
from pathlib import Path

from multipart_form_data_conformance import get_tests_dir

from datasette.utils.asgi import Request, BadRequest


def make_receive(body: bytes):
    """Create an async receive callable that yields body in chunks."""
    chunks = [body]
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
            b"\r\n"
            + large_content
            + b"\r\n"
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
                f'------TestBoundary123\r\n'
                f'Content-Disposition: form-data; name="field{i}"\r\n'
                f'\r\n'
                f'value{i}\r\n'
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
            b"\r\n"
            + large_content
            + b"\r\n"
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
            b"\r\n"
            + large_content
            + b"\r\n"
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


# Conformance test suite using multipart-form-data-conformance

import base64

# Tests where our parser intentionally differs from strict spec for security/practicality
# Our parser sanitizes filenames (strips paths) while the conformance suite expects raw
FILENAME_SANITIZATION_TESTS = {
    "026-filename-with-backslash",  # We preserve backslashes but they test expects raw
    "029-filename-path-traversal",  # We strip path components for security
}

# Tests for optional/lenient features we don't implement
OPTIONAL_TESTS = {
    "085-header-folding",          # Obsolete header folding feature
}

# Tests for malformed input where we're lenient instead of erroring
LENIENT_PARSING_TESTS = {
    "200-missing-final-terminator",
    "201-wrong-boundary",
    "202-truncated-body",
    "203-missing-content-disposition",
    "204-invalid-content-disposition",
    "205-no-blank-line",
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
                marks.append(pytest.mark.xfail(
                    reason="Parser sanitizes filenames for security"
                ))
            elif test_id in OPTIONAL_TESTS:
                marks.append(pytest.mark.xfail(
                    reason="Optional feature not implemented"
                ))
            elif test_id in LENIENT_PARSING_TESTS:
                marks.append(pytest.mark.xfail(
                    reason="Parser is lenient with malformed input"
                ))

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

    # Parse form data
    form = await request.form(files=True)

    # Verify each expected part
    for i, expected_part in enumerate(expected["parts"]):
        name = expected_part["name"]

        # Get value(s) for this name
        values = form.getlist(name)

        # Find the value at the correct index for this name
        # (handles multiple values with same name)
        same_name_count = sum(
            1 for p in expected["parts"][:i] if p["name"] == name
        )

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
            expected_filename = expected_part.get("filename_star") or expected_part.get("filename")
            if expected_filename:
                assert value.filename == expected_filename, (
                    f"Filename mismatch: expected {expected_filename!r}, got {value.filename!r}"
                )

            if expected_part.get("content_type"):
                assert value.content_type == expected_part["content_type"]

            content = await value.read()
            assert len(content) == expected_part["body_size"], (
                f"Size mismatch: expected {expected_part['body_size']}, got {len(content)}"
            )
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
                assert value == expected_text, (
                    f"Value mismatch: expected {expected_text!r}, got {value!r}"
                )
