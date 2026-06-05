#!/usr/bin/env python3
"""
Vista Platform — Local Proxy
Serves HTML/CSS/JS files and relays Notion API calls with auth headers injected.
The Notion token never appears in any browser-side file.

Usage:
    python proxy.py

Then open:
    http://localhost:8080/index.html
    http://localhost:8080/social-dashboard.html

Routes:
    /notion/social/...   → api.notion.com/v1/... using social_media token
    /notion/personal/... → api.notion.com/v1/... using personal token
    Everything else      → static file served from this folder
"""

import json
import os
import sys
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ── Load config ────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

try:
    with open(CONFIG_PATH, encoding='utf-8') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print(f'ERROR: config.json not found at {CONFIG_PATH}')
    print('Copy config.example.json to config.json and fill in your tokens.')
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f'ERROR: config.json is not valid JSON: {e}')
    print('Make sure all token values are wrapped in "double quotes".')
    sys.exit(1)

NOTION_API_BASE  = 'https://api.notion.com/v1'
NOTION_VERSION   = '2025-09-03'

PORT = CONFIG.get('proxy', {}).get('port', 8080)
BIND = CONFIG.get('proxy', {}).get('bind', '127.0.0.1')

_social_token   = CONFIG.get('notion', {}).get('social_media', {}).get('token', '')
_personal_token = CONFIG.get('notion', {}).get('personal', {}).get('token', '')

def _token_ready(t):
    return bool(t) and not t.startswith('PASTE_') and not t.startswith('REPLACE_')


# ── Proxy handler ──────────────────────────────────────────────────────────────
class VistaProxyHandler(SimpleHTTPRequestHandler):

    # Map URL prefix → token
    NOTION_ROUTES = {
        '/notion/social/':   _social_token,
        '/notion/personal/': _personal_token,
    }

    # ── Request routing ──────────────────────────────────────────────────────

    def do_OPTIONS(self):
        """Preflight CORS — browsers send this before POST requests."""
        self._send_cors_headers(200)
        self.end_headers()

    def do_GET(self):
        route = self._notion_route()
        if route:
            self._proxy_notion(*route)
        else:
            super().do_GET()

    def do_POST(self):
        route = self._notion_route()
        if route:
            self._proxy_notion(*route)
        else:
            self._json_error(404, 'Not found')

    # ── Notion proxy ─────────────────────────────────────────────────────────

    def _notion_route(self):
        """Return (notion_path, token) if this request should be proxied, else None."""
        for prefix, token in self.NOTION_ROUTES.items():
            if self.path.startswith(prefix):
                notion_path = self.path[len(prefix) - 1:]  # keep leading /
                return notion_path, token
        return None

    def _proxy_notion(self, notion_path, token):
        if not _token_ready(token):
            self._json_error(503, 'Notion token not configured for this source. '
                                  'Edit config.json and fill in the token.')
            return

        # Strip any query string for path assembly, preserve it
        url = NOTION_API_BASE + notion_path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        req = urllib.request.Request(
            url,
            data=body,
            method=self.command,
            headers={
                'Authorization':  f'Bearer {token}',
                'Notion-Version': NOTION_VERSION,
                'Content-Type':   'application/json',
                'Accept':         'application/json',
            },
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self._send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.URLError as e:
            self._json_error(502, f'Could not reach Notion API: {e.reason}')

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _send_cors_headers(self, code=None):
        if code is not None:
            self.send_response(code)
        self.send_header('Access-Control-Allow-Origin',  f'http://{BIND}:{PORT}')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json_error(self, code, message):
        body = json.dumps({'error': message}).encode('utf-8')
        self.send_response(code)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Only log Notion proxy calls — suppress noisy static-file requests
        if '/notion/' in self.path:
            print(f'  [notion] {self.command} {self.path}  →  {fmt % args}')


# ── Startup ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Change working directory to the folder containing this script
    # so SimpleHTTPRequestHandler serves files from the right place
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print()
    print('  Vista Platform — Local Proxy')
    print(f'  http://{BIND}:{PORT}/')
    print()
    print(f'  Social Media source  : {"OK - configured" if _token_ready(_social_token)   else "NOT SET - edit config.json"}')
    print(f'  Personal source      : {"OK - configured" if _token_ready(_personal_token) else "NOT SET - edit config.json"}')
    print()
    print('  Press Ctrl+C to stop.')
    print()

    try:
        server = HTTPServer((BIND, PORT), VistaProxyHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Proxy stopped.')
    except OSError as e:
        print(f'\n  ERROR: Could not start server on port {PORT}: {e}')
        print(f'  Try changing "port" in config.json to a free port (e.g. 8081).')
        sys.exit(1)
