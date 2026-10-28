"""Serve local static output with the checked-in CSP and an optional labelled snapshot."""

import argparse
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from tools.package_stock_frontend import ROOT


def serve(directory, snapshot=None, label=None, port=8767):
    directory = Path(directory).resolve()
    if snapshot is not None and not label:
        raise ValueError("a snapshot preview requires an explicit label")
    sample = Path(snapshot).read_bytes() if snapshot else None
    if sample is not None:
        if len(sample) > 256 * 1024:
            raise ValueError("preview snapshot exceeds public byte bound")
        json.loads(sample)
    policy = json.loads((ROOT / "dashboard-template.json").read_text())["Resources"]["SecurityHeaders"]["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            route = urlsplit(self.path).path
            if route.startswith("/public/"):
                self.path = self.path[len("/public"):]
                route = urlsplit(self.path).path
            if route == "/stock.json" and sample is not None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "public,max-age=15")
                self.send_header("Content-Length", str(len(sample)))
                self.end_headers()
                self.wfile.write(sample)
                return
            if route == "/stock.html" and label:
                body = (directory / "stock.html").read_text(encoding="utf-8").replace("<head>", '<head><meta name="stock-preview" content="' + escape(label, quote=True) + '">', 1).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def end_headers(self):
            self.send_header("Content-Security-Policy", policy)
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(directory)))
    print(f"Local preview: http://127.0.0.1:{port}/stock.html", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / ".aws-sam" / "stock-site")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--label")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    serve(args.directory, args.snapshot, args.label, args.port)
