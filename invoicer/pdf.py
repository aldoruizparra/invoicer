"""Render an invoice to PDF with fpdf2.

The layout reproduces the reference invoice (a Pages template): blue rule and
sender name across the top, a left column with the invoice number and sender
contact info, and a right column with bill-to, date, job address, the line
item table and the footer notes.

Coordinates are in points (72pt = 1in) on US Letter, measured from the
reference PDF. Text is placed by baseline so it lines up exactly.
"""

import logging
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF

from .money import compute_totals, fmt_money, fmt_rate
from .store import contact_lines

# fontTools warns about Apple-only tables it drops while subsetting; harmless.
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

# ---- colors ----
BLUE = (54, 125, 162)
BLACK = (0, 0, 0)
TITLE_GRAY = (68, 68, 68)
SIDE_GRAY = (96, 96, 96)
GRID_GRAY = (173, 173, 173)
HEADER_RULE = (127, 127, 127)
EDGE_GRAY = (228, 228, 228)

# ---- page frame (repeated on every page) ----
RULE_X0, RULE_X1 = 60, 552
TOP_RULE_Y, TOP_RULE_W = 56.5, 3
NAME_BASELINE, NAME_SIZE, NAME_TRACKING = 76.8, 13, 0.6
BOTTOM_RULE_Y = 722
PAGE_NUM_BASELINE = 738
CONTENT_BOTTOM = 700          # rows/paragraphs must end above this

# ---- left column ----
LEFT_X = 44.6
TITLE_BASELINE, TITLE_SIZE = 213.25, 18
SIDE_FIRST_BASELINE = 241.5   # phone; email follows, blank line, then address

# ---- right column ----
COL_X = 190
COL_RIGHT = 550.4
COL_W = COL_RIGHT - COL_X
BILL_TO_BASELINE = 186.1
LINE = 14.35                  # line pitch for 10pt text
BODY_SIZE = 10
TABLE_GAP = 20.2              # last "For" baseline -> table top

# ---- table ----
PRICE_X = 448.84              # divider between Description and Unit Price
HEADER_H = 20.69
ROW_H = 20.76
CELL_PAD = 4.12
ROW_BASELINE = 13.8           # row top -> text baseline
MIN_ITEM_ROWS = 5

# ---- footer notes ----
NOTES_GAP = 36.8              # table bottom -> first note baseline
PARA_GAP = 23.34              # baseline pitch between notes

FONT_FILE = Path("/System/Library/Fonts/HelveticaNeue.ttc")
# Index of each weight inside HelveticaNeue.ttc.
FONT_FACES = {"light": 7, "medium": 10, "bold": 1}
# Fallback to built-in core fonts when Helvetica Neue isn't installed (non-macOS).
CORE_FACES = {"light": ("Helvetica", ""), "medium": ("Helvetica", ""), "bold": ("Helvetica", "B")}

# Core PDF fonts only cover Latin-1; map common typographic characters first.
_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
}


