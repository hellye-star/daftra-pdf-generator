"""
Shared support for the PO Generator tests.

The PO tests exercise the real PO storage API (po_storage_api) over HTTP, but
never import proxy.py: importing it loads the real config.json (tokens) and
opens the proxy's log file. PoTestHandler routes /api/po/* exactly as
proxy.py does (handle_get / handle_post / handle_put) and answers 404 for
everything else, so the tests run in any checkout — including an isolated
copy without config.json — with no tokens, no live supplier calls and only
the temporary PO storage each test module sets (VISTA_PO_DATA_DIR).

proxy_po_routes() checks proxy.py's routing from its SOURCE (not by
importing it), so a change that stops the proxy from serving the PO API is
still caught.
"""
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import po_storage_api

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PoTestHandler(BaseHTTPRequestHandler):
    def _route(self, fn):
        if self.path.startswith('/api/po/'):
            return fn(self)
        self.send_response(404)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        self._route(po_storage_api.handle_get)

    def do_POST(self):
        self._route(po_storage_api.handle_post)

    def do_PUT(self):
        self._route(po_storage_api.handle_put)

    def _not_allowed(self):                # proxy.py answers these with 405 (see _block_daftra_write)
        body = b'{"error": "Method not allowed."}'
        self.send_response(405)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_DELETE = _not_allowed
    do_PATCH = _not_allowed

    def log_message(self, *args):          # keep test output clean
        pass


def start_server():
    """(server, port) — a PO API server on an ephemeral loopback port."""
    srv = ThreadingHTTPServer(('127.0.0.1', 0), PoTestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def proxy_po_routes():
    """{'GET': bool, 'POST': bool, 'PUT': bool}: does proxy.py hand /api/po/*
    to po_storage_api for that method? Read from the source; nothing is imported."""
    src = open(os.path.join(REPO, 'proxy.py'), encoding='utf-8').read()
    out = {}
    for method, fn in (('GET', 'handle_get'), ('POST', 'handle_post'), ('PUT', 'handle_put')):
        m = re.search(r'def do_' + method + r'\(self\):(.*?)(?=\n    def |\Z)', src, re.S)
        body = m.group(1) if m else ''
        out[method] = bool(re.search(r"self\.path\.startswith\('/api/po/'\).{0,200}?po_storage_api\." + fn + r"\(self\)", body, re.S))
    # DELETE / PATCH must never reach the PO API (the proxy answers them with 405)
    for method in ('DELETE', 'PATCH'):
        m = re.search(r'def do_' + method + r'\(self\):(.*?)(?=\n    def |\Z)', src, re.S)
        out[method + '_blocked'] = bool(m) and '/api/po/' not in m.group(1) and '_block_daftra_write' in m.group(1)
    return out
