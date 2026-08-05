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

import base64 as _b64
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote as _urllib_parse_quote

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

# ── Purchasing Invoices local file store ───────────────────────────────────────
PURCHASE_INVOICE_DIR  = r"C:\Users\YousefMokaled\Documents\Vista United Co\purchasing invoices"
PURCHASE_ALLOWED_EXTS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}

PORT = CONFIG.get('proxy', {}).get('port', 8080)
BIND = CONFIG.get('proxy', {}).get('bind', '127.0.0.1')

_social_token   = CONFIG.get('notion', {}).get('social_media', {}).get('token', '')
_personal_token = CONFIG.get('notion', {}).get('personal', {}).get('token', '')

_daftra_subdomain = CONFIG.get('daftra', {}).get('subdomain', '')
_daftra_api_key   = CONFIG.get('daftra', {}).get('api_key', '')

_ga4_cfg         = CONFIG.get('marketing_apis', {}).get('google', {}).get('ga4', {})
_ga4_property_id = _ga4_cfg.get('property_id', '')
_ga4_creds_path  = _ga4_cfg.get('credentials_json_path', '')

_gads_cfg           = CONFIG.get('marketing_apis', {}).get('google', {}).get('ads', {})
_gads_oauth_path    = _gads_cfg.get('oauth_json_path', '')
_gads_customer_id   = _gads_cfg.get('customer_id', '')
_gads_login_cust_id = _gads_cfg.get('login_customer_id', '')