class _InvoicePDF(FPDF):
    def __init__(self, sender_name):
        super().__init__(unit="pt", format="letter")
        self.sender_name = sender_name
        self.set_auto_page_break(False)
        self.set_margins(COL_X, TOP_RULE_Y, self.w - COL_RIGHT)
        self.use_system_font = FONT_FILE.exists()
        if self.use_system_font:
            for face, index in FONT_FACES.items():
                self.add_font(f"hn-{face}", "", str(FONT_FILE), collection_font_number=index)

    def clean(self, text):
        text = str(text or "")
        if self.use_system_font:
            return text
        for src, dst in _REPLACEMENTS.items():
            text = text.replace(src, dst)
        return text.encode("latin-1", "replace").decode("latin-1")

    def face(self, face, size=BODY_SIZE, color=BLACK):
        if self.use_system_font:
            self.set_font(f"hn-{face}", "", size)
        else:
            self.set_font(*CORE_FACES[face], size)
        self.set_text_color(*color)

    def put(self, x, baseline, text, align="L", right=None):
        """Draw one line of text at a baseline. align='R' right-aligns to `right`."""
        text = self.clean(text)
        if align == "R":
            x = right - self.get_string_width(text)
        self.text(x, baseline, text)
        return self.get_string_width(text)

    def wrap(self, text, width):
        """Split text into lines that fit width with the current font."""
        lines = []
        for para in self.clean(text).split("\n"):
            lines += self.multi_cell(width, LINE, para, dry_run=True, output="LINES") or [""]
        return lines

    def hline(self, x0, x1, y, width, color, dash=False):
        self.set_draw_color(*color)
        self.set_line_width(width)
        if dash:
            self.set_dash_pattern(dash=1.5, gap=1.5)
        self.line(x0, y, x1, y)
        self.set_dash_pattern()

    def vline(self, x, y0, y1, width, color, dash=False):
        self.set_draw_color(*color)
        self.set_line_width(width)
        if dash:
            self.set_dash_pattern(dash=1.5, gap=1.5)
        self.line(x, y0, x, y1)
        self.set_dash_pattern()

    def header(self):
        self.hline(RULE_X0, RULE_X1, TOP_RULE_Y, TOP_RULE_W, BLUE)
        self.face("bold", NAME_SIZE, BLUE)
        self.set_char_spacing(NAME_TRACKING)
        self.put(RULE_X0, NAME_BASELINE, self.sender_name.upper())
        self.set_char_spacing(0)

    def footer(self):
        self.hline(RULE_X0, RULE_X1, BOTTOM_RULE_Y, 1, BLUE)
        self.face("medium", BODY_SIZE, SIDE_GRAY)
        self.put(0, PAGE_NUM_BASELINE, str(self.page_no()), align="R", right=RULE_X1)


def render_invoice(sender, invoice, path):
    """Write the invoice PDF to path. invoice is the draft dict from flow.py."""
    pdf = _InvoicePDF(sender.get("name", ""))
    pdf.set_title(pdf.clean(f"Invoice #{invoice['number']}"))
    pdf.set_author(pdf.clean(sender.get("name")))
    pdf.set_creator("invoicer")
    pdf.add_page()

    _left_column(pdf, sender, invoice)
    y = _right_column(pdf, invoice)
    y = _table(pdf, invoice, y + TABLE_GAP)
    _notes(pdf, sender, y + NOTES_GAP)

    pdf.output(str(path))


def _left_column(pdf, sender, invoice):
    pdf.face("light", TITLE_SIZE, TITLE_GRAY)
    pdf.put(LEFT_X, TITLE_BASELINE, f"INVOICE #{invoice['number']}")

    y = SIDE_FIRST_BASELINE
    rows = [(sender.get("phone"), SIDE_GRAY), (sender.get("email"), BLUE), (None, None),
            (sender.get("street"), SIDE_GRAY), (sender.get("city_state_zip"), SIDE_GRAY)]
    for value, color in rows:
        if value:
            pdf.face("medium", BODY_SIZE, color)
            pdf.put(LEFT_X, y, value)
        if value or color is None:
            y += LINE


def _labeled(pdf, baseline, label, value):
    """'Label' in bold blue followed by value in light black; value may wrap."""
    pdf.face("bold", BODY_SIZE, BLUE)
    label_w = pdf.put(COL_X, baseline, label)
    pdf.face("light")
    lines = pdf.wrap(value, COL_W - label_w)
    pdf.put(COL_X + label_w, baseline, lines[0])
    for line in lines[1:]:
        baseline += LINE
        pdf.put(COL_X, baseline, line)
    return baseline


def _right_column(pdf, invoice):
    """Bill-to, date and job address. Returns the baseline of the last line."""
    contact = invoice["bill_to"]
    y = BILL_TO_BASELINE
    pdf.face("bold", BODY_SIZE, BLUE)
    w = pdf.put(COL_X, y, "Attention")
    pdf.face("light")
    pdf.put(COL_X + w, y, f": {contact.get('attention', '')}")
    for line in contact_lines(contact):
        y += LINE
        pdf.put(COL_X, y, line)

    y = _labeled(pdf, y + 2 * LINE, "Date:", f" {invoice['date']}")
    return _labeled(pdf, y + 2 * LINE, "For: ", invoice["job_address"])


