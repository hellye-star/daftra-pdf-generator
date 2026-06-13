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
import sys
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
        elif self.path.startswith('/api/ga4/'):
            self._handle_ga4_get()
        elif self.path.startswith('/daftra/'):
            self._proxy_daftra()
        else:
            route = self._notion_route()
            if route:
                self._proxy_notion(*route)
            else:
                super().do_GET()

    def do_POST(self):
        if self.path.startswith('/purchasing-invoices/combine'):
            self._combine_purchasing()
        elif self.path.startswith('/purchasing-invoices/upload'):
            self._upload_purchasing()
        elif self.path.startswith('/api/ga4/'):
            self._json_error(405, 'Method not allowed. GA4 API proxy is read-only (GET only).')
        elif self.path.startswith('/daftra/'):
            self._block_daftra_write()
        else:
            route = self._notion_route()
            if route:
                self._proxy_notion(*route)
            else:
                self._json_error(404, 'Not found')

    # ── Daftra proxy (read-only GET only) ────────────────────────────────────

    def do_DELETE(self): self._block_daftra_write()
    def do_PUT(self):    self._block_daftra_write()
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
                    print(f'  [purch]  combine:  {idx:3d}/{total_files}: {fname!r} — {npages} page(s)')
                else:
                    # Image: insert into a new page that matches image dimensions
                    img_doc = fitz.open(abs_path)          # treat image as single-page doc
                    combined.insert_pdf(img_doc)
                    img_doc.close()
                    print(f'  [purch]  combine:  {idx:3d}/{total_files}: {fname!r} — 1 page (image)')
            except Exception as e:
                errors.append(f'{fname}: {e}')
                print(f'  [purch]  combine:  {idx:3d}/{total_files}: {fname!r} — SKIPPED: {e}')

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
    print()
    print('  Press Ctrl+C to stop.')
    print()

    try:
        server = ThreadingHTTPServer((BIND, PORT), VistaProxyHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Proxy stopped.')
    except OSError as e:
        print(f'\n  ERROR: Could not start server on port {PORT}: {e}')
        print(f'  Try changing "port" in config.json to a free port (e.g. 8081).')
        sys.exit(1)
