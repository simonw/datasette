"""
A local HTTP server for trying ``datasette URL`` without the internet.

    uv run python prototypes/data_csv/cli_http_server.py DIRECTORY PORT [--rate BYTES_PER_SECOND] [--no-length]

Serves the files in DIRECTORY. ``--rate`` throttles every response, so there
is time to watch the progress bar or press Ctrl+C. ``--no-length`` leaves
out Content-Length. ``/redirect/NAME`` redirects to ``/NAME``.
"""

import argparse
import http.server
import os
import time
import urllib.parse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("port", type=int)
    parser.add_argument("--rate", type=int, default=0)
    parser.add_argument("--no-length", action="store_true")
    args = parser.parse_args()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            if path.startswith("/redirect/"):
                self.send_response(302)
                self.send_header("Location", "/" + path[len("/redirect/") :])
                self.end_headers()
                return
            full = os.path.join(args.directory, path.lstrip("/"))
            if not os.path.isfile(full):
                self.send_error(404)
                return
            self.send_response(200)
            if not args.no_length:
                self.send_header("Content-Length", str(os.path.getsize(full)))
            self.end_headers()
            chunk = max(args.rate // 10, 64 * 1024) if args.rate else 1024 * 1024
            with open(full, "rb") as fp:
                while True:
                    data = fp.read(chunk)
                    if not data:
                        break
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    if args.rate:
                        time.sleep(len(data) / args.rate)

        def log_message(self, format, *a):
            print(format % a, flush=True)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Serving {args.directory} on http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