def _table_header(pdf, top):
    pdf.set_fill_color(*BLUE)
    pdf.rect(COL_X, top, COL_W, HEADER_H, style="F")
    pdf.hline(COL_X, COL_RIGHT, top, 0.25, EDGE_GRAY)
    pdf.hline(COL_X, COL_RIGHT, top + HEADER_H, 0.5, HEADER_RULE)
    pdf.face("bold", BODY_SIZE, (255, 255, 255))
    pdf.put(COL_X + CELL_PAD, top + 13.67, "Description")
    pdf.put(PRICE_X + CELL_PAD, top + 13.67, "Unit Price")
    return top + HEADER_H


def _table_frame(pdf, top, bottom):
    """Light outer edges and the dashed column divider for a block of rows."""
    pdf.vline(COL_X, top, bottom, 0.25, EDGE_GRAY)
    pdf.vline(COL_RIGHT, top, bottom, 0.25, EDGE_GRAY)
    pdf.vline(PRICE_X, top, bottom, 0.75, GRID_GRAY, dash=True)


def _table(pdf, invoice, top):
    """Line items, padded to MIN_ITEM_ROWS, then tax and total rows. Returns bottom y."""
    rate = Decimal(invoice["tax_rate"])
    subtotal, tax, total = compute_totals([i["price"] for i in invoice["items"]], rate)
    desc_w = PRICE_X - COL_X - 2 * CELL_PAD

    rows = [(item["description"], fmt_money(Decimal(item["price"])))
            for item in invoice["items"]]
    rows += [("", "")] * (MIN_ITEM_ROWS - len(rows))

    y = block_top = _table_header(pdf, top)
    pdf.face("light")
    for i, (desc, price) in enumerate(rows):
        lines = pdf.wrap(desc, desc_w) if desc else [""]
        row_h = ROW_H + LINE * (len(lines) - 1)
        if y + row_h + 2 * ROW_H > CONTENT_BOTTOM and i < len(rows) - 1:
            _table_frame(pdf, block_top, y)
            pdf.add_page()
            y = block_top = _table_header(pdf, TOP_RULE_Y + 60)
            pdf.face("light")
        for n, line in enumerate(lines):
            pdf.put(COL_X + CELL_PAD, y + ROW_BASELINE + n * LINE, line)
        pdf.put(0, y + ROW_BASELINE, price, align="R", right=COL_RIGHT - CELL_PAD)
        y += row_h
        pdf.hline(COL_X, COL_RIGHT, y, 0.75, GRID_GRAY, dash=True)

    # Tax row
    pdf.face("bold")
    pdf.put(COL_X + CELL_PAD, y + ROW_BASELINE, f"Tax ({fmt_rate(rate)}%)")
    pdf.face("light")
    pdf.put(0, y + ROW_BASELINE, fmt_money(tax), align="R", right=COL_RIGHT - CELL_PAD)
    y += ROW_H
    pdf.hline(COL_X - 0.4, COL_RIGHT + 0.5, y, 1, BLACK)

    # Total row (no label, as in the reference)
    pdf.put(0, y + ROW_BASELINE, fmt_money(total), align="R", right=COL_RIGHT - CELL_PAD)
    y += ROW_H
    pdf.hline(COL_X - 0.4, COL_RIGHT + 0.5, y, 1, BLACK)
    _table_frame(pdf, block_top, y)
    return y


def _notes(pdf, sender, baseline):
    payable = sender.get("payable_to") or sender.get("name")
    contact = " | ".join(v for v in (sender.get("name"), sender.get("phone"),
                                     sender.get("email")) if v)
    notes = [(f"Make all checks payable to {payable}", BLACK),
             (sender.get("footer_text"), BLACK),
             (f"If you have any questions, contact {contact}", BLACK),
             ("THANK YOU FOR YOUR BUSINESS", BLUE)]

    pdf.face("light")
    blocks = [(pdf.wrap(text, COL_W), color) for text, color in notes if text]
    height = sum(LINE * (len(lines) - 1) for lines, _ in blocks) + PARA_GAP * (len(blocks) - 1)
    if baseline + height > CONTENT_BOTTOM:
        pdf.add_page()
        baseline = TOP_RULE_Y + 80

    for lines, color in blocks:
        pdf.face("light", BODY_SIZE, color)
        for n, line in enumerate(lines):
            pdf.put(COL_X, baseline + n * LINE, line)
        baseline += LINE * (len(lines) - 1) + PARA_GAP
