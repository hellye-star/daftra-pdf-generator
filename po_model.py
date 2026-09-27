"""
Vista Platform — PO Generator: draft validation and decimal-safe calculations.

Pure functions only (no I/O). Used by po_storage_api.py on every save so the
server — not the browser — is the authority on what a valid draft is and what
its totals are. po-generator.html mirrors the same calculation rules for live
display; the server result returned after each save is the reference.

Record layers (planned across phases — only the DRAFT layer exists in Phase 1):
    1. original source document   (Phase 2: stored unchanged, hash-addressed)
    2. immutable extraction        (Phase 2: evidence per field, never edited)
    3. editable PO draft           (Phase 1: this module)
    4. frozen issued snapshot      (Phase 4: atomic numbering, unique constraint,
                                    idempotency key, never modified after issue)

Rounding policy (explicit, identical in UI and server):
    * quantity: > 0, at most 3 decimal places
    * unit price: >= 0, at most 4 decimal places
    * line amount = quantity x unit price, rounded ONCE to the currency's
      minor unit, ROUND_HALF_UP
    * tax is calculated per line and rounded per line, ROUND_HALF_UP
        - prices exclusive of tax: tax = round(net x rate / 100)
        - prices inclusive of tax: net = round(gross x 100 / (100 + rate)),
                                   tax = gross - net
    * document totals are sums of the rounded line values (no re-rounding)
    * payment milestones: round(total x pct / 100) for every milestone except
      the last, which takes the remainder so milestones always sum to total
    * nothing is rounded until a currency is chosen; no currency = unresolved
"""
import re
from fractions import Fraction

SCHEMA_VERSION = 1

# ISO 4217 minor units for the currencies the PO Generator accepts.
CURRENCY_MINOR_UNITS = {
    'SAR': 2, 'USD': 2, 'EUR': 2, 'GBP': 2, 'AED': 2, 'QAR': 2, 'EGP': 2,
    'CNY': 2, 'INR': 2, 'TRY': 2, 'KWD': 3, 'BHD': 3, 'OMR': 3, 'JOD': 3,
    'JPY': 0,
}

TAX_TREATMENTS = ('unresolved', 'taxable', 'zero_rated', 'exempt', 'out_of_scope')

# Where an item's tax treatment came from. Shown in the UI so a default is
# never mistaken for a confirmed or extracted value.
#   'default' — proposed standard VAT for a new item; editable
#   'user'    — set or changed by the user
#   ''        — legacy/unknown (items saved before origins existed; kept as-is)
# Phase 2 (quotation import) will add 'extracted' for a tax treatment the
# supplier's document clearly states; when the document states none, the
# importer proposes DEFAULT_ITEM_TAX with origin 'default' and flags it for
# review — it is never presented as extracted.
TAX_ORIGINS = ('', 'default', 'user')
DEFAULT_ITEM_TAX = {'treatment': 'taxable', 'rate': '15', 'origin': 'default'}
PRICE_TAX_BASES = ('unresolved', 'exclusive', 'inclusive')
BALANCE_TRIGGERS = ('undecided', 'delivery', 'delivery_written_acceptance')
ITEM_SOURCES = ('manual',)          # Phase 2 adds 'extracted'

DRAFT_ID_RE = re.compile(r'^po_[A-Za-z0-9]{10,40}$')
ITEM_ID_RE = re.compile(r'^it_[A-Za-z0-9]{6,40}$')
MILESTONE_ID_RE = re.compile(r'^ms_[A-Za-z0-9]{2,40}$')
DAFTRA_ID_RE = re.compile(r'^\d{1,12}$')
_DEC_RE = re.compile(r'^\d{1,15}(\.\d{1,8})?$')

# Arabic-Indic and Eastern Arabic-Indic digits + Arabic decimal separator.
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate('٠١٢٣٤٥٦٧٨٩')}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate('۰۱۲۳۴۵۶۷۸۹')})
_DIGIT_MAP[ord('٫')] = '.'

# Only these supplier-snapshot fields are ever stored (never password,
# last_ip, last_login or any other account field Daftra returns).
SUPPLIER_SNAPSHOT_KEYS = (
    'businessName', 'firstName', 'lastName', 'email', 'phone1', 'phone2',
    'address1', 'address2', 'city', 'state', 'postalCode', 'countryCode',
    'vatNumber', 'vatLabel', 'crNumber', 'crLabel', 'defaultCurrency',
)

