"""The statement PDF (Billing Build B5, PR 6; plan section 7.2).

`render(document)` draws the frozen document dict that `issue` builds and returns the bytes. It
reads nothing but that dict, so the same dict always gives the same bytes: ReportLab's
invariant mode fixes the dates and the document id, and nothing here uses the clock.

Built-in Helvetica only, so no font is embedded; amounts are written `PHP 2,499.00`, never with
the peso sign. The streams are left uncompressed: a statement is one or two small pages and its
text stays readable by a checker. No text here says the document is an invoice or a receipt,
or claims any BIR status: the exact label says the opposite and is on every page.
"""
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from apps.statements.models import LABEL

MANILA = ZoneInfo("Asia/Manila")
FONT, BOLD = "Helvetica", "Helvetica-Bold"
MARGIN = 48
WIDTH, HEIGHT = A4
TEXT_WIDTH = WIDTH - 2 * MARGIN
KIND_TITLES = {
    "monthly": "Monthly subscription",
    "annual": "Annual subscription",
    "launch_promo": "Launch promo subscription",
    "usage_only": "Usage charges",
    "final_usage": "Final usage charges",
    "adjustment": "Adjustment",
}
COLUMNS = {"qty": WIDTH - MARGIN - 190, "unit": WIDTH - MARGIN - 105, "amount": WIDTH - MARGIN}


def money(centavos):
    """`PHP 2,499.00`; a negative amount (an adjustment line) is `-PHP 50.00`."""
    sign = "-" if centavos < 0 else ""
    pesos, cents = divmod(abs(centavos), 100)
    return f"{sign}PHP {pesos:,}.{cents:02d}"


def _day(iso):
    value = datetime.fromisoformat(iso).astimezone(MANILA).date()
    return f"{value.day} {value:%B %Y}"


class _Page:
    def __init__(self, document):
        self.document = document
        self.buffer = io.BytesIO()
        self.pdf = canvas.Canvas(
            self.buffer, pagesize=A4, invariant=1, pageCompression=0,
            title=f"Billing Statement {document['number']}", author="NegosyoOnline",
            creator="NegosyoOnline", subject=LABEL,
        )
        self.y = 0
        self.start()

    def start(self):
        """The top of a page: the exact label on every page, the TEST band when not live."""
        pdf = self.pdf
        if self.document["is_test"]:
            pdf.setFont(BOLD, 11)
            pdf.drawCentredString(WIDTH / 2, HEIGHT - 28, "TEST DOCUMENT - NOT A PAID CHARGE")
            pdf.setLineWidth(1)
            pdf.rect(MARGIN, HEIGHT - 36, TEXT_WIDTH, 20)
        pdf.setFont(BOLD, 9)
        y = HEIGHT - 56
        for row in simpleSplit(LABEL, BOLD, 9, TEXT_WIDTH):
            pdf.drawString(MARGIN, y, row)
            y -= 12
        pdf.setLineWidth(0.5)
        pdf.line(MARGIN, y, WIDTH - MARGIN, y)
        self.y = y - 22

    def need(self, space):
        if self.y - space < MARGIN + 20:
            self.pdf.showPage()
            self.start()

    def text(self, content, *, font=FONT, size=10, gap=14):
        for row in simpleSplit(content, font, size, TEXT_WIDTH):
            self.need(gap)
            self.pdf.setFont(font, size)
            self.pdf.drawString(MARGIN, self.y, row)
            self.y -= gap

    def right(self, content, x, *, font=FONT, size=10):
        self.pdf.setFont(font, size)
        self.pdf.drawRightString(x, self.y, content)


def _party(page, heading, rows):
    page.text(heading, font=BOLD, size=10)
    for row in rows:
        if row:
            page.text(row)
    page.y -= 6


def _issuer_rows(issuer):
    name = issuer["legal_name"]
    if issuer.get("trade_name") and issuer["trade_name"] != name:
        name = f"{name} ({issuer['trade_name']})"
    return [
        name, issuer["registered_address"],
        f"TIN {issuer['tin']}, branch {issuer['branch_code']}",
        "Non-VAT registered" if issuer.get("tax_type") == "non_vat" else "",
        issuer["contact_email"],
    ]


def _bill_to_rows(bill_to):
    return [
        bill_to.get("name", ""), bill_to.get("address", ""), bill_to.get("email", ""),
        f"TIN {bill_to['tin']}" if bill_to.get("tin") else "",
    ]


def render(document):
    """The PDF bytes of one frozen statement document."""
    page = _Page(document)
    title = KIND_TITLES.get(document["kind"], "Billing statement")
    page.text("BILLING STATEMENT", font=BOLD, size=16, gap=20)
    page.text(title, size=11, gap=18)
    page.text(f"Statement number: {document['number']}")
    page.text(f"Issue date: {_day(document['issued_at'])}")
    page.text(f"Period: {_day(document['period_start'])} to {_day(document['period_end'])}")
    if document.get("payment_reference"):
        page.text(f"Payment reference: {document['payment_reference']}")
    if document.get("original_number"):  # an adjustment names what it reduces and why
        page.text(f"Adjusts statement: {document['original_number']}")
        if document.get("reason_text"):
            page.text(f"Reason: {document['reason_text']}")
    page.y -= 8
    _party(page, "Issued by", _issuer_rows(document["issuer"]))
    _party(page, "Billed to", _bill_to_rows(document["bill_to"]))

    page.need(40)
    page.pdf.setFont(BOLD, 9)
    for name, label in (("qty", "Qty"), ("unit", "Unit price"), ("amount", "Amount")):
        page.pdf.drawRightString(COLUMNS[name], page.y, label)
    page.pdf.drawString(MARGIN, page.y, "Description")
    page.y -= 4
    page.pdf.line(MARGIN, page.y, WIDTH - MARGIN, page.y)
    page.y -= 14
    description_width = COLUMNS["qty"] - MARGIN - 50
    for line in document["lines"]:
        rows = simpleSplit(line["description"], FONT, 10, description_width)
        page.need(14 * len(rows))
        top = page.y
        for row in rows:
            page.pdf.setFont(FONT, 10)
            page.pdf.drawString(MARGIN, page.y, row)
            page.y -= 14
        bottom, page.y = page.y, top
        page.right(str(line["quantity"]), COLUMNS["qty"])
        page.right(money(line["unit_centavos"]), COLUMNS["unit"])
        page.right(money(line["amount_centavos"]), COLUMNS["amount"])
        page.y = bottom
    page.need(30)
    page.pdf.line(MARGIN, page.y + 6, WIDTH - MARGIN, page.y + 6)
    page.y -= 8
    page.right("Total", COLUMNS["unit"], font=BOLD, size=11)
    page.right(money(document["total_centavos"]), COLUMNS["amount"], font=BOLD, size=11)
    page.y -= 26
    page.text("Amounts are in Philippine pesos (PHP).", size=9)
    page.pdf.showPage()
    page.pdf.save()
    return page.buffer.getvalue()
