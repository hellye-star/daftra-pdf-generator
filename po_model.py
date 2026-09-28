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
#   'extracted' — the supplier's document clearly states it (row tax cell or
#                 a document-level statement); evidence is in the extraction
# When an imported quotation states no tax treatment, the importer proposes
# DEFAULT_ITEM_TAX with origin 'default' plus the review flag
# DEFAULT_TAX_REVIEW_FLAG — never presented as extracted.
TAX_ORIGINS = ('', 'default', 'user', 'extracted')
DEFAULT_ITEM_TAX = {'treatment': 'taxable', 'rate': '15', 'origin': 'default'}
DEFAULT_TAX_REVIEW_FLAG = 'default_tax_requires_review'
PRICE_TAX_BASES = ('unresolved', 'exclusive', 'inclusive')
BALANCE_TRIGGERS = ('undecided', 'delivery', 'delivery_written_acceptance')
ITEM_SOURCES = ('manual', 'extracted')

# Review flags an imported item may carry (copied from the extraction; the
# user clears the tax one by confirming/changing the tax treatment).
REVIEW_FLAGS = {
    DEFAULT_TAX_REVIEW_FLAG, 'low_confidence', 'ambiguous_number', 'unreadable_number',
    'line_total_mismatch', 'line_total_may_include_tax', 'lump_sum', 'lump_sum_or_missing_breakdown',
    'possible_duplicate', 'multiline', 'no_numbers', 'optional_selected', 'alternative_selected',
    'unit_from_quantity_cell', 'continues_next_page', 'tax_amount_without_rate', 'line_discount_present',
    'same_as_existing_item', 'document_adjustments_unapplied', 'from_incomplete_extraction',
    'spec_dimension_conflict', 'dash_amount', 'no_amount', 'same_values_as_other_row', 'component_of_item',
}
ORIG_KEYS = ('ref', 'description', 'unit', 'qty', 'unitPrice', 'discount', 'lineTotal', 'tax', 'occurrence',
             'parent', 'dimensions', 'proposedDescription')
# Flags meaning the quotation has price adjustments the PO does not apply yet:
# PO totals are then provisional and must not be read as the supplier's payable total.
ADJUSTMENT_FLAGS = ('line_discount_present', 'document_adjustments_unapplied')
SOURCE_REF_RE = {'sourceId': re.compile(r'^src_[0-9a-f]{24}$'), 'extractionId': re.compile(r'^ex_[0-9a-f]{24}$'),
                 'rowId': re.compile(r'^[ro]\d{1,5}$')}
QUOTATION_STR_KEYS = (
    'sourceId', 'extractionId', 'appliedAt', 'ref', 'dateRaw', 'dateIso', 'dateHijri', 'dateCalendar',
    'validity', 'supplierName', 'supplierVat', 'supplierCr', 'supplierEmail', 'supplierPhone',
    'currency', 'currencyRaw', 'paymentTerms', 'delivery', 'taxBasis', 'taxRate', 'taxStatementRaw',
    'exclusions', 'notes', 'customerName', 'projectName')
QUOTATION_TOTAL_KEYS = ('subtotal', 'discount', 'delivery', 'installation', 'vat', 'grandTotal', 'total')

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
    'createdAt', 'updatedAt', 'quotation', 'photos',
}

LIMITS = {
    'title': 200, 'description': 5000, 'unit': 40, 'numeric': 32,
    'projectRef': 200, 'deliveryLocation': 500, 'notes': 5000,
    'paymentText': 2000, 'milestoneLabel': 200, 'snapshotField': 500,
    'supplierName': 300, 'supplierNumber': 64, 'timestamp': 40,
    'items': 500, 'milestones': 6, 'photos': 500, 'photoParent': 400,
    'itemName': 300, 'dimValue': 32, 'dimUnit': 20, 'dimText': 300,
}

# Item dimensions: printed labels (W/D/H/L) with their values exactly as printed
# (a blank cell stays ''), the unit only when printed or entered by the user
# (never inferred), and a status. Conflicting source values stay
# 'needs_confirmation' until the user confirms what the PO prints.
DIM_LABELS = ('W', 'D', 'H', 'L')
DIM_STATUSES = ('as_printed', 'needs_confirmation', 'confirmed')
DIM_ORIGINS = ('extracted', 'user')

