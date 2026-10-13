"""
Vista Platform — native (selectable-text) PDF for Sarawat quotations.

The browser sends the quotation exactly as it is rendered in the preview
(#pdfPage / #sarawatPdfPage markup + the page's own CSS, see
nativeQuotationPdf() in daftra-pdf-generator_1.html). This module prints that
HTML with the locally installed Microsoft Edge (or Chrome) in headless mode —
the same approach and browser lookup as po_pdf.py — so every word, number and
date in the PDF is real, searchable PDF text drawn by the browser's own layout
engine (fonts embedded, Arabic shaped natively, CSS page breaks honoured).
Nothing is rasterised: only <img> photos/logos stay images.

Safety: the snapshot is static markup — the browser strips every <script> and
inline on* handler before sending it (Edge's own scriptEnabled=false switch
was tried and makes --print-to-pdf silently produce no file); a throwaway
profile is used per render, and the proxy only listens on 127.0.0.1. Network is allowed solely so the page's own Google Fonts stylesheet
and same-origin images (e.g. the Vista logo) load exactly as in the preview.
"""
import os
import shutil
import subprocess
import tempfile

from po_pdf import PdfRenderError, find_browser

MAX_HTML_BYTES = 40 * 1024 * 1024   # embedded item photos are data URIs


def html_to_pdf(doc_html, timeout=120):
    """Print a self-contained quotation HTML document to PDF. Returns PDF bytes."""
    if not isinstance(doc_html, str) or '<html' not in doc_html[:2000].lower():
        raise PdfRenderError('The quotation HTML is missing.')
    if len(doc_html.encode('utf-8')) > MAX_HTML_BYTES:
        raise PdfRenderError('The quotation is too large to render.')
    browser = find_browser()
    if not browser:
        raise PdfRenderError('No Microsoft Edge or Google Chrome found on this PC to produce the PDF.')
    work = tempfile.mkdtemp(prefix='vista-sarawat-pdf-')
    try:
        src = os.path.join(work, 'quotation.html')
        out = os.path.join(work, 'quotation.pdf')
        with open(src, 'w', encoding='utf-8') as f:
            f.write(doc_html)
        url = 'file:///' + src.replace('\\', '/')
        try:
            subprocess.run([browser, '--headless=new', '--disable-gpu', '--no-first-run', '--disable-extensions',
                            '--no-pdf-header-footer',
                            # Give the web fonts time to arrive before printing (virtual time
                            # fast-forwards once the network is idle, so it rarely costs much).
                            '--run-all-compositor-stages-before-draw', '--virtual-time-budget=15000',
                            f'--user-data-dir={os.path.join(work, "profile")}',
                            f'--print-to-pdf={out}', url],
                           check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError) as ex:
            raise PdfRenderError(f'The PDF could not be produced ({type(ex).__name__}).')
        if not os.path.isfile(out):
            raise PdfRenderError('The PDF could not be produced.')
        with open(out, 'rb') as f:
            data = f.read()
        if not data.startswith(b'%PDF-'):
            raise PdfRenderError('The PDF output is not a PDF.')
        return data
    finally:
        shutil.rmtree(work, ignore_errors=True)
