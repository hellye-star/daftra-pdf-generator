"""
Supplier Quotation Intelligence — local AI proxy.

Isolated from proxy.py (Daftra) on purpose: this process's only job is to
hold the AI provider's API key server-side and forward structured
evaluation data to it for a critical procurement review. The browser-side
module (supplier-quotation-intelligence.html) never sees, stores, or
transmits the key — it only calls this local endpoint.

Run it:
    setx ANTHROPIC_API_KEY "your-key-here"      (Windows, once, new shell after)
    python sqi_ai_proxy.py

Then in the module's AI Assessment tab, set the endpoint to
http://localhost:8092 (this is also the default the module tries).

Standard library only — no pip install needed. If ANTHROPIC_API_KEY is not
set, every request responds with {"available": false, ...} and the module
falls back to local deterministic analysis, exactly as if this server
were not running at all.
"""

import json
import os
import re
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Bumped whenever the prompt/schema/parsing logic changes in a way that
# matters to a running instance vs a freshly-started one — /api/status
# reports this so the frontend (and proxy.py's auto-start check) can tell
# a stale already-running process apart from the current code on disk,
# instead of silently trusting whatever happens to already be on the port.
BUILD_VERSION = 'concise-ai-v2'

PORT = int(os.environ.get('SQI_AI_PROXY_PORT', '8092'))
MODEL = os.environ.get('SQI_AI_MODEL', 'claude-sonnet-5')
ANTHROPIC_VERSION = '2023-06-01'
ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'
# A ceiling, not the fix — the real fix is the tightened prompt/schema below
# (short, capped fields instead of open-ended per-supplier paragraphs). At
# the word limits in the schema, a full response comfortably fits well
# under this; it exists as a backstop so a runaway response still gets cut
# (and salvaged, see repair_truncated_json) rather than burning unlimited
# tokens.
MAX_TOKENS = 3000

# Hard caps enforced server-side on top of the prompt's own limits (belt
# and braces — a model does not always obey word counts exactly).
LIMITS = {
    'narrative_words': 150,
    'reasoning_words': 120,
    'points_per_supplier': 3,
    'mainRisks': 5,
    'missingScope': 5,
    'priceConcerns': 5,
    'clarificationQuestions': 6,
    'evidenceUsed': 6,
}

SYSTEM_PROMPT = """You are a critical procurement analyst reviewing competing supplier quotations for Vista United Co against a client's requested scope. You receive structured JSON, never raw documents.

Ground every claim in the structured data given (scope compliance, charges, historical prices, math validation, normalized totals). Do not invent figures. You are also given `deterministicRecommendation` (Vista's local scoring engine's pick) and each supplier's `deterministicScoreBreakdown` — weigh these, agree or disagree, do not just repeat them.

BE CONCISE. This is a short executive brief, not a report. Never repeat the same point in multiple fields. Respond with ONLY valid JSON (no markdown fences, no prose outside the JSON), strictly matching this shape and these hard limits:
{
  "narrative": "overview, max 150 words",
  "recommendedSupplier": { "supplierName": "string", "confidence": "High|Medium|Low", "reasoning": "max 120 words — why this supplier and whether you agree with the deterministic pick" },
  "perSupplier": [
    { "supplierName": "string", "points": ["at most 3 short one-line points (position, key driver, and one risk/caveat) — no paragraphs, no repeating the narrative"] }
  ],
  "bestComparableTotal": { "supplierName": "string", "reasoning": "one short sentence" },
  "mainRisks": ["short string, at most 5 items total"],
  "missingScope": ["short string, at most 5 items total"],
  "priceConcerns": ["short string, at most 5 items total"],
  "evidenceUsed": ["short string naming a data field used, at most 6 items total"],
  "clarificationQuestions": ["short string, at most 6 items total"]
}

Every array has a hard maximum — pick only the most important items, do not pad. Every prose field has a hard word maximum — stop well within it. If you are at risk of running out of room, shorten or drop lower-priority array items FIRST, never leave the JSON unclosed."""