# Item photos: which stored photo (po_photos) belongs to which item. A photo
# shared by several priced components of one titled item is attached to that
# parent ("group") — never guessed onto one component.
PHOTO_ID_RE = re.compile(r'^ph_[0-9a-f]{24}$')
PHOTO_ASSOC_ID_RE = re.compile(r'^pa_[A-Za-z0-9]{6,40}$')
PHOTO_STATUSES = ('suggested', 'uncertain', 'confirmed', 'removed')
PHOTO_ORIGINS = ('auto', 'user')
PHOTO_KINDS = ('embedded', 'source_crop')
PHOTO_REASON_RE = re.compile(r'^[a-z0-9_:.\- ]{1,80}$')

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
        for k in sorted(set(it) - {'id', 'name', 'description', 'unit', 'qty', 'unitPrice', 'included', 'dimensions',
                                    'tax', 'source', 'createdAt', 'excludedAt', 'sourceRef', 'orig', 'reviewFlags'}):
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
        entry = {
            'id': iid,
            'name': _str(errors, f'{p}.name', it.get('name'), LIMITS['itemName']),
            'description': _str(errors, f'{p}.description', it.get('description'), LIMITS['description']),
            'unit': _str(errors, f'{p}.unit', it.get('unit'), LIMITS['unit']),
            'qty': _num_field(errors, f'{p}.qty', it.get('qty'), 3, False, MAX_QTY, 'Quantity'),
            'unitPrice': _num_field(errors, f'{p}.unitPrice', it.get('unitPrice'), 4, True, MAX_PRICE, 'Unit price'),
            'included': included,
            'tax': {'treatment': treat, 'rate': rate, 'origin': origin},
            'source': source,
            'createdAt': _str(errors, f'{p}.createdAt', it.get('createdAt'), LIMITS['timestamp']),
            'excludedAt': _str(errors, f'{p}.excludedAt', it.get('excludedAt'), LIMITS['timestamp']),
            'dimensions': _validate_dimensions(errors, f'{p}.dimensions', it.get('dimensions')),
        }
        # imported items: link to the immutable extraction row + a copy of the
        # original extracted text (edits change the PO values, never these)
        if source == 'extracted':
            ref_in = it.get('sourceRef')
            if not isinstance(ref_in, dict) or set(ref_in) != set(SOURCE_REF_RE):
                errors.append({'path': f'{p}.sourceRef', 'message': 'imported items need sourceId, extractionId and rowId'})
                ref_in = {}
            for k, rx in SOURCE_REF_RE.items():
                if ref_in and (not isinstance(ref_in.get(k), str) or not rx.match(ref_in[k])):
                    errors.append({'path': f'{p}.sourceRef.{k}', 'message': 'invalid reference'})
            entry['sourceRef'] = {k: ref_in.get(k) for k in SOURCE_REF_RE} if ref_in else None
            orig_in = it.get('orig') or {}
            if not isinstance(orig_in, dict):
                errors.append({'path': f'{p}.orig', 'message': 'must be an object'})
                orig_in = {}
            for k in sorted(set(orig_in) - set(ORIG_KEYS)):
                errors.append({'path': f'{p}.orig.{k}', 'message': 'is not a recognised original field'})
            entry['orig'] = {k: _str(errors, f'{p}.orig.{k}', orig_in.get(k), LIMITS['description']) for k in ORIG_KEYS}
        elif it.get('sourceRef') is not None or it.get('orig') is not None:
            errors.append({'path': f'{p}.sourceRef', 'message': 'only imported items carry a source reference'})
        flags_in = it.get('reviewFlags') or []
        if not isinstance(flags_in, list) or len(flags_in) > 20 or any(f not in REVIEW_FLAGS for f in flags_in if isinstance(f, str)) \
                or any(not isinstance(f, str) for f in flags_in):
            errors.append({'path': f'{p}.reviewFlags', 'message': 'invalid review flags'})
            flags_in = []
        if flags_in:
            entry['reviewFlags'] = sorted(set(flags_in))
        items.append(entry)
    out['items'] = items
    out['photos'] = _validate_photos(errors, doc.get('photos'), {it['id'] for it in items if it.get('id')})

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

    # reviewed quotation header fields (copied from an extraction on explicit apply)
    q = doc.get('quotation')
    if q is None:
        out['quotation'] = None
    elif not isinstance(q, dict):
        errors.append({'path': 'quotation', 'message': 'must be an object or null'})
        out['quotation'] = None
    else:
        allowed = set(QUOTATION_STR_KEYS) | {'dateAmbiguous', 'totals', 'flags'}
        for k in sorted(set(q) - allowed):
            errors.append({'path': f'quotation.{k}', 'message': 'is not a recognised quotation field'})
        qo = {k: _str(errors, f'quotation.{k}', q.get(k), LIMITS['paymentText']) for k in QUOTATION_STR_KEYS}
        for k, rx in (('sourceId', SOURCE_REF_RE['sourceId']), ('extractionId', SOURCE_REF_RE['extractionId'])):
            if not rx.match(qo[k] or ''):
                errors.append({'path': f'quotation.{k}', 'message': 'invalid reference'})
        if qo['currency'] and qo['currency'] not in CURRENCY_MINOR_UNITS:
            qo['currency'] = ''          # an unsupported code stays visible in currencyRaw only
        if not isinstance(q.get('dateAmbiguous', False), bool):
            errors.append({'path': 'quotation.dateAmbiguous', 'message': 'must be true or false'})
        qo['dateAmbiguous'] = bool(q.get('dateAmbiguous', False))
        tot = q.get('totals') or {}
        if not isinstance(tot, dict):
            errors.append({'path': 'quotation.totals', 'message': 'must be an object'})
            tot = {}
        for k in sorted(set(tot) - set(QUOTATION_TOTAL_KEYS)):
            errors.append({'path': f'quotation.totals.{k}', 'message': 'is not a recognised total'})
        qo['totals'] = {k: _str(errors, f'quotation.totals.{k}', tot.get(k), LIMITS['numeric'] * 8) for k in QUOTATION_TOTAL_KEYS}
        fl = q.get('flags') or {}
        if not isinstance(fl, dict) or len(fl) > 40 or any(
                not isinstance(v, list) or len(v) > 20 or any(not isinstance(x, str) or len(x) > 80 for x in v) for v in fl.values()):
            errors.append({'path': 'quotation.flags', 'message': 'invalid flags'})
            fl = {}
        qo['flags'] = {str(k)[:40]: list(v) for k, v in fl.items()}
        out['quotation'] = qo
    out['createdAt'] = _str(errors, 'createdAt', doc.get('createdAt'), LIMITS['timestamp'])
    out['updatedAt'] = _str(errors, 'updatedAt', doc.get('updatedAt'), LIMITS['timestamp'])

    if errors:
        raise DraftInvalid(errors)
    return out