TOP_LEVEL_KEYS = {
    'id', 'schema', 'status', 'title', 'currency', 'priceTaxBasis', 'supplier',
    'items', 'paymentTerms', 'projectRef', 'deliveryLocation', 'notes',
    'createdAt', 'updatedAt',
}

LIMITS = {
    'title': 200, 'description': 5000, 'unit': 40, 'numeric': 32,
    'projectRef': 200, 'deliveryLocation': 500, 'notes': 5000,
    'paymentText': 2000, 'milestoneLabel': 200, 'snapshotField': 500,
    'supplierName': 300, 'supplierNumber': 64, 'timestamp': 40,
    'items': 500, 'milestones': 6,
}

MAX_QTY = Fraction(10**9)
MAX_PRICE = Fraction(10**12)


class DraftInvalid(ValueError):
    def __init__(self, errors):
        super().__init__('; '.join(f"{e['path']}: {e['message']}" for e in errors))
        self.errors = errors


# ── decimal helpers ──────────────────────────────────────────────────────────

def normalize_numeric(s):
    """Arabic-Indic digits → ASCII, strip surrounding spaces. Returns str."""
    return str(s).translate(_DIGIT_MAP).strip()


def parse_decimal(s, max_dp):
    """Strict non-negative decimal string → Fraction. None if malformed or
    more than max_dp decimal places. No signs, exponents, commas or spaces."""
    s = normalize_numeric(s)
    if not _DEC_RE.match(s):
        return None
    if '.' in s and len(s.split('.')[1]) > max_dp:
        return None
    return Fraction(s)