_meta_cfg        = CONFIG.get('marketing_apis', {}).get('meta', {})
_meta_token_path = _meta_cfg.get('token_path', '')
_meta_ig_acct_id = _meta_cfg.get('instagram_business_account_id', '')
_meta_page_id    = _meta_cfg.get('page_id', '')

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
        if self.path.startswith('/purchasing-invoices/'):
            self._handle_purchasing_get()
        elif self.path.startswith('/api/setup/'):
            self._handle_setup_get()
        elif self.path.startswith('/api/ga4/'):
            self._handle_ga4_get()
        elif self.path.startswith('/api/google-ads/'):
            self._handle_gads_get()
        elif self.path.startswith('/api/meta/'):
            self._handle_meta_get()
        elif self.path.startswith('/api/sqi/'):
            import sqi_storage_api
            sqi_storage_api.handle_get(self)
        elif self.path.startswith('/daftra/'):
            self._proxy_daftra()
        else:
            route = self._notion_route()
            if route:
                self._proxy_notion(*route)
            else:
                self._serving_static = True
                super().do_GET()

    def do_HEAD(self):
        self._serving_static = True
        super().do_HEAD()

    def end_headers(self):
        """Inject no-store for static file responses so browsers never cache stale HTML/JS."""
        if getattr(self, '_serving_static', False):
            self._serving_static = False
            self.send_header('Cache-Control', 'no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
        super().end_headers()

    def do_POST(self):
        if self.path.startswith('/purchasing-invoices/combine'):
            self._combine_purchasing()
        elif self.path.startswith('/purchasing-invoices/upload'):
            self._upload_purchasing()
        elif self.path.startswith('/api/setup/'):
            self._handle_setup_post()
        elif self.path.startswith('/api/ga4/'):
            self._json_error(405, 'Method not allowed. GA4 API proxy is read-only (GET only).')
        elif self.path.startswith('/api/google-ads/'):
            self._json_error(405, 'Method not allowed. Google Ads API proxy is read-only (GET only).')
        elif self.path.startswith('/api/meta/'):
            self._json_error(405, 'Method not allowed. Meta API proxy is read-only (GET only).')
        elif self.path.startswith('/api/sqi/'):
            import sqi_storage_api
            sqi_storage_api.handle_post(self)
        elif self.path.startswith('/daftra/'):
            self._block_daftra_write()
        else:
            route = self._notion_route()
            if route:
                self._proxy_notion(*route)
            else:
                self._json_error(404, 'Not found')

    # ── Daftra proxy (read-only GET only) ────────────────────────────────────

    def do_DELETE(self):
        if self.path.startswith('/api/sqi/'):
            import sqi_storage_api
            sqi_storage_api.handle_delete(self)
        else:
            self._block_daftra_write()
    def do_PUT(self):
        if self.path.startswith('/api/sqi/'):
            import sqi_storage_api
            sqi_storage_api.handle_put(self)
        else:
            self._block_daftra_write()
    def do_PATCH(self):
        if self.path.startswith('/daftra/'):
            self._block_daftra_write()
        else:
            route = self._notion_route()
            if route:
                self._proxy_notion(*route)
            else:
                self._block_daftra_write()

    def _block_daftra_write(self):
        if self.path.startswith('/daftra/'):
            self._json_error(405, 'Method not allowed. The Daftra proxy is read-only (GET only).')
        else:
            self._json_error(405, 'Method not allowed.')

    def _proxy_daftra(self):
        if not _token_ready(_daftra_subdomain) or not _token_ready(_daftra_api_key):
            self._json_error(503, 'Daftra credentials not configured. '
                                  'Edit config.json and set daftra.subdomain and daftra.api_key.')
            return

        # Strip /daftra prefix; preserve path + query string verbatim
        daftra_path = self.path[len('/daftra'):]   # e.g. /invoices.json?limit=1&page=1
        url = f'https://{_daftra_subdomain}.daftra.com/api2{daftra_path}'

        req = urllib.request.Request(
            url,
            method='GET',
            headers={
                'APIKEY':  _daftra_api_key,
                'Accept':  'application/json',
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
            self._json_error(502, f'Could not reach Daftra API: {e.reason}')

    # ── Purchasing Invoices (local file routes) ──────────────────────────────

    def _safe_purchase_path(self, rel_path):
        """Validate rel_path and return absolute path inside PURCHASE_INVOICE_DIR.
        Returns None if the path is invalid, contains traversal, or escapes the base."""
        if not rel_path or '..' in rel_path:
            return None
        # Normalise separators; reject anything that looks absolute
        rel_path = rel_path.replace('/', os.sep).replace('\\', os.sep)
        if rel_path.startswith(os.sep) or (len(rel_path) > 1 and rel_path[1] == ':'):
            return None
        abs_path = os.path.normpath(os.path.join(PURCHASE_INVOICE_DIR, rel_path))
        base_norm = os.path.normpath(PURCHASE_INVOICE_DIR)
        if abs_path != base_norm and not abs_path.startswith(base_norm + os.sep):
            return None
        return abs_path

    def _handle_purchasing_get(self):
        parsed = urlparse(self.path)
        if parsed.path == '/purchasing-invoices/list':
            self._list_purchasing()
        elif parsed.path == '/purchasing-invoices/file':
            params = parse_qs(parsed.query)
            rel_path = unquote(params.get('path', [''])[0])
            self._serve_purchasing_file(rel_path)
        else:
            self._json_error(404, 'Not found')

    def _list_purchasing(self):
        if not os.path.isdir(PURCHASE_INVOICE_DIR):
            self._json_error(404, 'Purchasing invoices directory not found on this machine.')
            return
        files = []
        for root, dirs, filenames in os.walk(PURCHASE_INVOICE_DIR):
            # Skip hidden directories
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            rel_folder = os.path.relpath(root, PURCHASE_INVOICE_DIR)
            if rel_folder == '.':
                rel_folder = ''
            for fname in sorted(filenames):
                if fname.startswith('.') or fname.lower() == 'desktop.ini':
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext not in PURCHASE_ALLOWED_EXTS:
                    continue
                abs_path = os.path.join(root, fname)
                rel_path = (rel_folder + '/' + fname) if rel_folder else fname
                rel_path = rel_path.replace(os.sep, '/')
                try:
                    stat  = os.stat(abs_path)
                    size  = stat.st_size
                    modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                except OSError:
                    size, modified = 0, ''
                files.append({
                    'folder':       rel_folder if rel_folder else '(root)',
                    'name':         fname,
                    'relativePath': rel_path,
                    'type':         ext.lstrip('.'),
                    'size':         size,
                    'modified':     modified,
                })
        body = json.dumps({'files': files}).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
        self.wfile.write(body)

    def _serve_purchasing_file(self, rel_path):
        # ── Phase 1: validation — all errors here can still send a JSON error
        #    response because no response headers have been written yet.
        abs_path = self._safe_purchase_path(rel_path)
        if not abs_path:
            self._json_error(400, 'Invalid path.')
            return
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in PURCHASE_ALLOWED_EXTS:
            self._json_error(403, 'File type not allowed.')
            return
        if not os.path.isfile(abs_path):
            self._json_error(404, 'File not found.')
            return

        mime_map = {
            '.pdf':  'application/pdf',
            '.png':  'image/png',
            '.jpg':  'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
        }
        content_type = mime_map.get(ext, 'application/octet-stream')

        # Get file size for Content-Length before opening (avoids reading all into memory)
        try:
            file_size = os.path.getsize(abs_path)
        except OSError as e:
            self._json_error(500, f'Could not stat file: {e}')
            return

        # ── Phase 2: headers — response has started; never call _json_error below here.
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(file_size))
        # RFC 5987 — safe for Arabic/Unicode filenames.
        # HTTP headers are latin-1; raw Unicode in filename= crashes.
        # Use ASCII-safe fallback + filename*=UTF-8'' percent-encoded original.
        raw_name  = os.path.basename(abs_path)
        safe_name = raw_name.encode('ascii', errors='replace').decode('ascii').replace('"', '_')
        utf8_name = _urllib_parse_quote(raw_name.encode('utf-8'), safe='')
        self.send_header('Content-Disposition',
                         f'inline; filename="{safe_name}"; filename*=UTF-8\'\'{utf8_name}')
        self.end_headers()

        # ── Phase 3: streaming — chunk file to avoid large memory allocation.
        #    Connection errors here mean the browser/iframe closed the request early
        #    (e.g. PDF viewer navigated away, tab closed).  Log and return silently;
        #    do NOT attempt to send a JSON error — headers are already sent.
        try:
            with open(abs_path, 'rb') as f:
                while True:
                    chunk = f.read(64 * 1024)   # 64 KB chunks
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError) as e:
            # Client cancelled — normal for PDF viewers when navigating away
            print(f'  [purch]  client closed connection while streaming {raw_name!r}: {e}')
        except OSError as e:
            # Unexpected I/O error during streaming — log but cannot send error response
            print(f'  [purch]  OSError streaming {raw_name!r}: {e}')

    def _combine_purchasing(self):
        """Combine a list of purchasing files (PDFs and/or images) into one PDF.

        Requires PyMuPDF (fitz).  If not installed, returns 503 with install
        instructions so the front-end can display a clear error message.

        Request body (JSON):
            { "paths": ["folder/file1.pdf", "folder/file2.pdf", ...] }

        Response (success):
            Content-Type: application/pdf  — the combined PDF as a binary stream.

        Response (error):
            Content-Type: application/json — { "error": "..." }
        """
        # ── Check fitz availability ──────────────────────────────────────────
        try:
            import fitz  # PyMuPDF
        except ImportError:
            self._json_error(503,
                'PyMuPDF is not installed. '
                'To enable combined PDF export, run: pip install PyMuPDF '
                'then restart proxy.py.')
            return

        # ── Read + parse request body ────────────────────────────────────────
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 50 * 1024 * 1024:
            self._json_error(413, 'Request body too large.')
            return
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._json_error(400, 'Invalid JSON body.')
            return

        paths = payload.get('paths', [])
        if not paths or not isinstance(paths, list):
            self._json_error(400, 'Missing or invalid "paths" list.')
            return
        if len(paths) > 200:
            self._json_error(400, 'Too many files (max 200).')
            return

        # ── Validate every path before touching fitz ─────────────────────────
        abs_paths = []
        for rel_path in paths:
            if not isinstance(rel_path, str):
                self._json_error(400, 'All paths must be strings.')
                return
            abs_path = self._safe_purchase_path(rel_path)
            if not abs_path:
                self._json_error(400, f'Invalid path: {rel_path!r}')
                return
            ext = os.path.splitext(abs_path)[1].lower()
            if ext not in PURCHASE_ALLOWED_EXTS:
                self._json_error(403, f'File type not allowed: {ext!r}')
                return
            if not os.path.isfile(abs_path):
                self._json_error(404, f'File not found: {rel_path!r}')
                return
            abs_paths.append((abs_path, ext))

        # ── Combine with fitz ────────────────────────────────────────────────
        # Filenames can contain any Unicode (Arabic/CJK/emoji, etc.), but this
        # console's codepage (e.g. Windows cp1252) often can't encode them —
        # print()'ing a raw filename then raises UnicodeEncodeError, which
        # previously crashed the whole request thread (the except-block's own
        # error-logging print below was not itself guarded, so it raised the
        # same error uncaught), aborting the connection before any HTTP
        # response was sent — the browser then reports a bare "Failed to
        # fetch" with no indication this was a logging-only failure. Routing
        # every filename (and the exception text) through _log_safe() before
        # printing fixes this — it only affects these diagnostic log lines,
        # never the actual PDF bytes/merge logic above.
        def _log_safe(s):
            return s.encode('ascii', errors='replace').decode('ascii')

        total_files = len(abs_paths)
        print(f'  [purch]  combine: merging {total_files} file(s)…')
        combined = fitz.open()
        errors   = []
        for idx, (abs_path, ext) in enumerate(abs_paths, 1):
            fname = os.path.basename(abs_path)
            try:
                if ext == '.pdf':
                    with fitz.open(abs_path) as src:
                        npages = src.page_count
                        combined.insert_pdf(src)
                    print(f'  [purch]  combine:  {idx:3d}/{total_files}: {_log_safe(fname)!r} — {npages} page(s)')
                else:
                    # Image: insert into a new page that matches image dimensions
                    img_doc = fitz.open(abs_path)          # treat image as single-page doc
                    combined.insert_pdf(img_doc)
                    img_doc.close()
                    print(f'  [purch]  combine:  {idx:3d}/{total_files}: {_log_safe(fname)!r} — 1 page (image)')
            except Exception as e:
                errors.append(f'{fname}: {e}')
                print(f'  [purch]  combine:  {idx:3d}/{total_files}: {_log_safe(fname)!r} — SKIPPED: {_log_safe(str(e))}')

        if combined.page_count == 0:
            combined.close()
            self._json_error(500,
                'No pages could be combined. ' +
                ('Errors: ' + '; '.join(errors) if errors else 'Unknown error.'))
            return

        try:
            pdf_bytes = combined.tobytes(garbage=4, deflate=True)
        except Exception as e:
            combined.close()
            self._json_error(500, f'Failed to serialise combined PDF: {e}')
            return
        combined.close()

        # ── Stream response ──────────────────────────────────────────────────
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Length', str(len(pdf_bytes)))
        self.send_header('Content-Disposition',
                         'inline; filename="combined-invoices.pdf"')
        self.end_headers()
        try:
            self.wfile.write(pdf_bytes)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError) as e:
            print(f'  [purch]  combine: client closed connection: {e}')

    def _upload_purchasing(self):
        """Accept JSON body: {filename, data (base64), folder (optional)}."""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 25 * 1024 * 1024:   # 25 MB (base64 of ~18 MB file)
            self._json_error(413, 'File too large (max ~18 MB).')
            return
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._json_error(400, 'Invalid JSON body.')
            return

        filename = os.path.basename(payload.get('filename', '').strip())
        b64_data = payload.get('data', '')
        folder   = payload.get('folder', '').strip()

        if not filename or not b64_data:
            self._json_error(400, 'Missing filename or data.')
            return

        ext = os.path.splitext(filename)[1].lower()
        if ext not in PURCHASE_ALLOWED_EXTS:
            self._json_error(403, f'File type {ext!r} not allowed.')
            return

        if not folder:
            t = datetime.date.today()
            folder = f'{t.day}-{t.month}-{t.year}'

        # Whitelist: digits, letters, hyphens, underscores, dots only
        safe_chars = set('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_.')
        if '..' in folder or not all(c in safe_chars for c in folder):
            self._json_error(400, 'Invalid folder name.')
            return

        target_dir = os.path.join(PURCHASE_INVOICE_DIR, folder)
        os.makedirs(target_dir, exist_ok=True)

        # Decode base64 content
        try:
            file_bytes = _b64.b64decode(b64_data)
        except Exception:
            self._json_error(400, 'Invalid base64 data.')
            return

        # Resolve filename — avoid overwriting existing files
        dest_name = filename
        dest = os.path.join(target_dir, dest_name)
        if os.path.exists(dest):
            base, ext2 = os.path.splitext(filename)
            n = 1
            while os.path.exists(os.path.join(target_dir, f'{base}_{n}{ext2}')):
                n += 1
            dest_name = f'{base}_{n}{ext2}'
            dest = os.path.join(target_dir, dest_name)

        try:
            with open(dest, 'wb') as f:
                f.write(file_bytes)
        except OSError as e:
            self._json_error(500, f'Could not save file: {e}')
            return

        result = {'saved': dest_name, 'folder': folder,
                  'relativePath': (folder + '/' + dest_name)}
        body_out = json.dumps(result).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body_out)

    # ── GA4 API proxy (read-only) ────────────────────────────────────────────

    def _handle_ga4_get(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/ga4/status':
            self._ga4_status()
        elif parsed.path == '/api/ga4/report':
            params = parse_qs(parsed.query)
            period = params.get('period', ['last_30_days'])[0]
            self._ga4_report(period)
        else:
            self._json_error(404, 'GA4 API endpoint not found.')

    def _ga4_status(self):
        pid_ok   = _token_ready(_ga4_property_id)
        creds_ok = bool(_ga4_creds_path) and not _ga4_creds_path.startswith('REPLACE_')
        file_ok  = creds_ok and os.path.isfile(_ga4_creds_path)

        # Lazy check — do not import google-auth at module level
        try:
            import google.oauth2.service_account  # noqa: F401
            gauth_ok = True
        except ImportError:
            gauth_ok = False

        configured = pid_ok and file_ok and gauth_ok
        msgs = []
        if not pid_ok:
            msgs.append('GA4 property ID is not configured.')
        if not creds_ok:
            msgs.append('GA4 credentials path is not configured.')
        elif not file_ok:
            msgs.append('GA4 credentials file is missing or inaccessible.')
        if not gauth_ok:
            msgs.append('google-auth package is not installed. Run: pip install google-auth requests')

        body = json.dumps({
            'configured':        configured,
            'property_id_set':   pid_ok,
            'creds_file_exists': file_ok,
            'gauth_installed':   gauth_ok,
            'message':           ' '.join(msgs) if msgs else 'GA4 is ready.',
        }).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _ga4_report(self, period='last_30_days'):
        # ── Lazy import — missing package returns 503, does not crash proxy ─
        try:
            from google.oauth2 import service_account as _sa
            import google.auth.transport.requests as _ga_transport
        except ImportError:
            self._json_error(503,
                'google-auth package is not installed. '
                'Run: pip install google-auth requests  then restart proxy.py.')
            return

        if not _token_ready(_ga4_property_id):
            self._json_error(503, 'GA4 property ID is not configured in config.json.')
            return
        if not _ga4_creds_path or _ga4_creds_path.startswith('REPLACE_'):
            self._json_error(503, 'GA4 credentials path is not configured in config.json.')
            return
        if not os.path.isfile(_ga4_creds_path):
            self._json_error(503, 'GA4 credentials file is missing or inaccessible.')
            return

        # ── Authenticate via service account ────────────────────────────────
        try:
            SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
            creds  = _sa.Credentials.from_service_account_file(_ga4_creds_path, scopes=SCOPES)
            creds.refresh(_ga_transport.Request())
            access_token = creds.token
        except Exception:
            self._json_error(503,
                'Failed to authenticate with GA4. '
                'Check that the service account JSON file is valid and not corrupted.')
            return

        # ── Date range ───────────────────────────────────────────────────────
        if period == 'last_7_days':
            date_range   = {'startDate': '7daysAgo',  'endDate': 'today'}
            period_label = 'Last 7 Days'
        else:
            date_range   = {'startDate': '30daysAgo', 'endDate': 'today'}
            period_label = 'Last 30 Days'

        prop_url = (f'https://analyticsdata.googleapis.com/v1beta'
                    f'/properties/{_ga4_property_id}:runReport')
        auth_headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type':  'application/json',
        }

        def _post(body_dict):
            """POST one runReport call; returns (data_dict, error_str)."""
            raw = json.dumps(body_dict).encode('utf-8')
            req = urllib.request.Request(
                prop_url, data=raw, method='POST', headers=auth_headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read()), None
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read().decode('utf-8', errors='replace'))
                    code = err.get('error', {}).get('code', e.code)
                    msg  = err.get('error', {}).get('message', '')
                except Exception:
                    code, msg = e.code, ''
                if code in (401, 403):
                    return None, ('Service account does not have access to this GA4 property. '
                                  'Grant Viewer role in GA4 Admin > Property Access Management.')
                if code == 400:
                    return None, f'GA4 API rejected the request (400). Check metric/dimension names. Detail: {msg[:120]}'
                return None, f'GA4 API returned error {code}.'
            except urllib.error.URLError:
                return None, 'Could not reach GA4 API. Check internet connection.'
            except Exception:
                return None, 'Unexpected error calling GA4 API.'

        # ── Call 1: page-level sessions + engagement ─────────────────────────
        page_data, err = _post({
            'dateRanges': [date_range],
            'dimensions': [
                {'name': 'pagePath'},
                {'name': 'pageTitle'},
                {'name': 'sessionSourceMedium'},
            ],
            'metrics': [
                {'name': 'sessions'},
                {'name': 'totalUsers'},
                {'name': 'screenPageViews'},
                {'name': 'averageSessionDuration'},
                {'name': 'engagementRate'},
                {'name': 'eventCount'},
                {'name': 'keyEvents'},
            ],
            'limit': 100,
            'orderBys': [{'metric': {'metricName': 'sessions'}, 'desc': True}],
        })
        if err:
            self._json_error(502, err)
            return

        # ── Call 2: custom events per page (non-fatal if property has none) ──
        EVENT_MAP = {
            'whatsapp_click': 'whatsappClicks',
            'contact_click':  'contactClicks',
            'form_submit':    'formSubmits',
            'scroll':         'scrolls',
        }
        event_data, _ = _post({
            'dateRanges': [date_range],
            'dimensions': [{'name': 'pagePath'}, {'name': 'eventName'}],
            'metrics':    [{'name': 'eventCount'}],
            'dimensionFilter': {
                'filter': {
                    'fieldName':    'eventName',
                    'inListFilter': {'values': list(EVENT_MAP.keys())},
                }
            },
            'limit': 10000,
        })

        # ── Call 3: audience by country / city / device ──────────────────────
        audience_data, _ = _post({
            'dateRanges': [date_range],
            'dimensions': [
                {'name': 'country'},
                {'name': 'city'},
                {'name': 'deviceCategory'},
            ],
            'metrics': [
                {'name': 'totalUsers'},
                {'name': 'newUsers'},
                {'name': 'sessions'},
                {'name': 'averageSessionDuration'},
                {'name': 'engagementRate'},
                {'name': 'keyEvents'},
            ],
            'limit': 100,
            'orderBys': [{'metric': {'metricName': 'sessions'}, 'desc': True}],
        })

        # ── Build event lookup: {pagePath: {fieldName: count}} ───────────────
        evt_lookup = {}
        for row in (event_data or {}).get('rows', []):
            dims = [d['value'] for d in row.get('dimensionValues', [])]
            if len(dims) < 2:
                continue
            path, evt = dims[0], dims[1]
            field = EVENT_MAP.get(evt)
            if field:
                bucket = evt_lookup.setdefault(path, {})
                bucket[field] = bucket.get(field, 0) + int(float(
                    row['metricValues'][0]['value']))

        # ── Transform pages ──────────────────────────────────────────────────
        pages = []
        for row in (page_data.get('rows') or []):
            dims = [d['value'] for d in row.get('dimensionValues', [])]
            mets = [m['value'] for m in row.get('metricValues', [])]
            if len(dims) < 3 or len(mets) < 7:
                continue
            path = dims[0]
            evt  = evt_lookup.get(path, {})
            pages.append({
                'landingPage':           path,
                'pagePath':              path,
                'pageTitle':             dims[1],
                'sourceMedium':          dims[2],
                'sessions':              int(float(mets[0])),
                'users':                 int(float(mets[1])),
                'views':                 int(float(mets[2])),
                'averageEngagementTime': round(float(mets[3])),
                'engagementRate':        round(float(mets[4]), 4),
                'eventCount':            int(float(mets[5])),
                'keyEvents':             int(float(mets[6])),
                'whatsappClicks':        evt.get('whatsappClicks', 0),
                'contactClicks':         evt.get('contactClicks', 0),
                'formSubmits':           evt.get('formSubmits', 0),
                'scrolls':               evt.get('scrolls', 0),
            })

        # ── Transform audience ───────────────────────────────────────────────
        audience = []
        for row in (audience_data or {}).get('rows', []):
            dims = [d['value'] for d in row.get('dimensionValues', [])]
            mets = [m['value'] for m in row.get('metricValues', [])]
            if len(dims) < 3 or len(mets) < 6:
                continue
            total = int(float(mets[0]))
            new_u = int(float(mets[1]))
            audience.append({
                'country':               dims[0],
                'city':                  dims[1],
                'deviceCategory':        dims[2],
                'users':                 total,
                'newUsers':              new_u,
                'returningUsers':        max(0, total - new_u),
                'sessions':              int(float(mets[2])),
                'averageEngagementTime': round(float(mets[3])),
                'engagementRate':        round(float(mets[4]), 4),
                'keyEvents':             int(float(mets[5])),
                'whatsappClicks':        0,
                'contactClicks':         0,
                'formSubmits':           0,
            })

        result = json.dumps({
            'propertyName': f'GA4 Property {_ga4_property_id}',
            'period':        period_label,
            'pages':         pages,
            'audience':      audience,
            '_source':       'api',
        }).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(result)

    # ── GA4 Setup endpoints (localhost-only, read-write config) ─────────────

    _VISTA_KEYS_DIR   = os.path.join(os.path.expanduser('~'), '.vista-platform', 'keys')
    _GA4_SA_FILENAME  = 'ga4-service-account.json'

    def _require_localhost(self):
        """Block non-localhost clients. Returns True if allowed, sends 403 and returns False otherwise."""
        if self.client_address[0] != '127.0.0.1':
            self._json_error(403, 'Setup endpoints are only accessible from localhost.')
            return False
        return True

    def _handle_setup_get(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/setup/ga4/status':
            self._ga4_setup_status()
        elif parsed.path == '/api/setup/google-ads/status':
            self._gads_setup_status()
        elif parsed.path == '/api/setup/meta/status':
            self._meta_setup_status()
        elif parsed.path == '/api/setup/meta/accounts':
            self._meta_setup_accounts()
        elif parsed.path == '/api/setup/meta/token-diag':
            self._meta_token_diag()
        else:
            self._json_error(404, 'Setup endpoint not found.')

    def _handle_setup_post(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/setup/ga4/save':
            self._ga4_setup_save()
        elif parsed.path == '/api/setup/ga4/test':
            self._ga4_setup_test()
        elif parsed.path == '/api/setup/google-ads/save':
            self._gads_setup_save()
        elif parsed.path == '/api/setup/google-ads/test':
            self._gads_setup_test()
        elif parsed.path == '/api/setup/meta/save':
            self._meta_setup_save()
        elif parsed.path == '/api/setup/meta/test':
            self._meta_setup_test()
        else:
            self._json_error(404, 'Setup endpoint not found.')

    def _ga4_setup_status(self):
        """Return masked GA4 config state — never returns credential values."""
        pid_ok   = _token_ready(_ga4_property_id)
        creds_ok = bool(_ga4_creds_path) and not _ga4_creds_path.startswith('REPLACE_')
        file_ok  = creds_ok and os.path.isfile(_ga4_creds_path)
        try:
            import google.oauth2.service_account  # noqa: F401
            gauth_ok = True
        except ImportError:
            gauth_ok = False

        configured = pid_ok and file_ok and gauth_ok

        # Mask property ID — show last 4 digits only
        masked = ''
        if pid_ok:
            masked = ('****' + _ga4_property_id[-4:]) if len(_ga4_property_id) >= 4 else '****'

        msgs = []
        if not pid_ok:
            msgs.append('GA4 property ID is not configured.')
        if not creds_ok:
            msgs.append('GA4 credentials path is not configured.')
        elif not file_ok:
            msgs.append('GA4 credentials file is missing or inaccessible.')
        if not gauth_ok:
            msgs.append('google-auth is not installed. Run: pip install google-auth requests')

        body = json.dumps({
            'configured':          configured,
            'property_id_set':     pid_ok,
            'property_id_masked':  masked,
            'creds_file_exists':   file_ok,
            'gauth_installed':     gauth_ok,
            'message':             'Ready.' if configured else (' '.join(msgs) if msgs else 'Not configured.'),
        }).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _ga4_setup_save(self):
        """Validate, save SA JSON outside repo, update config.json atomically."""
        global _ga4_property_id, _ga4_creds_path   # declared first — used later in this function

        if not self._require_localhost():
            return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 64 * 1024:
            self._json_error(413, 'Request body too large.')
            return
        raw = self.rfile.read(content_length)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._json_error(400, 'Invalid JSON body.')
            return

        property_id = str(payload.get('property_id', '')).strip()
        sa_json_raw = payload.get('service_account_json', '')

        # ── Validate property ID ─────────────────────────────────────────────
        if not property_id:
            self._json_error(400, 'GA4 property ID is required.')
            return
        if not property_id.isdigit():
            self._json_error(400, 'GA4 property ID must be numeric (digits only, no dashes or prefixes).')
            return

        # ── Validate service account JSON (if provided) ──────────────────────
        sa_dict = None
        if sa_json_raw and sa_json_raw.strip():
            try:
                sa_dict = json.loads(sa_json_raw) if isinstance(sa_json_raw, str) else sa_json_raw
            except json.JSONDecodeError:
                self._json_error(400, 'Service account JSON is not valid JSON.')
                return

            if sa_dict.get('type') != 'service_account':
                self._json_error(400, "Invalid service account file — 'type' must be 'service_account'.")
                return
            for field in ('project_id', 'private_key', 'private_key_id', 'client_email', 'token_uri'):
                if not sa_dict.get(field):
                    self._json_error(400, f"Invalid service account file — required field '{field}' is missing.")
                    return
        else:
            # No SA JSON provided — only allowed if a key file already exists
            if not (_ga4_creds_path and os.path.isfile(_ga4_creds_path)):
                self._json_error(400, 'Service account JSON is required — no existing credentials found.')
                return

        # ── All validation passed — write files ──────────────────────────────
        sa_path = os.path.join(self._VISTA_KEYS_DIR, self._GA4_SA_FILENAME)

        if sa_dict is not None:
            try:
                os.makedirs(self._VISTA_KEYS_DIR, exist_ok=True)
            except OSError:
                self._json_error(500, 'Could not create keys directory.')
                return
            sa_tmp = sa_path + '.tmp'
            try:
                with open(sa_tmp, 'w', encoding='utf-8') as f:
                    json.dump(sa_dict, f, indent=2)
                os.replace(sa_tmp, sa_path)
            except OSError:
                self._json_error(500, 'Could not save service account file.')
                return
        else:
            # Keep existing key file path
            sa_path = _ga4_creds_path

        # ── Update config.json atomically ────────────────────────────────────
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg.setdefault('marketing_apis', {})
            cfg['marketing_apis'].setdefault('google', {})
            cfg['marketing_apis']['google'].setdefault('ga4', {})
            cfg['marketing_apis']['google']['ga4']['property_id']         = property_id
            cfg['marketing_apis']['google']['ga4']['credentials_json_path'] = sa_path
            cfg_tmp = CONFIG_PATH + '.tmp'
            with open(cfg_tmp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(cfg_tmp, CONFIG_PATH)
        except (OSError, json.JSONDecodeError):
            self._json_error(500,
                'Service account file saved but config.json could not be updated. '
                'Check file permissions.')
            return

        # ── Reload in-memory config vars (no restart needed) ─────────────────
        _ga4_property_id = property_id
        _ga4_creds_path  = sa_path

        print('  [setup]  GA4 configured — property ID and credentials updated.')

        body = json.dumps({'ok': True, 'message': 'GA4 configuration saved successfully.'}).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _ga4_setup_test(self):
        """Authenticate with GA4 and run a minimal 1-row report to confirm access."""
        if not self._require_localhost():
            return

        # Consume (and discard) any request body
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length:
            self.rfile.read(min(content_length, 1024))

        try:
            from google.oauth2 import service_account as _sa
            import google.auth.transport.requests as _ga_transport
        except ImportError:
            self._json_error(503,
                'google-auth is not installed. Run: pip install google-auth requests then restart proxy.py.')
            return

        if not _token_ready(_ga4_property_id):
            self._json_error(503, 'GA4 property ID is not configured. Complete the setup first.')
            return
        if not _ga4_creds_path or not os.path.isfile(_ga4_creds_path):
            self._json_error(503, 'GA4 credentials file not found. Complete the setup first.')
            return

        try:
            SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
            creds  = _sa.Credentials.from_service_account_file(_ga4_creds_path, scopes=SCOPES)
            creds.refresh(_ga_transport.Request())
            access_token = creds.token
        except Exception:
            self._json_error(503,
                'Authentication failed. Check that the service account JSON file is valid.')
            return

        test_body = json.dumps({
            'dateRanges': [{'startDate': '7daysAgo', 'endDate': 'today'}],
            'dimensions': [{'name': 'pagePath'}],
            'metrics':    [{'name': 'sessions'}],
            'limit': 1,
        }).encode('utf-8')
        req = urllib.request.Request(
            f'https://analyticsdata.googleapis.com/v1beta/properties/{_ga4_property_id}:runReport',
            data=test_body, method='POST',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type':  'application/json',
            })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data      = json.loads(resp.read())
                row_count = data.get('rowCount', 0)
                body = json.dumps({
                    'ok':      True,
                    'message': f'Connection successful — GA4 property is accessible. {row_count} page(s) found in the last 7 days.',
                }).encode('utf-8')
                self.send_response(200)
                self._send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            try:
                err  = json.loads(e.read().decode('utf-8', errors='replace'))
                code = err.get('error', {}).get('code', e.code)
            except Exception:
                code = e.code
            if code in (401, 403):
                self._json_error(502,
                    'Service account does not have access to this GA4 property. '
                    'Grant Viewer role in GA4 Admin > Property Access Management.')
            else:
                self._json_error(502, f'GA4 API returned error {code}.')
        except urllib.error.URLError:
            self._json_error(502, 'Could not reach GA4 API. Check internet connection.')
        except Exception:
            self._json_error(500, 'Unexpected error during connection test.')

    # ── Google Ads Setup endpoints (localhost-only, read-write config) ─────────

    _GADS_OAUTH_FILENAME = 'google-ads-oauth.json'

    def _handle_gads_get(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/google-ads/status':
            self._gads_setup_status()
        else:
            self._json_error(404, 'Google Ads API endpoint not found.')

    def _gads_setup_status(self):
        """Return masked Google Ads config state — never returns credential values."""
        cust_ok  = _token_ready(_gads_customer_id)
        oauth_ok = bool(_gads_oauth_path) and not _gads_oauth_path.startswith('REPLACE_')
        file_ok  = oauth_ok and os.path.isfile(_gads_oauth_path)
        try:
            import google.ads.googleads.client  # noqa: F401
            gads_lib_ok = True
        except ImportError:
            gads_lib_ok = False

        configured = cust_ok and file_ok and gads_lib_ok

        # Mask customer ID — show last 4 digits only
        masked = ''
        if cust_ok:
            masked = ('****' + _gads_customer_id[-4:]) if len(_gads_customer_id) >= 4 else '****'
        login_cust_masked = ''
        if _gads_login_cust_id:
            login_cust_masked = ('****' + _gads_login_cust_id[-4:]) if len(_gads_login_cust_id) >= 4 else '****'

        msgs = []
        if not cust_ok:
            msgs.append('Google Ads customer ID is not configured.')
        if not oauth_ok:
            msgs.append('Google Ads credentials path is not configured.')
        elif not file_ok:
            msgs.append('Google Ads credentials file is missing or inaccessible.')
        if not gads_lib_ok:
            msgs.append('google-ads package is not installed. Run: python -m pip install google-ads')

        body = json.dumps({
            'configured':          configured,
            'customer_id_set':     cust_ok,
            'customer_id_masked':  masked,
            'login_customer_id_masked': login_cust_masked,
            'oauth_file_exists':   file_ok,
            'gads_lib_installed':  gads_lib_ok,
            'message':             'Ready.' if configured else (' '.join(msgs) if msgs else 'Not configured.'),
        }).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _gads_setup_save(self):
        global _gads_oauth_path, _gads_customer_id, _gads_login_cust_id
        if not self._require_localhost():
            return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self._json_error(400, 'Empty request body.')
            return
        raw = self.rfile.read(min(content_length, 256 * 1024))
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_error(400, 'Invalid JSON body.')
            return

        # ── Extract and validate required fields ────────────────────────────
        def _strip_cid(s):
            return s.replace('-', '').replace(' ', '')

        developer_token = (payload.get('developer_token') or '').strip()
        client_id       = (payload.get('client_id') or '').strip()
        client_secret   = (payload.get('client_secret') or '').strip()
        refresh_token   = (payload.get('refresh_token') or '').strip()
        customer_id_raw = (payload.get('customer_id') or '').strip()
        login_cust_raw  = (payload.get('login_customer_id') or '').strip()

        missing = [n for n, v in [
            ('developer_token', developer_token),
            ('client_id',       client_id),
            ('client_secret',   client_secret),
            ('refresh_token',   refresh_token),
            ('customer_id',     customer_id_raw),
        ] if not v]
        if missing:
            self._json_error(400, f'Missing required field(s): {", ".join(missing)}.')
            return

        customer_id_clean = _strip_cid(customer_id_raw)
        if not customer_id_clean.isdigit() or len(customer_id_clean) < 5:
            self._json_error(400, 'customer_id must be digits only (hyphens are stripped automatically). Example: 123-456-7890 or 1234567890.')
            return

        login_cust_clean = ''
        if login_cust_raw:
            login_cust_clean = _strip_cid(login_cust_raw)
            if not login_cust_clean.isdigit() or len(login_cust_clean) < 5:
                self._json_error(400, 'login_customer_id must be digits only when provided. Leave blank if not using an MCC account.')
                return

        # ── Write credentials to keys file (outside repo) ───────────────────
        oauth_path = os.path.join(self._VISTA_KEYS_DIR, self._GADS_OAUTH_FILENAME)
        try:
            os.makedirs(self._VISTA_KEYS_DIR, exist_ok=True)
        except OSError as e:
            self._json_error(500, f'Could not create keys directory: {e.strerror}')
            return

        oauth_data = {
            'developer_token': developer_token,
            'client_id':       client_id,
            'client_secret':   client_secret,
            'refresh_token':   refresh_token,
        }
        oauth_tmp = oauth_path + '.tmp'
        try:
            with open(oauth_tmp, 'w', encoding='utf-8') as f:
                json.dump(oauth_data, f, indent=2)
            os.replace(oauth_tmp, oauth_path)
        except OSError as e:
            self._json_error(500, f'Failed to write credentials file: {e.strerror}')
            return

        # ── Update config.json atomically ───────────────────────────────────
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._json_error(500, f'Failed to read config.json: {e}')
            return

        cfg.setdefault('marketing_apis', {}).setdefault('google', {}).setdefault('ads', {})
        cfg['marketing_apis']['google']['ads']['oauth_json_path']    = oauth_path
        cfg['marketing_apis']['google']['ads']['customer_id']        = customer_id_clean
        cfg['marketing_apis']['google']['ads']['login_customer_id']  = login_cust_clean

        cfg_tmp = CONFIG_PATH + '.tmp'
        try:
            with open(cfg_tmp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
            os.replace(cfg_tmp, CONFIG_PATH)
        except OSError as e:
            self._json_error(500, f'Failed to update config.json: {e.strerror}')
            return

        # ── Reload in-memory vars ───────────────────────────────────────────
        _gads_oauth_path    = oauth_path
        _gads_customer_id   = customer_id_clean
        _gads_login_cust_id = login_cust_clean

        masked = ('****' + customer_id_clean[-4:]) if len(customer_id_clean) >= 4 else '****'
        body = json.dumps({
            'ok':      True,
            'message': f'Google Ads credentials saved. Customer ID ending {masked}.',
        }).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _gads_setup_test(self):
        """Authenticate with Google Ads API and list accessible customer accounts."""
        if not self._require_localhost():
            return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length:
            self.rfile.read(min(content_length, 1024))

        if not _gads_oauth_path or not os.path.isfile(_gads_oauth_path):
            self._json_error(400, 'Google Ads credentials file is not configured. Use Save first.')
            return
        if not _token_ready(_gads_customer_id):
            self._json_error(400, 'Google Ads customer ID is not configured. Use Save first.')
            return

        try:
            from google.ads.googleads.client import GoogleAdsClient
        except ImportError:
            self._json_error(503, 'google-ads package is not installed. Run: python -m pip install google-ads')
            return

        try:
            with open(_gads_oauth_path, 'r', encoding='utf-8') as f:
                creds = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._json_error(500, 'Google Ads credentials file is missing or invalid.')
            return

        for field in ('developer_token', 'client_id', 'client_secret', 'refresh_token'):
            if not creds.get(field):
                self._json_error(400, f'Credentials file is missing field: {field}. Re-save your credentials.')
                return

        ads_config = {
            'developer_token': creds['developer_token'],
            'client_id':       creds['client_id'],
            'client_secret':   creds['client_secret'],
            'refresh_token':   creds['refresh_token'],
            'use_proto_plus':  True,
        }
        if _gads_login_cust_id:
            ads_config['login_customer_id'] = _gads_login_cust_id

        try:
            client  = GoogleAdsClient.load_from_dict(ads_config)
            svc     = client.get_service('CustomerService')
            result  = svc.list_accessible_customers()
            count   = len(result.resource_names)
            body = json.dumps({
                'ok':      True,
                'message': f'Connection successful — {count} Google Ads account(s) accessible.',
            }).encode('utf-8')
            self.send_response(200)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = str(e)
            if 'OAUTH_TOKEN_INVALID' in err or 'invalid_grant' in err:
                msg = 'OAuth refresh token is invalid or expired. Re-authorise via Google OAuth.'
            elif 'DEVELOPER_TOKEN_NOT_APPROVED' in err or 'not approved' in err.lower():
                msg = 'Developer token not approved for production use. Apply for standard access in Google Ads API Centre.'
            elif 'CUSTOMER_NOT_FOUND' in err:
                msg = 'Customer ID not found. Check the ID in your Google Ads account settings.'
            elif 'PERMISSION_DENIED' in err:
                msg = 'Permission denied. Check developer token access level and account permissions.'
            elif 'invalid_client' in err:
                msg = 'OAuth client credentials are invalid. Check client ID and client secret.'
            elif 'quota' in err.lower():
                msg = 'API quota exceeded. Try again later.'
            else:
                msg = 'Google Ads API connection failed. Check all credentials and try again.'
            self._json_error(502, msg)

    # ── Meta / Instagram Setup endpoints (localhost-only, read-write config) ──

    _META_TOKEN_FILENAME = 'meta-access-token.json'

    def _handle_meta_get(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/meta/status':
            self._meta_setup_status()
        elif parsed.path == '/api/setup/meta/accounts':
            self._meta_setup_accounts()
        elif parsed.path == '/api/setup/meta/token-diag':
            self._meta_token_diag()
        elif parsed.path == '/api/meta/ig1/profile':
            self._meta_ig1_profile()
        elif parsed.path == '/api/meta/ig1/insights':
            self._meta_ig1_insights()
        else:
            self._json_error(404, 'Meta API endpoint not found.')

    def _meta_setup_status(self):
        """Return masked Meta config state — never returns token value, path, or IDs in full."""
        token_ok   = _token_ready(_meta_token_path)
        file_ok    = token_ok and os.path.isfile(_meta_token_path)
        acct_ok    = bool(_meta_ig_acct_id) and _meta_ig_acct_id.isdigit()

        configured = file_ok and acct_ok

        # Mask Instagram Business Account ID — last 4 digits only
        acct_masked = ''
        if acct_ok:
            acct_masked = ('****' + _meta_ig_acct_id[-4:]) if len(_meta_ig_acct_id) >= 4 else '****'

        # Mask page_id if present
        page_masked = ''
        if _meta_page_id and _meta_page_id.isdigit():
            page_masked = ('****' + _meta_page_id[-4:]) if len(_meta_page_id) >= 4 else '****'

        token_only = file_ok and not acct_ok

        msgs = []
        if not token_ok:
            msgs.append('Meta access token path is not configured.')
        elif not file_ok:
            msgs.append('Meta access token file is missing or inaccessible.')
        if not acct_ok:
            if file_ok:
                msgs.append('Token saved. Use Find Instagram Accounts to select an account.')
            else:
                msgs.append('Instagram Business Account ID is not configured.')

        body = json.dumps({
            'configured':           configured,
            'token_file_exists':    file_ok,
            'token_only':           token_only,
            'ig_account_id_set':    acct_ok,
            'ig_account_id_masked': acct_masked,
            'page_id_masked':       page_masked,
            'message':              'Ready.' if configured else (' '.join(msgs) if msgs else 'Not configured.'),
        }).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _meta_setup_save(self):
        global _meta_token_path, _meta_ig_acct_id, _meta_page_id
        if not self._require_localhost():
            return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self._json_error(400, 'Empty request body.')
            return
        raw = self.rfile.read(min(content_length, 64 * 1024))  # tokens can be long; cap at 64 KB
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_error(400, 'Invalid JSON body.')
            return

        raw_token    = payload.get('access_token') or ''
        ig_acct_id   = (payload.get('instagram_business_account_id') or '').strip().replace(' ', '')
        page_id_raw  = (payload.get('page_id') or '').strip().replace(' ', '')

        # ── Token normalization ──────────────────────────────────────────────
        # 1. Strip outer whitespace / CR / LF
        access_token = raw_token.strip().replace('\r', '').replace('\n', '')
        # 2. Remove surrounding quote characters (user may have pasted "token" or 'token')
        if len(access_token) >= 2 and access_token[0] == access_token[-1] and access_token[0] in ('"', "'"):
            access_token = access_token[1:-1].strip()
        # 3. Reject if internal whitespace remains after normalization
        if any(c in access_token for c in (' ', '\t', '\r', '\n')):
            self._json_error(400, 'access_token contains whitespace. Paste the token as a single unbroken string.')
            return

        if not access_token:
            self._json_error(400, 'access_token is required.')
            return
        if ig_acct_id and not ig_acct_id.isdigit():
            self._json_error(400, 'instagram_business_account_id must be digits only.')
            return
        if page_id_raw and not page_id_raw.isdigit():
            self._json_error(400, 'page_id must be digits only when provided. Leave blank if unsure.')
            return

        # ── Write token to keys file (outside repo) ─────────────────────────
        token_path = os.path.join(self._VISTA_KEYS_DIR, self._META_TOKEN_FILENAME)
        try:
            os.makedirs(self._VISTA_KEYS_DIR, exist_ok=True)
        except OSError as e:
            self._json_error(500, f'Could not create keys directory: {e.strerror}')
            return

        token_data = {'access_token': access_token}
        token_tmp  = token_path + '.tmp'
        try:
            with open(token_tmp, 'w', encoding='utf-8') as f:
                json.dump(token_data, f, indent=2)
            os.replace(token_tmp, token_path)
        except OSError as e:
            self._json_error(500, f'Failed to write token file: {e.strerror}')
            return

        # ── Update config.json atomically ───────────────────────────────────
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._json_error(500, f'Failed to read config.json: {e}')
            return

        cfg.setdefault('marketing_apis', {}).setdefault('meta', {})
        cfg['marketing_apis']['meta']['token_path'] = token_path
        if ig_acct_id:
            cfg['marketing_apis']['meta']['instagram_business_account_id'] = ig_acct_id
        if page_id_raw:
            cfg['marketing_apis']['meta']['page_id'] = page_id_raw

        cfg_tmp = CONFIG_PATH + '.tmp'
        try:
            with open(cfg_tmp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
            os.replace(cfg_tmp, CONFIG_PATH)
        except OSError as e:
            self._json_error(500, f'Failed to update config.json: {e.strerror}')
            return

        # ── Reload in-memory vars ───────────────────────────────────────────
        _meta_token_path = token_path
        if ig_acct_id:
            _meta_ig_acct_id = ig_acct_id
        if page_id_raw:
            _meta_page_id = page_id_raw

        token_suffix_safe = access_token[-6:] if len(access_token) >= 6 else access_token
        if ig_acct_id:
            acct_masked = ('****' + ig_acct_id[-4:]) if len(ig_acct_id) >= 4 else '****'
            msg = f'Saved. Token ending ···{token_suffix_safe}. Instagram Account ID ending {acct_masked}.'
        else:
            msg = f'Saved. Token ending ···{token_suffix_safe}. Use Find Instagram Accounts to select an account.'
        body = json.dumps({'ok': True, 'message': msg, 'token_suffix': token_suffix_safe}).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _meta_read_token_safe(self):
        """Read saved token from file. Returns (token_str, diag_dict, error_str|None).
        token_str is None on error. Never exposes token value in diag."""
        if not _meta_token_path or not os.path.isfile(_meta_token_path):
            return None, {}, 'Token file not found. Save your access token first.'
        try:
            with open(_meta_token_path, 'r', encoding='utf-8') as f:
                raw_json = f.read()
            token_data = json.loads(raw_json)
        except (OSError, json.JSONDecodeError) as e:
            return None, {}, f'Could not read token file: {type(e).__name__}'

        token = token_data.get('access_token', '') or ''

        # Normalise (same rules as save — in case file was written externally)
        token = token.strip().replace('\r', '').replace('\n', '')
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
            token = token[1:-1].strip()

        raw_for_check = token_data.get('access_token', '') or ''
        diag = {
            'token_present':      bool(token),
            'token_length':       len(token),
            'token_length_ok':    len(token) >= 50,
            'token_prefix':       token[:6]  if len(token) >= 6  else f'({len(token)} chars — too short)',
            'token_suffix':       token[-6:] if len(token) >= 6  else f'({len(token)} chars — too short)',
            'token_has_whitespace': any(c in token for c in (' ', '\t')),
            'token_has_quotes':   (raw_for_check[:1] in ('"', "'") or raw_for_check[-1:] in ('"', "'")),
            'token_newline_count': raw_for_check.count('\n') + raw_for_check.count('\r'),
            'token_source':       'saved_file',
        }
        return (token if token else None), diag, None

    def _meta_token_diag(self):
        """Localhost-only: read saved token and return safe diagnostic — no API calls."""
        if not self._require_localhost():
            return
        token, diag, err = self._meta_read_token_safe()
        resp = {'ok': not bool(err), 'diag': diag}
        if err:
            resp['error'] = err
        body = json.dumps(resp).encode('utf-8')
        self.send_response(200 if not err else 400)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _meta_graph_call(self, endpoint, params, token):
        """Make a safe Meta Graph API GET call. Returns (data_dict, None) or (None, err_dict).
        err_dict keys: code, type, message (safe human text, not raw Meta message)."""
        from urllib.parse import urlencode
        params['access_token'] = token
        qs = urlencode(params)
        url = f'https://graph.facebook.com/v20.0/{endpoint}?{qs}'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'vista-platform/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8')), None
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode('utf-8'))
                err = err_body.get('error', {})
            except Exception:
                err = {}
            return None, {
                'code':    err.get('code', e.code),
                'type':    err.get('type', ''),
                'message': err.get('message', f'HTTP {e.code}'),
            }
        except urllib.error.URLError as e:
            return None, {'code': 0, 'type': 'URLError', 'message': str(e.reason)}
        except Exception as e:
            return None, {'code': 0, 'type': type(e).__name__, 'message': str(e)}

    def _meta_setup_accounts(self):
        """Discover IG Business accounts linked to saved token — returns safe, non-secret data only."""
        if not self._require_localhost():
            return

        # ── Read token from file using shared helper ─────────────────────────
        token, diag, read_err = self._meta_read_token_safe()

        if read_err or not token:
            err_msg = read_err or 'Token file exists but access_token is empty.'
            body = json.dumps({'ok': False, 'error': err_msg, 'diag': diag}).encode('utf-8')
            self.send_response(400)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Step 1: /me check — simplest token validity test ─────────────────
        me_data, me_err = self._meta_graph_call('me', {'fields': 'id,name'}, token)
        diag['graph_endpoint_called'] = 'me'

        if me_err:
            diag['meta_error_code']         = me_err['code']
            diag['meta_error_type']         = me_err['type']
            diag['meta_error_message_safe'] = me_err['message']
            code = me_err['code']
            if code in (190, 102, 463, 467):
                user_msg = 'Token rejected by Meta (/me check failed). Token may be expired, revoked, or saved incorrectly — check prefix/suffix in diag match your token.'
            elif code in (10, 200):
                user_msg = 'Token is valid but missing permissions. Ensure instagram_basic and pages_show_list scopes are granted.'
            elif code == 4:
                user_msg = 'Meta API rate limit reached. Try again in a few minutes.'
            elif code == 0:
                user_msg = 'Could not reach Meta API. Check internet connection.'
            else:
                user_msg = f'Meta API returned error code {code}. See diag.meta_error_message_safe for details.'
            body = json.dumps({'ok': False, 'error': user_msg, 'diag': diag}).encode('utf-8')
            self.send_response(400)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        # /me succeeded — token is valid
        diag['me_id_present'] = bool(me_data.get('id'))

        # ── Step 2: /me/accounts with field expansion ─────────────────────────
        diag['graph_endpoint_called'] = 'me/accounts'
        accts_data, accts_err = self._meta_graph_call(
            'me/accounts',
            {'fields': 'id,name,instagram_business_account{id,username,name}'},
            token,
        )

        if accts_err:
            diag['meta_error_code']         = accts_err['code']
            diag['meta_error_type']         = accts_err['type']
            diag['meta_error_message_safe'] = accts_err['message']
            code = accts_err['code']
            if code in (10, 200):
                user_msg = 'Token is valid (/me passed) but lacks page permissions. Grant pages_show_list and pages_read_engagement, then generate a new token.'
            elif code == 4:
                user_msg = 'Meta API rate limit reached. Try again in a few minutes.'
            else:
                user_msg = f'Token valid but /me/accounts failed (code {code}). See diag for details.'
            body = json.dumps({'ok': False, 'error': user_msg, 'diag': diag}).encode('utf-8')
            self.send_response(400)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        accounts = []
        for page in accts_data.get('data', []):
            ig = page.get('instagram_business_account')
            if ig:
                accounts.append({
                    'page_id':   page.get('id', ''),
                    'page_name': page.get('name', ''),
                    'instagram_business_account_id': ig.get('id', ''),
                    'username':  ig.get('username', ''),
                    'name':      ig.get('name', ''),
                })

        # ── Fallback: /me/accounts returned 0 pages but a saved IG account ID exists ──
        # This happens when the token lacks pages_show_list scope or the page association
        # isn't visible to this token type, but the token CAN read the IG account directly.
        # Use the same direct-lookup call that Test Connection uses.
        diag['pages_found'] = len(accts_data.get('data', []))
        if not accounts and _meta_ig_acct_id and _meta_ig_acct_id.isdigit():
            diag['fallback_attempted'] = True
            ig_data, ig_err = self._meta_graph_call(
                _meta_ig_acct_id,
                {'fields': 'id,username,name'},
                token,
            )
            if ig_data and not ig_err:
                accounts.append({
                    'page_id':   '',
                    'page_name': '(Page not visible to token — direct lookup)',
                    'instagram_business_account_id': ig_data.get('id', _meta_ig_acct_id),
                    'username':  ig_data.get('username', ''),
                    'name':      ig_data.get('name', ''),
                })
                diag['fallback_success'] = True
            else:
                diag['fallback_success'] = False
                if ig_err:
                    diag['fallback_error_code'] = ig_err.get('code')

        body = json.dumps({'ok': True, 'accounts': accounts, 'diag': diag}).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _meta_setup_test(self):
        """Call Meta Graph API with the saved token — minimal read-only account lookup."""
        if not self._require_localhost():
            return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length:
            self.rfile.read(min(content_length, 1024))

        if not _meta_token_path or not os.path.isfile(_meta_token_path):
            self._json_error(400, 'Meta token file not found. Use Save first.')
            return
        if not _meta_ig_acct_id or not _meta_ig_acct_id.isdigit():
            self._json_error(400, 'Instagram Business Account ID is not configured. Use Save first.')
            return

        try:
            with open(_meta_token_path, 'r', encoding='utf-8') as f:
                token_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._json_error(500, 'Meta token file is missing or invalid.')
            return

        token = token_data.get('access_token', '')
        if not token:
            self._json_error(400, 'Token file exists but access_token field is empty. Re-save your credentials.')
            return

        # Minimal read-only call — account username and follower count only
        from urllib.parse import urlencode as _ue
        _qs = _ue({'fields': 'username,followers_count', 'access_token': token})
        url = f'https://graph.facebook.com/v20.0/{_meta_ig_acct_id}?{_qs}'
        req = urllib.request.Request(url, method='GET',
                                     headers={'User-Agent': 'VistaProxy/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            username  = data.get('username', '')
            followers = data.get('followers_count', '')
            handle = f'@{username}' if username else '(account found)'
            body = json.dumps({
                'ok':      True,
                'message': f'Connection successful — {handle}, {followers:,} followers.' if isinstance(followers, int) else f'Connection successful — {handle}.',
            }).encode('utf-8')
            self.send_response(200)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.HTTPError as e:
            try:
                err  = json.loads(e.read().decode('utf-8', errors='replace'))
                code = err.get('error', {}).get('code', e.code)
                emsg = err.get('error', {}).get('message', '')
            except Exception:
                code, emsg = e.code, ''
            if code == 190 or 'token' in emsg.lower():
                msg = 'Access token is invalid or expired. Generate a new long-lived token in Meta Graph API Explorer.'
            elif code == 100 or 'does not exist' in emsg.lower() or 'unsupported' in emsg.lower():
                msg = 'Instagram Business Account ID not found. Verify the numeric ID and ensure the account is Business/Creator type linked to a Facebook Page.'
            elif code == 10 or 'permission' in emsg.lower():
                msg = 'Insufficient token permissions. Ensure instagram_basic and instagram_manage_insights scopes are granted.'
            elif code == 4 or 'rate' in emsg.lower():
                msg = 'Meta API rate limit hit. Try again in a few minutes.'
            elif code == 200 or 'page' in emsg.lower():
                msg = 'Page permission error. Ensure the Facebook Page is linked to the Instagram Business account.'
            else:
                msg = f'Meta API returned error {code}. Check token, account ID, and app permissions.'
            self._json_error(502, msg)
        except urllib.error.URLError:
            self._json_error(502, 'Could not reach Meta API. Check internet connection.')
        except Exception:
            self._json_error(500, 'Unexpected error during connection test.')

    def _meta_ig1_profile(self):
        """Localhost-only: fetch live IG1 account profile fields from Meta Graph API."""
        if not self._require_localhost():
            return

        token, diag, read_err = self._meta_read_token_safe()
        if read_err or not token:
            body = json.dumps({
                'ok': False,
                'error': read_err or 'Token file exists but access_token is empty.',
                'source': 'meta_api_live',
            }).encode('utf-8')
            self.send_response(400)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        if not _meta_ig_acct_id or not _meta_ig_acct_id.isdigit():
            body = json.dumps({
                'ok': False,
                'error': 'Instagram Business Account ID is not configured. Use Find Instagram Accounts in Setup to select an account.',
                'source': 'meta_api_live',
            }).encode('utf-8')
            self.send_response(400)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        fields = 'id,username,name,biography,followers_count,follows_count,media_count,profile_picture_url,website'
        ig_data, ig_err = self._meta_graph_call(
            _meta_ig_acct_id,
            {'fields': fields},
            token,
        )

        if ig_err:
            code = ig_err.get('code', 0)
            if code in (190, 102, 463, 467):
                msg = 'Token rejected or expired. Generate a new token in Meta Graph API Explorer.'
            elif code in (10, 200):
                msg = 'Token valid but missing permissions for profile fields. Ensure instagram_basic scope is granted.'
            elif code == 100:
                msg = 'Instagram Business Account ID not found or account is not Business/Creator type.'
            elif code == 4:
                msg = 'Meta API rate limit reached. Try again in a few minutes.'
            elif code == 0:
                msg = 'Could not reach Meta API. Check internet connection.'
            else:
                msg = f'Meta API returned error code {code}.'
            body = json.dumps({
                'ok': False,
                'error': msg,
                'error_code': code,
                'source': 'meta_api_live',
            }).encode('utf-8')
            self.send_response(502)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        profile = {
            'ok':                  True,
            'source':              'meta_api_live',
            'id':                  ig_data.get('id', ''),
            'username':            ig_data.get('username', ''),
            'name':                ig_data.get('name', ''),
            'biography':           ig_data.get('biography', ''),
            'followers_count':     ig_data.get('followers_count'),
            'follows_count':       ig_data.get('follows_count'),
            'media_count':         ig_data.get('media_count'),
            'profile_picture_url': ig_data.get('profile_picture_url', ''),
            'website':             ig_data.get('website', ''),
        }
        body = json.dumps(profile).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _meta_ig1_insights(self):
        """Localhost-only: fetch live IG1 account-level insights from Meta Graph API."""
        if not self._require_localhost():
            return

        token, diag, read_err = self._meta_read_token_safe()
        if read_err or not token:
            body = json.dumps({
                'ok': False,
                'error': read_err or 'Token file exists but access_token is empty.',
                'source': 'meta_api_live',
            }).encode('utf-8')
            self.send_response(400)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        if not _meta_ig_acct_id or not _meta_ig_acct_id.isdigit():
            body = json.dumps({
                'ok': False,
                'error': 'Instagram Business Account ID is not configured.',
                'source': 'meta_api_live',
            }).encode('utf-8')
            self.send_response(400)
            self._send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        warnings = []
        timeseries = {}
        totals     = {}

        def _classify_err(err):
            """Return (is_fatal, user_msg) for a Meta API error dict."""
            code = err.get('code', 0)
            if code == 10:
                return True, ('permission_missing', 'instagram_manage_insights')
            if code in (190, 102, 463, 467):
                return True, ('token', 'Token rejected or expired. Generate a new token in Meta Graph API Explorer.')
            if code == 4:
                return False, ('rate_limit', 'Meta API rate limit reached. Try again in a few minutes.')
            if code == 0:
                return True, ('network', 'Could not reach Meta API. Check internet connection.')
            return False, ('api_error', f'Meta API returned error code {code}.')

        # ── Call A: time-series — reach, follower_count ───────────────────────
        ts_data, ts_err = self._meta_graph_call(
            f'{_meta_ig_acct_id}/insights',
            {'metric': 'reach,follower_count', 'period': 'day', 'limit': '30'},
            token,
        )
        if ts_err:
            fatal, detail = _classify_err(ts_err)
            if fatal and detail[0] == 'permission_missing':
                body = json.dumps({
                    'ok':                  False,
                    'permission_missing':  True,
                    'required_permission': detail[1],
                    'error': (
                        f'Insights require {detail[1]}. '
                        'Regenerate the Meta token with this scope, then save it again in Setup Center.'
                    ),
                    'error_code': ts_err.get('code'),
                    'source': 'meta_api_live',
                }).encode('utf-8')
                self.send_response(200)
                self._send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            if fatal:
                body = json.dumps({
                    'ok': False, 'error': detail[1],
                    'error_code': ts_err.get('code'), 'source': 'meta_api_live',
                }).encode('utf-8')
                self.send_response(200)
                self._send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            warnings.append(f'Time-series call failed (code {ts_err.get("code")}): {detail[1]}')
        else:
            for item in (ts_data or {}).get('data', []):
                name = item.get('name', '')
                timeseries[name] = {
                    'period': item.get('period', ''),
                    'values': [
                        {'date': v.get('end_time', '')[:10], 'value': v.get('value', 0)}
                        for v in item.get('values', [])
                    ],
                }

        # ── Call B: total-value — profile_views, interactions, clicks ─────────
        tv_data, tv_err = self._meta_graph_call(
            f'{_meta_ig_acct_id}/insights',
            {
                'metric':      'profile_views,accounts_engaged,total_interactions,website_clicks,views',
                'period':      'day',
                'metric_type': 'total_value',
            },
            token,
        )
        if tv_err:
            _, detail = _classify_err(tv_err)
            warnings.append(f'Total-value call failed (code {tv_err.get("code")}): {detail[1]}')
        else:
            for item in (tv_data or {}).get('data', []):
                name  = item.get('name', '')
                tv    = item.get('total_value') or {}
                totals[name] = tv.get('value', 0)

        any_data = bool(timeseries or totals)
        resp = {
            'ok':      any_data,
            'partial': bool(warnings) and any_data,
            'source':  'meta_api_live',
            'metrics': {
                'timeseries': timeseries,
                'totals':     totals,
            },
            'warnings': warnings,
        }
        if not any_data:
            resp['error'] = 'No insights data could be retrieved. ' + '; '.join(warnings)

        body = json.dumps(resp).encode('utf-8')
        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

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
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Only log proxy calls — suppress noisy static-file requests
        if '/notion/' in self.path:
            print(f'  [notion] {self.command} {self.path}  ->  {fmt % args}')
        elif '/daftra/' in self.path:
            print(f'  [daftra] {self.command} {self.path}  ->  {fmt % args}')
        elif '/purchasing-invoices/' in self.path:
            print(f'  [purch]  {self.command} {self.path}  ->  {fmt % args}')
        elif '/api/ga4/' in self.path:
            print(f'  [ga4]    {self.command} {self.path}  ->  {fmt % args}')
        elif '/api/google-ads/' in self.path:
            print(f'  [gads]   {self.command} {self.path}  ->  {fmt % args}')
        elif '/api/meta/' in self.path:
            print(f'  [meta]   {self.command} {self.path}  ->  {fmt % args}')
        elif '/api/setup/' in self.path:
            print(f'  [setup]  {self.command} {self.path}  ->  {fmt % args}')


# ── SQI AI proxy auto-start ──────────────────────────────────────────────────
# Isolated on purpose (see sqi_ai_proxy.py's own docstring): this proxy.py
# process never reads, holds, forwards, or logs ANTHROPIC_API_KEY. It only
# launches sqi_ai_proxy.py as a separate child process, which inherits the
# Windows environment on its own and checks the key itself. Nothing here
# touches any other route or handler above — it is only ever called from
# the __main__ block below.
_SQI_AI_PROXY_PORT = int(os.environ.get('SQI_AI_PROXY_PORT', '8092'))
_SQI_AI_PROXY_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sqi_ai_proxy.py')
_sqi_ai_proxy_process = None  # only set if THIS process started it — see _stop_sqi_ai_proxy


def _sqi_ai_proxy_probe():
    """GET the SQI AI proxy's own /api/status. Returns ('up', data) if
    something answering with the expected shape is already on the port,
    ('down', None) if nothing answers, or ('conflict', None) if the port is
    taken by something that isn't it (never assume — never risk a duplicate
    or a clobbered unrelated service)."""
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{_SQI_AI_PROXY_PORT}/api/status')
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, dict) and 'available' in data and 'provider' in data:
            return 'up', data
        return 'conflict', None
    except (urllib.error.URLError, OSError, ValueError):
        return 'down', None
    except Exception:
        return 'conflict', None


def _sqi_ai_proxy_expected_build():
    """Reads BUILD_VERSION straight out of the current sqi_ai_proxy.py on
    disk — never a separately hardcoded copy in this file that could drift
    out of sync. Loaded as a plain module (never enters its own
    `if __name__ == '__main__'` block), so this never binds port 8092
    itself; it only reads a constant off the already-imported module."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('_sqi_ai_proxy_version_probe', _SQI_AI_PROXY_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, 'BUILD_VERSION', None)
    except Exception:
        return None


def _sqi_ai_proxy_line(label, data):
    key_note = 'AI ready' if (data and data.get('available')) else 'API key not configured'
    build_note = (', build ' + data.get('build')) if (data and data.get('build')) else ''
    print(f'  SQI AI proxy         : {label} ({key_note}{build_note})')


def _find_pid_listening_on_port(port):
    """Falls back to `netstat -ano` to find the PID bound to a port when
    the running process can't tell us its own PID — which is exactly the
    case for an old-build instance that predates the /api/status `pid`
    field being added at all. Without this, a stale process from before
    that field existed could never be found and stopped."""
    try:
        out = subprocess.check_output(['netstat', '-ano'], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    needle = ':' + str(port)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == 'TCP' and parts[-1].isdigit() and parts[-2] == 'LISTENING' and parts[1].endswith(needle):
            return int(parts[-1])
    return None


def _stop_pid_tree(pid):
    """Kills a process (and any children — see _stop_sqi_ai_proxy's own
    docstring for why /T matters) by PID directly, for a process this
    proxy.py instance did not itself start (e.g. a stale-build instance
    discovered already running on the port)."""
    if not pid:
        return False
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _start_sqi_ai_proxy():
    """Best-effort auto-start of the isolated Supplier Quotation
    Intelligence AI proxy, so Youssef never has to run it in a separate
    terminal. Any failure here is caught and reported as a warning — it
    must never stop the main Vista proxy from starting."""
    global _sqi_ai_proxy_process
    expected_build = _sqi_ai_proxy_expected_build()
    state, data = _sqi_ai_proxy_probe()
    if state == 'up':
        running_build = data.get('build')
        if expected_build and running_build != expected_build:
            # An already-running instance is serving OLD code (or code from
            # before build tracking existed, running_build is None) — never
            # silently reuse it just because a port answered. Stop it and
            # fall through to start the current build fresh.
            print(f'  SQI AI proxy         : AI proxy running old build ({running_build or "unknown"} ≠ {expected_build}) - restarting with current build')
            # A pre-upgrade instance may not even report its own PID (that
            # field itself could be part of what's new in this build) — the
            # netstat fallback finds it by port instead so it can still be
            # stopped rather than left running forever.
            pid = data.get('pid') or _find_pid_listening_on_port(_SQI_AI_PROXY_PORT)
            _stop_pid_tree(pid)
            for _ in range(15):
                time.sleep(0.2)
                state2, _ = _sqi_ai_proxy_probe()
                if state2 == 'down':
                    break
        else:
            _sqi_ai_proxy_line('AI proxy already running', data)
            return
    state, data = _sqi_ai_proxy_probe()
    if state == 'up':
        # Stale instance would not go down (e.g. taskkill failed) — do not
        # spawn a duplicate on top of it.
        print(f'  SQI AI proxy         : AI proxy unavailable - old build would not stop on port {_SQI_AI_PROXY_PORT}')
        return
    if state == 'conflict':
        print(f'  SQI AI proxy         : AI proxy unavailable - port {_SQI_AI_PROXY_PORT} is already in use by another process')
        return
    if not os.path.isfile(_SQI_AI_PROXY_SCRIPT):
        print('  SQI AI proxy         : AI proxy unavailable - sqi_ai_proxy.py not found')
        return
    try:
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        log_path = os.path.join(os.path.dirname(_SQI_AI_PROXY_SCRIPT), 'sqi_ai_proxy.log')
        # Redirected to a real log file rather than DEVNULL — some Python
        # launchers (the Microsoft Store "App Execution Alias" stub) fail to
        # relaunch themselves as a child process when stdio is DEVNULL, which
        # would silently make the AI proxy never actually start. A log file
        # also means a startup crash is visible instead of swallowed.
        _sqi_ai_proxy_log = open(log_path, 'a', encoding='utf-8')
        _sqi_ai_proxy_process = subprocess.Popen(
            [sys.executable, _SQI_AI_PROXY_SCRIPT],
            cwd=os.path.dirname(_SQI_AI_PROXY_SCRIPT),
            stdout=_sqi_ai_proxy_log, stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except Exception as e:
        print(f'  SQI AI proxy         : AI proxy unavailable - failed to start ({e})')
        return
    # Give it a moment to bind the port, then confirm rather than assume.
    # A generous window (up to ~8s) because some Python launchers (e.g. the
    # Microsoft Store "App Execution Alias" stub) relaunch themselves as a
    # child process before the real interpreter starts, which is slower
    # than a normal venv/python.exe cold start.
    for _ in range(40):
        time.sleep(0.2)
        state2, data2 = _sqi_ai_proxy_probe()
        if state2 == 'up':
            _sqi_ai_proxy_line('AI proxy started successfully', data2)
            return
        if _sqi_ai_proxy_process.poll() is not None:
            break  # the child exited already — stop waiting on it
    print(f'  SQI AI proxy         : AI proxy unavailable - did not respond on port {_SQI_AI_PROXY_PORT} after starting')


def _stop_sqi_ai_proxy():
    """Only stops the AI proxy if THIS proxy.py process started it — an
    instance Youssef already had running elsewhere is left alone, matching
    the "don't create duplicate processes" rule in the other direction.
    Uses taskkill /T to kill the whole process tree rather than just
    Popen.terminate() on the immediate child: some Python launchers (the
    Microsoft Store "App Execution Alias" stub in particular) relaunch
    themselves as a grandchild process, and terminate() alone would leave
    that real interpreter running as an orphan."""
    if _sqi_ai_proxy_process and _sqi_ai_proxy_process.poll() is None:
        if not _stop_pid_tree(_sqi_ai_proxy_process.pid):
            try:
                _sqi_ai_proxy_process.terminate()
            except Exception:
                pass


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
    print(f'  Daftra               : {"OK - configured" if (_token_ready(_daftra_subdomain) and _token_ready(_daftra_api_key)) else "NOT SET - edit config.json"}')
    _purch_ok = os.path.isdir(PURCHASE_INVOICE_DIR)
    print(f'  Purchasing invoices  : {"OK - folder found" if _purch_ok else "FOLDER NOT FOUND - " + PURCHASE_INVOICE_DIR}')
    _ga4_pid_ok   = _token_ready(_ga4_property_id)
    _ga4_creds_ok = (bool(_ga4_creds_path) and not _ga4_creds_path.startswith('REPLACE_')
                     and os.path.isfile(_ga4_creds_path))
    print(f'  GA4 API              : {"OK - configured" if (_ga4_pid_ok and _ga4_creds_ok) else "NOT SET - edit config.json (marketing_apis.google.ga4)"}')
    _gads_cust_ok  = _token_ready(_gads_customer_id)
    _gads_oauth_ok = (bool(_gads_oauth_path) and not _gads_oauth_path.startswith('REPLACE_')
                      and os.path.isfile(_gads_oauth_path))
    print(f'  Google Ads API       : {"OK - configured" if (_gads_cust_ok and _gads_oauth_ok) else "NOT SET - use Google Ads Setup Center"}')
    _meta_acct_ok  = bool(_meta_ig_acct_id) and _meta_ig_acct_id.isdigit()
    _meta_file_ok  = (bool(_meta_token_path) and not _meta_token_path.startswith('REPLACE_')
                      and os.path.isfile(_meta_token_path))
    print(f'  Meta / Instagram API : {"OK - configured" if (_meta_acct_ok and _meta_file_ok) else "NOT SET - use Meta Setup Center"}')
    _start_sqi_ai_proxy()
    print()
    print('  Press Ctrl+C to stop.')
    print()

    try:
        server = ThreadingHTTPServer((BIND, PORT), VistaProxyHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        _stop_sqi_ai_proxy()
        print('\n  Proxy stopped.')
    except OSError as e:
        _stop_sqi_ai_proxy()
        print(f'\n  ERROR: Could not start server on port {PORT}: {e}')
        print(f'  Try changing "port" in config.json to a free port (e.g. 8081).')
        sys.exit(1)