def _validate_dimensions(errors, p, d):
    if d is None:
        return None
    if not isinstance(d, dict):
        errors.append({'path': p, 'message': 'must be an object or null'})
        return None
    for k in sorted(set(d) - {'values', 'unit', 'status', 'origin', 'conflicts', 'described'}):
        errors.append({'path': f'{p}.{k}', 'message': 'is not a recognised dimensions field'})
    # measurements printed in the description, word for word: the item's own size line (main)
    # and separate parts ("BASE …", "POLE OF 1.5 M") — kept so hiding the description loses nothing
    des = d.get('described')
    out_des = {'main': '', 'additional': []}
    if des is not None:
        if not isinstance(des, dict) or set(des) - {'main', 'additional'} or not isinstance(des.get('additional', []), list) \
                or len(des.get('additional', [])) > 8:
            errors.append({'path': f'{p}.described', 'message': 'must be {main, additional: [..]} (at most 8 parts)'})
        else:
            out_des = {'main': _str(errors, f'{p}.described.main', des.get('main'), LIMITS['dimText']),
                       'additional': [_str(errors, f'{p}.described.additional[{j}]', x, LIMITS['dimText'])
                                      for j, x in enumerate(des.get('additional', []))]}
    has_des = bool(out_des['main'] or any(out_des['additional']))
    vals = d.get('values')
    out_vals = {}
    if not isinstance(vals, dict) or (not vals and not has_des) or set(vals) - set(DIM_LABELS):
        errors.append({'path': f'{p}.values', 'message': 'must map W / D / H / L to the printed values'})
    else:
        for k in DIM_LABELS:                                     # printed order W, D, H, L
            if k in vals:
                out_vals[k] = _str(errors, f'{p}.values.{k}', vals[k], LIMITS['dimValue'])
    status = d.get('status')
    if status not in DIM_STATUSES:
        errors.append({'path': f'{p}.status', 'message': 'invalid value'})
    origin = d.get('origin')
    if origin not in DIM_ORIGINS:
        errors.append({'path': f'{p}.origin', 'message': 'invalid value'})
    conflicts = d.get('conflicts') or []
    out_conf = []
    if not isinstance(conflicts, list) or len(conflicts) > 8:
        errors.append({'path': f'{p}.conflicts', 'message': 'invalid conflicts'})
    else:
        for j, c in enumerate(conflicts):
            if not isinstance(c, dict) or set(c) != {'dim', 'column', 'description'} or c.get('dim') not in DIM_LABELS:
                errors.append({'path': f'{p}.conflicts[{j}]', 'message': 'invalid conflict record'})
                continue
            out_conf.append({'dim': c['dim'], 'column': _str(errors, f'{p}.conflicts[{j}].column', c['column'], LIMITS['dimValue']),
                             'description': _str(errors, f'{p}.conflicts[{j}].description', str(c['description']) if isinstance(c['description'], (int, float)) and not isinstance(c['description'], bool) else c['description'], LIMITS['dimValue'])})
    out = {'values': out_vals, 'unit': _str(errors, f'{p}.unit', d.get('unit'), LIMITS['dimUnit']),
           'status': status, 'origin': origin, 'conflicts': out_conf}
    if des is not None:
        out['described'] = out_des
    return out