def call_anthropic(api_key, payload):
    body = json.dumps({
        'model': MODEL,
        'max_tokens': MAX_TOKENS,
        'system': SYSTEM_PROMPT,
        'messages': [{'role': 'user', 'content': json.dumps(payload)}]
    }).encode('utf-8')
    req = urllib.request.Request(ANTHROPIC_URL, data=body, method='POST', headers={
        'content-type': 'application/json',
        'x-api-key': api_key,
        'anthropic-version': ANTHROPIC_VERSION
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode('utf-8'))


def repair_truncated_json(text):
    """Best-effort repair of JSON cut off mid-generation: walks the text
    tracking open strings/objects/arrays, then closes whatever was still
    open at the point it stopped. This lets whatever fields DID complete
    (narrative and recommendedSupplier come first in the schema, so are
    the most likely to have finished) still parse instead of the entire
    response being discarded."""
    stack = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in '{[':
                stack.append(ch)
            elif ch in '}]':
                if stack:
                    stack.pop()
    repaired = text
    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += '}' if opener == '{' else ']'
    return repaired


def strip_stray_fragments(text):
    """Clears one specific malformed-JSON artifact seen in practice: a
    bare string literal with no ':' value immediately before a closing
    OBJECT brace (e.g. the model echoing a stray `,"confidence: High"`
    fragment instead of a proper key/value pair, usually right after a
    prose field that already stated the same thing). Deliberately scoped
    to `}` only, never `]` — a bare string immediately before `]` is a
    perfectly legitimate last array element (e.g. the final item in
    mainRisks/clarificationQuestions/etc), and must never be stripped."""
    return re.sub(r',\s*"[^"]*"\s*(?=\})', '', text)


def parse_critique_text(text):
    """Tries the raw text first, then a version with stray trailing
    fragments stripped. Returns (dict_or_None, was_repaired). A dict here
    means the JSON is fully intact — nothing was lost, so this is NOT the
    same as the truncation-salvage path below."""
    for candidate in (text, strip_stray_fragments(text)):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj, candidate != text
        except (json.JSONDecodeError, ValueError):
            continue
    return None, False


def _truncate_words(text, max_words):
    if not isinstance(text, str):
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    return ' '.join(words[:max_words]) + '…'


def enforce_limits(critique):
    """Server-side backstop on top of the prompt's own word/item limits —
    a model does not always obey instructions exactly. Trims rather than
    rejects, so the response is still short AND still fully usable."""
    if not isinstance(critique, dict):
        return critique
    if isinstance(critique.get('narrative'), str):
        critique['narrative'] = _truncate_words(critique['narrative'], LIMITS['narrative_words'])
    rec = critique.get('recommendedSupplier')
    if isinstance(rec, dict) and isinstance(rec.get('reasoning'), str):
        rec['reasoning'] = _truncate_words(rec['reasoning'], LIMITS['reasoning_words'])
    if isinstance(critique.get('perSupplier'), list):
        for p in critique['perSupplier']:
            if isinstance(p, dict) and isinstance(p.get('points'), list):
                p['points'] = [str(pt) for pt in p['points'][:LIMITS['points_per_supplier']]]
    for key in ('mainRisks', 'missingScope', 'priceConcerns', 'clarificationQuestions', 'evidenceUsed'):
        if isinstance(critique.get(key), list):
            critique[key] = critique[key][:LIMITS[key]]
    return critique


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'content-type')

    def _json(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip('/') == '/api/status':
            key = os.environ.get('ANTHROPIC_API_KEY', '')
            self._json(200, {'available': bool(key), 'state': 'ready' if key else 'key_not_configured',
                              'provider': 'anthropic', 'model': MODEL,
                              'build': BUILD_VERSION, 'maxTokens': MAX_TOKENS, 'pid': os.getpid(),
                              'reason': None if key else 'ANTHROPIC_API_KEY is not set in this server\'s environment.'})
            return
        self._json(404, {'error': 'not found'})

    def do_POST(self):
        if self.path.rstrip('/') != '/api/ai-critique':
            self._json(404, {'error': 'not found'})
            return
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            self._json(200, {'available': False, 'state': 'key_not_configured', 'reason': 'ANTHROPIC_API_KEY is not set in this server\'s environment. Set it and restart sqi_ai_proxy.py to enable AI review.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, json.JSONDecodeError):
            self._json(400, {'available': False, 'state': 'request_failed', 'reason': 'Malformed request body.'})
            return
        try:
            result = call_anthropic(key, payload)
            stop_reason = result.get('stop_reason')
            text = ''.join(block.get('text', '') for block in result.get('content', []) if block.get('type') == 'text')
            cut_off_reason = None
            if stop_reason == 'max_tokens':
                cut_off_reason = ('The AI response was cut off before it finished (hit the ' + str(MAX_TOKENS) +
                                   '-token limit).')
            # Tier 1: fully intact JSON, optionally after clearing a stray
            # trailing fragment (nothing lost either way — treated as a
            # full, non-partial success).
            critique, _was_repaired = parse_critique_text(text)
            if critique is not None:
                # Stop/validation logic: even a fully-parsed response is
                # trimmed to the agreed limits before it ever reaches the
                # frontend — the prompt asks for this, this is the backstop.
                critique = enforce_limits(critique)
                self._json(200, {'available': True, 'state': 'ready', 'critique': critique})
                return
            # Tier 2: cut off mid-generation (or otherwise malformed beyond
            # a stray fragment) — try to SALVAGE whatever fields did
            # complete (narrative and recommendedSupplier come first in the
            # schema, so are the most likely survivors) rather than losing
            # the whole response outright. Credits were already spent on
            # this call, so the goal is to keep as much of it as possible.
            salvaged = None
            for candidate_text in (text, strip_stray_fragments(text)):
                try:
                    repaired = json.loads(repair_truncated_json(candidate_text))
                    if isinstance(repaired, dict) and (repaired.get('narrative') or repaired.get('recommendedSupplier')):
                        salvaged = enforce_limits(repaired)
                        break
                except (json.JSONDecodeError, ValueError):
                    continue
            if salvaged is not None:
                reason = (cut_off_reason or 'The AI response could not be fully parsed.') + ' Showing the part that did complete.'
                self._json(200, {'available': True, 'state': 'ready', 'salvaged': True,
                                  'reason': reason, 'rawText': text, 'critique': salvaged})
            else:
                # Tier 3: nothing usable could be recovered at all.
                reason = cut_off_reason or 'The AI response could not be parsed as JSON.'
                self._json(200, {'available': True, 'state': 'ready', 'parseError': True,
                                  'reason': reason, 'rawText': text, 'critique': None})
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', errors='replace')[:500]
            self._json(200, {'available': False, 'state': 'request_failed', 'reason': 'Anthropic API returned an error (HTTP ' + str(e.code) + '): ' + detail})
        except urllib.error.URLError as e:
            self._json(200, {'available': False, 'state': 'request_failed', 'reason': 'Could not reach the Anthropic API: ' + str(e.reason)})
        except Exception as e:  # noqa: BLE001 — surface unexpected errors to the caller instead of a bare 500
            self._json(200, {'available': False, 'state': 'request_failed', 'reason': 'Unexpected proxy error: ' + str(e)})

    def log_message(self, fmt, *args):
        # Quiet by default; uncomment to debug. Never logs the API key or
        # full request bodies (which may contain client-confidential scope).
        pass


if __name__ == '__main__':
    has_key = bool(os.environ.get('ANTHROPIC_API_KEY'))
    print('SQI AI proxy listening on http://localhost:%d' % PORT)
    print('Build: %s | MAX_TOKENS: %d | PID: %d' % (BUILD_VERSION, MAX_TOKENS, os.getpid()))
    print('ANTHROPIC_API_KEY: %s' % ('configured' if has_key else 'NOT SET — AI review will report unavailable until you set it'))
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