def round_half_up(value, dp):
    """Fraction (>= 0) → int count of minor units, ROUND_HALF_UP."""
    scaled = value * (10 ** dp)
    return int((scaled * 2 + 1) // 2) if scaled >= 0 else -int((-scaled * 2 + 1) // 2)


def fmt_minor(minor, dp):
    """int minor units → fixed-point string with dp decimals."""
    neg = minor < 0
    minor = abs(minor)
    if dp == 0:
        s = str(minor)
    else:
        s = str(minor).rjust(dp + 1, '0')
        s = s[:-dp] + '.' + s[-dp:]
    return ('-' if neg else '') + s


def fmt_exact(value):
    """Fraction with a terminating decimal expansion → plain string."""
    if value.denominator == 1:
        return str(value.numerator)
    for dp in range(1, 20):
        scaled = value * (10 ** dp)
        if scaled.denominator == 1:
            return fmt_minor(int(scaled), dp)
    return str(float(value))


# ── validation ───────────────────────────────────────────────────────────────

def _str(errors, path, v, limit, required=False):
    if v is None:
        v = ''
    if not isinstance(v, str):
        errors.append({'path': path, 'message': 'must be text'})
        return ''
    if '\x00' in v:
        errors.append({'path': path, 'message': 'contains a NUL character'})
        return ''
    if len(v) > limit:
        errors.append({'path': path, 'message': f'is longer than {limit} characters'})
    if required and not v.strip():
        errors.append({'path': path, 'message': 'is required'})
    return v


def _num_field(errors, path, v, max_dp, allow_zero, maximum, label):
    v = _str(errors, path, v, LIMITS['numeric'])
    v = normalize_numeric(v)
    if v == '':
        return ''
    f = parse_decimal(v, max_dp)
    if f is None:
        errors.append({'path': path, 'message':
                       f'{label} must be a plain number with at most {max_dp} decimal places '
                       f'(digits and one dot; no commas, signs or spaces)'})
        return v
    if not allow_zero and f == 0:
        errors.append({'path': path, 'message': f'{label} must be greater than zero'})
    if f > maximum:
        errors.append({'path': path, 'message': f'{label} is unrealistically large'})
    return v


def validate_draft(doc):
    """Validate + normalize a draft document. Returns the cleaned document or
    raises DraftInvalid with a list of {path, message}. Never invents values:
    blanks stay blank and are reported by readiness(), not filled in."""
    errors = []
    if not isinstance(doc, dict):
        raise DraftInvalid([{'path': '$', 'message': 'draft must be a JSON object'}])

    unknown = sorted(set(doc) - TOP_LEVEL_KEYS)
    for k in unknown:
        errors.append({'path': k, 'message': 'is not a recognised draft field'})

    out = {}
    did = doc.get('id')
    if not isinstance(did, str) or not DRAFT_ID_RE.match(did):
        errors.append({'path': 'id', 'message': 'invalid draft id'})
    out['id'] = did
    if doc.get('schema', SCHEMA_VERSION) != SCHEMA_VERSION:
        errors.append({'path': 'schema', 'message': f'unsupported schema (expected {SCHEMA_VERSION})'})
    out['schema'] = SCHEMA_VERSION
    if doc.get('status', 'draft') != 'draft':
        errors.append({'path': 'status', 'message': 'only drafts can be saved (issuance is not available)'})
    out['status'] = 'draft'

    out['title'] = _str(errors, 'title', doc.get('title'), LIMITS['title'])
    cur = doc.get('currency') or ''
    if cur and cur not in CURRENCY_MINOR_UNITS:
        errors.append({'path': 'currency', 'message': 'unsupported currency code'})
    out['currency'] = cur
    basis = doc.get('priceTaxBasis') or 'unresolved'
    if basis not in PRICE_TAX_BASES:
        errors.append({'path': 'priceTaxBasis', 'message': 'invalid value'})
    out['priceTaxBasis'] = basis

    # supplier (optional in a draft; reported by readiness when missing)
    sup = doc.get('supplier')
    if sup is None:
        out['supplier'] = None
    elif not isinstance(sup, dict):
        errors.append({'path': 'supplier', 'message': 'must be an object or null'})
        out['supplier'] = None
    else:
        s = {}
        sid = str(sup.get('daftraId') or '')
        if not DAFTRA_ID_RE.match(sid):
            errors.append({'path': 'supplier.daftraId', 'message': 'must be a numeric Daftra supplier id'})
        s['daftraId'] = sid
        s['name'] = _str(errors, 'supplier.name', sup.get('name'), LIMITS['supplierName'], required=True)
        s['supplierNumber'] = _str(errors, 'supplier.supplierNumber', sup.get('supplierNumber'), LIMITS['supplierNumber'])
        s['confirmedAt'] = _str(errors, 'supplier.confirmedAt', sup.get('confirmedAt'), LIMITS['timestamp'])
        snap_in = sup.get('snapshot') or {}
        if not isinstance(snap_in, dict):
            errors.append({'path': 'supplier.snapshot', 'message': 'must be an object'})
            snap_in = {}
        extra = sorted(set(snap_in) - set(SUPPLIER_SNAPSHOT_KEYS))
        for k in extra:
            errors.append({'path': f'supplier.snapshot.{k}', 'message': 'is not an allowed snapshot field'})
        s['snapshot'] = {k: _str(errors, f'supplier.snapshot.{k}', snap_in.get(k), LIMITS['snapshotField'])
                         for k in SUPPLIER_SNAPSHOT_KEYS}
        for k in sorted(set(sup) - {'daftraId', 'name', 'supplierNumber', 'confirmedAt', 'snapshot'}):
            errors.append({'path': f'supplier.{k}', 'message': 'is not a recognised supplier field'})
        out['supplier'] = s

    # items
    items_in = doc.get('items') or []
    if not isinstance(items_in, list):
        errors.append({'path': 'items', 'message': 'must be a list'})
        items_in = []
    if len(items_in) > LIMITS['items']:
        errors.append({'path': 'items', 'message': f'at most {LIMITS["items"]} items per draft'})
    seen = set()
    items = []
    for i, it in enumerate(items_in):
        p = f'items[{i}]'
        if not isinstance(it, dict):
            errors.append({'path': p, 'message': 'must be an object'})
            continue
        for k in sorted(set(it) - {'id', 'description', 'unit', 'qty', 'unitPrice', 'included',
                                    'tax', 'source', 'createdAt', 'excludedAt'}):
            errors.append({'path': f'{p}.{k}', 'message': 'is not a recognised item field'})
        iid = it.get('id')
        if not isinstance(iid, str) or not ITEM_ID_RE.match(iid):
            errors.append({'path': f'{p}.id', 'message': 'invalid item id'})
        elif iid in seen:
            errors.append({'path': f'{p}.id', 'message': 'duplicate item id'})
        seen.add(iid)
        included = it.get('included', True)
        if not isinstance(included, bool):
            errors.append({'path': f'{p}.included', 'message': 'must be true or false'})
            included = True
        source = it.get('source') or 'manual'
        if source not in ITEM_SOURCES:
            errors.append({'path': f'{p}.source', 'message': 'invalid source'})
        tax_in = it.get('tax') or {}
        if not isinstance(tax_in, dict):
            errors.append({'path': f'{p}.tax', 'message': 'must be an object'})
            tax_in = {}
        for k in sorted(set(tax_in) - {'treatment', 'rate', 'origin'}):
            errors.append({'path': f'{p}.tax.{k}', 'message': 'is not a recognised tax field'})
        treat = tax_in.get('treatment') or 'unresolved'
        if treat not in TAX_TREATMENTS:
            errors.append({'path': f'{p}.tax.treatment', 'message': 'invalid tax treatment'})
        origin = tax_in.get('origin') or ''
        if origin not in TAX_ORIGINS:
            errors.append({'path': f'{p}.tax.origin', 'message': 'invalid tax origin'})
        rate = _str(errors, f'{p}.tax.rate', tax_in.get('rate'), LIMITS['numeric'])
        rate = normalize_numeric(rate)
        if treat == 'taxable':
            rf = parse_decimal(rate, 2) if rate else None
            if rate and (rf is None or rf <= 0 or rf > 100):
                errors.append({'path': f'{p}.tax.rate', 'message':
                               'tax rate must be a number above 0 and at most 100, with at most 2 decimals'})
        else:
            rate = ''   # a rate only means something for taxable lines
        items.append({
            'id': iid,
            'description': _str(errors, f'{p}.description', it.get('description'), LIMITS['description']),
            'unit': _str(errors, f'{p}.unit', it.get('unit'), LIMITS['unit']),
            'qty': _num_field(errors, f'{p}.qty', it.get('qty'), 3, False, MAX_QTY, 'Quantity'),
            'unitPrice': _num_field(errors, f'{p}.unitPrice', it.get('unitPrice'), 4, True, MAX_PRICE, 'Unit price'),
            'included': included,
            'tax': {'treatment': treat, 'rate': rate, 'origin': origin},
            'source': source,
            'createdAt': _str(errors, f'{p}.createdAt', it.get('createdAt'), LIMITS['timestamp']),
            'excludedAt': _str(errors, f'{p}.excludedAt', it.get('excludedAt'), LIMITS['timestamp']),
        })
    out['items'] = items

    # payment terms (editable draft default; trigger stays undecided until chosen)
    pt = doc.get('paymentTerms') or {}
    if not isinstance(pt, dict):
        errors.append({'path': 'paymentTerms', 'message': 'must be an object'})
        pt = {}
    trig = pt.get('balanceTrigger') or 'undecided'
    if trig not in BALANCE_TRIGGERS:
        errors.append({'path': 'paymentTerms.balanceTrigger', 'message': 'invalid value'})
    ms_in = pt.get('milestones') or []
    if not isinstance(ms_in, list) or len(ms_in) > LIMITS['milestones']:
        errors.append({'path': 'paymentTerms.milestones', 'message': f'must be a list of at most {LIMITS["milestones"]}'})
        ms_in = []
    ms = []
    for j, m in enumerate(ms_in):
        mp = f'paymentTerms.milestones[{j}]'
        if not isinstance(m, dict):
            errors.append({'path': mp, 'message': 'must be an object'})
            continue
        mid = m.get('id')
        if not isinstance(mid, str) or not MILESTONE_ID_RE.match(mid):
            errors.append({'path': f'{mp}.id', 'message': 'invalid milestone id'})
        pct = normalize_numeric(_str(errors, f'{mp}.pct', m.get('pct'), LIMITS['numeric']))
        pf = parse_decimal(pct, 2) if pct else None
        if pct and (pf is None or pf <= 0 or pf > 100):
            errors.append({'path': f'{mp}.pct', 'message': 'percentage must be above 0 and at most 100 (2 decimals max)'})
        ms.append({'id': mid, 'pct': pct,
                   'label': _str(errors, f'{mp}.label', m.get('label'), LIMITS['milestoneLabel'])})
    out['paymentTerms'] = {
        'text': _str(errors, 'paymentTerms.text', pt.get('text'), LIMITS['paymentText']),
        'isDraftDefault': bool(pt.get('isDraftDefault', False)),
        'balanceTrigger': trig,
        'milestones': ms,
    }
    for k in sorted(set(pt) - {'text', 'isDraftDefault', 'balanceTrigger', 'milestones'}):
        errors.append({'path': f'paymentTerms.{k}', 'message': 'is not a recognised field'})

    out['projectRef'] = _str(errors, 'projectRef', doc.get('projectRef'), LIMITS['projectRef'])
    out['deliveryLocation'] = _str(errors, 'deliveryLocation', doc.get('deliveryLocation'), LIMITS['deliveryLocation'])
    out['notes'] = _str(errors, 'notes', doc.get('notes'), LIMITS['notes'])
    out['createdAt'] = _str(errors, 'createdAt', doc.get('createdAt'), LIMITS['timestamp'])
    out['updatedAt'] = _str(errors, 'updatedAt', doc.get('updatedAt'), LIMITS['timestamp'])

    if errors:
        raise DraftInvalid(errors)
    return out


# ── calculations ─────────────────────────────────────────────────────────────

def compute(doc):
    """Server-authoritative totals for a VALIDATED draft. Values are strings
    (fixed-point in the currency's minor unit) or None when unresolved."""
    cur = doc.get('currency') or ''
    dp = CURRENCY_MINOR_UNITS.get(cur)
    basis = doc.get('priceTaxBasis') or 'unresolved'
    lines = []
    invalid_items, tax_unresolved_items = [], []
    net_sum = tax_sum = gross_sum = 0
    tax_groups = {}
    for it in doc.get('items', []):
        row = {'id': it['id'], 'included': it['included'], 'exactAmount': None,
               'lineAmount': None, 'net': None, 'tax': None, 'gross': None, 'issues': []}
        q = parse_decimal(it['qty'], 3) if it['qty'] else None
        p = parse_decimal(it['unitPrice'], 4) if it['unitPrice'] != '' else None
        if not it['description'].strip():
            row['issues'].append('description missing')
        if not it['unit'].strip():
            row['issues'].append('unit missing')
        if q is None or q <= 0:
            row['issues'].append('quantity missing')
        if p is None:
            row['issues'].append('unit price missing')
        if q is not None and p is not None:
            row['exactAmount'] = fmt_exact(q * p)
        treat = it['tax']['treatment']
        rate = parse_decimal(it['tax']['rate'], 2) if it['tax']['rate'] else None
        if treat == 'unresolved':
            row['issues'].append('tax treatment unresolved')
        elif treat == 'taxable' and rate is None:
            row['issues'].append('tax rate missing')
        elif treat == 'taxable' and basis == 'unresolved':
            row['issues'].append('price tax basis (inclusive/exclusive) unresolved')

        if it['included']:
            if any(x.endswith('missing') and 'tax' not in x for x in row['issues']):
                invalid_items.append(it['id'])
            elif any('tax' in x for x in row['issues']):
                tax_unresolved_items.append(it['id'])

        if dp is not None and q is not None and p is not None:
            amount = round_half_up(q * p, dp)
            row['lineAmount'] = fmt_minor(amount, dp)
            tax_ok = treat in ('zero_rated', 'exempt', 'out_of_scope') or (
                treat == 'taxable' and rate is not None and basis != 'unresolved')
            if tax_ok:
                if treat != 'taxable':
                    net, tax = amount, 0
                elif basis == 'exclusive':
                    net = amount
                    tax = round_half_up(Fraction(amount, 10 ** dp) * rate / 100, dp)
                else:  # inclusive
                    net = round_half_up(Fraction(amount, 10 ** dp) * 100 / (100 + rate), dp)
                    tax = amount - net
                row['net'], row['tax'], row['gross'] = fmt_minor(net, dp), fmt_minor(tax, dp), fmt_minor(net + tax, dp)
                if it['included']:
                    net_sum += net
                    tax_sum += tax
                    gross_sum += net + tax
                    key = treat + (':' + fmt_exact(rate) if treat == 'taxable' else '')
                    g = tax_groups.setdefault(key, {'treatment': treat,
                                                    'rate': fmt_exact(rate) if treat == 'taxable' else '',
                                                    'net': 0, 'tax': 0})
                    g['net'] += net
                    g['tax'] += tax
        lines.append(row)

    included = [it for it in doc.get('items', []) if it['included']]
    if dp is None:
        status = 'currency_unresolved'
    elif not included:
        status = 'no_items'
    elif invalid_items:
        status = 'incomplete'
    elif tax_unresolved_items:
        status = 'tax_unresolved'
    else:
        status = 'complete'
    complete = status == 'complete'
    totals = {
        'status': status,
        'currency': cur,
        'minorUnits': dp,
        'net': fmt_minor(net_sum, dp) if complete else None,
        'tax': fmt_minor(tax_sum, dp) if complete else None,
        'gross': fmt_minor(gross_sum, dp) if complete else None,
        'taxGroups': [{'treatment': g['treatment'], 'rate': g['rate'],
                       'net': fmt_minor(g['net'], dp), 'tax': fmt_minor(g['tax'], dp)}
                      for g in tax_groups.values()] if complete else [],
        'invalidItems': invalid_items,
        'taxUnresolvedItems': tax_unresolved_items,
        'includedCount': len(included),
        'excludedCount': len(doc.get('items', [])) - len(included),
    }

    # payment milestones on the confirmed total only
    ms_out = []
    ms = doc.get('paymentTerms', {}).get('milestones', [])
    pcts = [parse_decimal(m['pct'], 2) if m['pct'] else None for m in ms]
    pct_ok = bool(ms) and all(x is not None for x in pcts) and sum(pcts) == 100
    if complete and pct_ok:
        allocated = 0
        for k, (m, pf) in enumerate(zip(ms, pcts)):
            amt = gross_sum - allocated if k == len(ms) - 1 else round_half_up(Fraction(gross_sum, 10 ** dp) * pf / 100, dp)
            allocated += amt
            ms_out.append({'id': m['id'], 'pct': m['pct'], 'amount': fmt_minor(amt, dp)})
    else:
        ms_out = [{'id': m['id'], 'pct': m['pct'], 'amount': None} for m in ms]
    return {'lines': lines, 'totals': totals, 'milestones': ms_out, 'milestonePctValid': pct_ok}


def readiness(doc, computed):
    """Everything that blocks FINAL issuance. Phase 1 never issues; the list
    exists so drafts show exactly what is still missing. An acknowledged
    discrepancy (Phase 3) will never clear the 'essential' blockers."""
    b = []

    def add(code, msg, essential=True):
        b.append({'code': code, 'message': msg, 'essential': essential})

    add('issuance_not_available', 'Issuing POs is not available in Phase 1 (drafts only).')
    add('buyer_unconfirmed', 'Buyer company name, VAT number and address have not been confirmed.')
    add('po_number_format_unconfirmed', 'PO number format and starting number have not been confirmed.')
    if not doc.get('supplier'):
        add('supplier_missing', 'No Daftra supplier selected.')
    if not doc.get('currency'):
        add('currency_missing', 'Currency not selected.')
    t = computed['totals']
    if t['includedCount'] == 0:
        add('no_included_items', 'No included items.')
    if t['invalidItems']:
        add('item_invalid', f'{len(t["invalidItems"])} included item(s) missing description, unit, quantity or unit price.')
    if t['taxUnresolvedItems']:
        add('tax_unresolved', f'Tax treatment unresolved on {len(t["taxUnresolvedItems"])} included item(s).')
    pt = doc.get('paymentTerms', {})
    if pt.get('balanceTrigger', 'undecided') == 'undecided':
        add('payment_trigger_undecided', 'Balance payment trigger (delivery vs delivery + written acceptance) not decided.')
    if not computed['milestonePctValid']:
        add('payment_pct_invalid', 'Payment milestone percentages must be filled in and add up to 100%.')
    return b