def _validate_photos(errors, photos_in, item_ids):
    if photos_in is None:
        return []
    if not isinstance(photos_in, list) or len(photos_in) > LIMITS['photos']:
        errors.append({'path': 'photos', 'message': f'must be a list of at most {LIMITS["photos"]}'})
        return []
    out, seen_ids, seen_photos = [], set(), set()
    allowed = {'id', 'photoId', 'sourceId', 'page', 'region', 'kind', 'target', 'status', 'origin',
               'includeInPdf', 'reasons', 'suggestion', 'updatedAt'}
    for j, a in enumerate(photos_in):
        p = f'photos[{j}]'
        if not isinstance(a, dict):
            errors.append({'path': p, 'message': 'must be an object'})
            continue
        for k in sorted(set(a) - allowed):
            errors.append({'path': f'{p}.{k}', 'message': 'is not a recognised photo field'})
        aid, pid = a.get('id'), a.get('photoId')
        if not isinstance(aid, str) or not PHOTO_ASSOC_ID_RE.match(aid) or aid in seen_ids:
            errors.append({'path': f'{p}.id', 'message': 'invalid or duplicate id'})
        if not isinstance(pid, str) or not PHOTO_ID_RE.match(pid):
            errors.append({'path': f'{p}.photoId', 'message': 'invalid photo reference'})
        elif pid in seen_photos:
            errors.append({'path': f'{p}.photoId', 'message': 'a photo can belong to only one item (or parent item)'})
        seen_ids.add(aid)
        seen_photos.add(pid)
        sid = a.get('sourceId')
        if not isinstance(sid, str) or not SOURCE_REF_RE['sourceId'].match(sid):
            errors.append({'path': f'{p}.sourceId', 'message': 'invalid reference'})
        page = a.get('page')
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 10000:
            errors.append({'path': f'{p}.page', 'message': 'invalid page'})
        reg = a.get('region')
        if not isinstance(reg, dict) or set(reg) != {'x', 'y', 'w', 'h'} or any(
                not isinstance(reg[k], (int, float)) or isinstance(reg[k], bool) or not -1 <= reg[k] <= 20000 for k in reg):
            errors.append({'path': f'{p}.region', 'message': 'region must be {x, y, w, h} in page points'})
            reg = {'x': 0, 'y': 0, 'w': 0, 'h': 0}
        kind = a.get('kind')
        if kind not in PHOTO_KINDS:
            errors.append({'path': f'{p}.kind', 'message': 'invalid value'})
        status = a.get('status')
        if status not in PHOTO_STATUSES:
            errors.append({'path': f'{p}.status', 'message': 'invalid value'})
        origin = a.get('origin')
        if origin not in PHOTO_ORIGINS:
            errors.append({'path': f'{p}.origin', 'message': 'invalid value'})
        t = a.get('target')
        target = None
        if t is not None:
            if not isinstance(t, dict) or t.get('kind') not in ('item', 'group'):
                errors.append({'path': f'{p}.target', 'message': 'must be null, an item or a parent item'})
            elif t['kind'] == 'item':
                if set(t) != {'kind', 'itemId'} or t.get('itemId') not in item_ids:
                    errors.append({'path': f'{p}.target.itemId', 'message': 'is not an item of this draft'})
                target = {'kind': 'item', 'itemId': t.get('itemId')}
            else:
                parent = t.get('parent')
                if set(t) != {'kind', 'sourceId', 'parent'} or not isinstance(parent, str) or not parent.strip() \
                        or len(parent) > LIMITS['photoParent'] or t.get('sourceId') != sid:
                    errors.append({'path': f'{p}.target', 'message': 'a parent-item target needs its source and printed parent'})
                target = {'kind': 'group', 'sourceId': t.get('sourceId'), 'parent': parent}
        if target is None and status in ('suggested', 'confirmed'):
            errors.append({'path': f'{p}.target', 'message': 'a suggested or confirmed photo must belong to an item'})
        include = a.get('includeInPdf', False)
        if not isinstance(include, bool):
            errors.append({'path': f'{p}.includeInPdf', 'message': 'must be true or false'})
            include = False
        reasons = a.get('reasons') or []
        if not isinstance(reasons, list) or len(reasons) > 10 or any(not isinstance(r, str) or not PHOTO_REASON_RE.match(r) for r in reasons):
            errors.append({'path': f'{p}.reasons', 'message': 'invalid reasons'})
            reasons = []
        sug = a.get('suggestion')
        sug_out = None
        if sug is not None:
            if not isinstance(sug, dict) or set(sug) - {'extractionId', 'rowIds', 'confidence'} \
                    or not SOURCE_REF_RE['extractionId'].match(str(sug.get('extractionId') or '')) \
                    or not isinstance(sug.get('rowIds', []), list) or len(sug.get('rowIds', [])) > 60 \
                    or any(not isinstance(r, str) or not SOURCE_REF_RE['rowId'].match(r) for r in sug.get('rowIds', [])) \
                    or sug.get('confidence', 'high') not in ('high', 'low'):
                errors.append({'path': f'{p}.suggestion', 'message': 'invalid suggestion record'})
            else:
                sug_out = {'extractionId': sug['extractionId'], 'rowIds': list(sug.get('rowIds', [])),
                           'confidence': sug.get('confidence', 'high')}
        out.append({'id': aid, 'photoId': pid, 'sourceId': sid, 'page': page,
                    'region': {k: round(float(reg[k]), 1) for k in ('x', 'y', 'w', 'h')}, 'kind': kind,
                    'target': target, 'status': status, 'origin': origin, 'includeInPdf': include,
                    'reasons': list(reasons), 'suggestion': sug_out,
                    'updatedAt': _str(errors, f'{p}.updatedAt', a.get('updatedAt'), LIMITS['timestamp'])})
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
        if not (it.get('name') or '').strip() and not it['description'].strip():
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
    # Provisional: the quotation has discounts/charges this PO does not apply yet.
    adj_items = [it['id'] for it in included if any(f in it.get('reviewFlags', []) for f in ADJUSTMENT_FLAGS)]
    totals['provisional'] = bool(adj_items)
    totals['provisionalItems'] = adj_items

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
        add('item_invalid', f'{len(t["invalidItems"])} included item(s) missing name/description, unit, quantity or unit price.')
    if t['taxUnresolvedItems']:
        add('tax_unresolved', f'Tax treatment unresolved on {len(t["taxUnresolvedItems"])} included item(s).')
    pt = doc.get('paymentTerms', {})
    if t.get('provisional'):
        add('adjustments_unapplied', f'The quotation has discounts or charges affecting {len(t["provisionalItems"])} included item(s) '
                                     f'that this PO does not apply yet — PO totals are provisional and are not the quotation\'s payable total.')
    partial = [it['id'] for it in doc.get('items', [])
               if it['included'] and 'from_incomplete_extraction' in it.get('reviewFlags', [])]
    if partial:
        add('incomplete_source_document', f'{len(partial)} included item(s) come from an extraction that did not read every page '
                                          f'of the supplier document.')
    unreviewed = [it['id'] for it in doc.get('items', [])
                  if it['included'] and DEFAULT_TAX_REVIEW_FLAG in it.get('reviewFlags', [])]
    if unreviewed:
        add('default_tax_unreviewed', f'{len(unreviewed)} imported item(s) use the default VAT 15% because the quotation '
                                      f'states no tax treatment — review and confirm or change it.')
    dims = [it['id'] for it in doc.get('items', []) if it['included'] and (it.get('dimensions') or {}).get('status') == 'needs_confirmation']
    if dims:
        add('dimensions_unconfirmed', f'{len(dims)} included item(s) have conflicting source dimensions — confirm the dimensions '
                                      f'to print on the PO.')
    photos = [a for a in doc.get('photos', []) if a['status'] != 'removed']
    unsure = [a for a in photos if a['status'] == 'uncertain' or (a['includeInPdf'] and a['target'] is None)]
    if unsure:
        add('photos_unconfirmed', f'{len(unsure)} item photo(s) have an uncertain or missing item association — '
                                  f'confirm, reassign or remove them.')
    if pt.get('balanceTrigger', 'undecided') == 'undecided':
        add('payment_trigger_undecided', 'Balance payment trigger (delivery vs delivery + written acceptance) not decided.')
    if not computed['milestonePctValid']:
        add('payment_pct_invalid', 'Payment milestone percentages must be filled in and add up to 100%.')
    return b
