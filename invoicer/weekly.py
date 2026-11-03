"""Weekly contractor invoice: fill the client's .xlsx template from a work-log email."""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import openpyxl
import questionary

from . import store
from .email_log import parse_log, read_eml_text
from .flow import INVOICES_DIR, Cancelled, _ask, _validator, text
from .money import fmt_money, parse_money

# Cell layout of the "Weekly Invoice Sheet" template.
CONTRACTOR_CELLS = {
    "name": "C9",
    "street": "C10",
    "address2": "C11",
    "city_state_zip": "C12",
    "email": "C13",
    "phone": "C14",
}
PERIOD_START_CELL, PERIOD_END_CELL = "E10", "E11"
FIRST_ROW, LAST_ROW = 17, 100
# Job columns; Job #, City/State and Service aren't in the email and stay blank.
COL_CLIENT, COL_ADDRESS, COL_DATE, COL_AMOUNT = "A", "C", "E", "G"
DATE_FORMAT = "mm-dd-yy"


def template_path():
    return store.data_dir() / "weekly_template.xlsx"


# ---------- setup ----------

def run_setup_weekly(data, suggested=None):
    """Save contractor info and a private copy of the client's template."""
    weekly = data["weekly"]
    c = weekly["contractor"]
    hint = suggested or {}
    print("\nWeekly invoice contractor info (press Enter to keep the value shown)\n")
    c["name"] = text("Contractor name:", c["name"] or hint.get("name"), required=True)
    c["street"] = text("Street address:", c["street"] or hint.get("street"))
    c["address2"] = text("Address 2 (optional):", c["address2"])
    c["city_state_zip"] = text("City, State ZIP:", c["city_state_zip"] or hint.get("city_state_zip"))
    c["email"] = text("Contractor email:", c["email"])
    c["phone"] = text("Contractor phone:", c["phone"])

    have = template_path().exists()
    if not have or _ask(questionary.confirm("Replace the saved invoice template?", default=False)):
        source = _ask(questionary.path(
            "Path to the blank template (.xlsx) - tip: drag the file here:",
            validate=_check_template))
        template_path().parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_clean_path(source), template_path())
        print(f"Template saved to {template_path()}")
    store.save(data)
    print(f"Saved to {store.data_path()}")


def _clean_path(value):
    """Accept paths typed, pasted in quotes, or dragged into Terminal ('My\\ File.xlsx')."""
    value = value.strip().strip("'\"").replace("\\ ", " ")
    return Path(value).expanduser()


def _check_template(value):
    path = _clean_path(value)
    if not path.is_file() or path.suffix.lower() != ".xlsx":
        return "Choose an existing .xlsx file"
    try:
        openpyxl.load_workbook(path)
    except Exception as e:  # corrupt or not really a workbook
        return f"Can't open that workbook: {e}"
    return True


def _suggest_from_sender(lines):
    keys = ("name", "street", "city_state_zip")
    return dict(zip(keys, lines))


# ---------- fill ----------

def fill_workbook(template, contractor, log, path):
    """Write contractor info, period dates and jobs into a copy of the template."""
    capacity = LAST_ROW - FIRST_ROW + 1
    if len(log.jobs) > capacity:
        raise ValueError(f"The template has room for {capacity} jobs; this email has {len(log.jobs)}")
    wb = openpyxl.load_workbook(template)
    ws = wb.active
    for key, cell in CONTRACTOR_CELLS.items():
        ws[cell] = contractor.get(key) or None
    for cell, value in ((PERIOD_START_CELL, log.period_start), (PERIOD_END_CELL, log.period_end)):
        ws[cell] = value
        ws[cell].number_format = DATE_FORMAT
    for row, job in enumerate(log.jobs, start=FIRST_ROW):
        ws[f"{COL_CLIENT}{row}"] = job.client or None
        ws[f"{COL_ADDRESS}{row}"] = job.address or None
        ws[f"{COL_DATE}{row}"] = job.date
        ws[f"{COL_DATE}{row}"].number_format = DATE_FORMAT
        ws[f"{COL_AMOUNT}{row}"] = job.amount
    # Print setup so Excel prints just the invoice, one page wide.
    ws.print_area = f"A1:G{LAST_ROW + 1}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.save(path)


# PDF colors taken from the template's theme.
TITLE_RULE = (91, 155, 213)    # accent5, thick rule under the title
NAME_RULE = (112, 173, 71)     # accent6, thick rule under the contractor row
GRID = (68, 114, 196)          # accent1, table grid
LINK = (5, 99, 193)
TABLE_COLS = "ABCDEFG"
MIN_TABLE_ROWS = 10


def _latin1(value):
    text = str(value if value is not None else "")
    for src, dst in {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                     "\u2013": "-", "\u2014": "-", "\u00a0": " "}.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def _cell_text(cell):
    value = cell.value
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%m-%d-%y")
    if isinstance(value, (int, float, Decimal)) and "$" in (cell.number_format or ""):
        return fmt_money(Decimal(str(value)))
    return str(value).strip()


def export_pdf(xlsx_path):
    """Draw a one-page-wide Letter PDF of the filled sheet (only the rows in use)."""
    from io import BytesIO

    from fpdf import FPDF

    xlsx_path = Path(xlsx_path)
    pdf_path = xlsx_path.with_suffix(".pdf")
    ws = openpyxl.load_workbook(xlsx_path).active

    widths = [ws.column_dimensions[c].width or 8.43 for c in TABLE_COLS]
    margin = 36
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    page_w = pdf.w - 2 * margin
    col_w = [w / sum(widths) * page_w for w in widths]
    col_x = [margin + sum(col_w[:i]) for i in range(len(col_w))]

    def put(x, y, w, h, value, style="", size=10, align="L", color=(0, 0, 0)):
        pdf.set_font("Times", style, size)
        pdf.set_text_color(*color)
        pdf.set_xy(x, y)
        pdf.cell(w, h, _latin1(value), align=align)

    def rule(x0, x1, y, width, color):
        pdf.set_draw_color(*color)
        pdf.set_line_width(width)
        pdf.line(x0, y, x1, y)

    # Logo, placed over the right-hand columns like the sheet.
    y = margin
    if ws._images:
        logo = ws._images[0]
        logo_w = 110
        logo_h = logo_w * logo.height / logo.width
        pdf.image(BytesIO(logo._data()), x=col_x[4], y=y, w=logo_w, h=logo_h)
        y += logo_h + 8

    # Title (merged A8:F8) with the blue rule, then the contractor row with the green rule.
    title_w = sum(col_w[:6])
    put(margin, y, title_w, 20, ws["A8"].value, "B", 16, "C")
    y += 20
    rule(margin, margin + title_w, y, 2.5, TITLE_RULE)
    y += 2
    put(margin, y, col_w[0] + col_w[1], 14, ws["A9"].value, "B")
    put(col_x[2], y, col_w[2] + col_w[3], 14, ws["C9"].value, "B")
    put(col_x[5], y, col_w[5], 14, ws["F9"].value, "", 10, "C", LINK)
    y += 14
    rule(margin, margin + title_w, y, 2.5, NAME_RULE)
    y += 2

    row_h = 14
    for r in range(10, 15):
        put(margin, y, col_w[0] + col_w[1], row_h, ws[f"A{r}"].value)
        put(col_x[2], y, col_w[2], row_h, ws[f"C{r}"].value)
        rule(col_x[2], col_x[3], y + row_h - 1, 0.5, (0, 0, 0))
        if ws[f"D{r}"].value:
            put(col_x[3], y, col_w[3], row_h, ws[f"D{r}"].value, align="R")
            put(col_x[4], y, col_w[4], row_h, _cell_text(ws[f"E{r}"]), align="R")
            rule(col_x[4], col_x[5], y + row_h - 1, 0.5, GRID)
        y += row_h
    y += row_h

    def table_header(y):
        for i, c in enumerate(TABLE_COLS):
            label = str(ws[f"{c}16"].value or "").strip()
            put(col_x[i], y, col_w[i], row_h, label, "BU", 10, "C")
        return y + row_h

    used = [r for r in range(FIRST_ROW, LAST_ROW + 1)
            if any(ws[f"{c}{r}"].value is not None for c in TABLE_COLS)]
    rows = used + [None] * max(0, MIN_TABLE_ROWS - len(used))
    total = Decimal("0.00")
    y = table_header(y)
    bottom = pdf.h - margin - 2 * row_h
    for r in rows:
        if y + row_h > bottom:
            pdf.add_page()
            y = table_header(margin)
        pdf.set_draw_color(*GRID)
        pdf.set_line_width(0.6)
        for i in range(len(TABLE_COLS)):
            pdf.rect(col_x[i], y, col_w[i], row_h)
        if r is not None:
            for i, c in enumerate(TABLE_COLS):
                put(col_x[i] + 2, y, col_w[i] - 4, row_h, _cell_text(ws[f"{c}{r}"]),
                    align="R" if c == COL_AMOUNT else "C")
            amount = ws[f"{COL_AMOUNT}{r}"].value
            if amount is not None:
                total += Decimal(str(amount))
        y += row_h

    put(margin, y + 4, col_w[0], 16, ws[f"A{LAST_ROW + 1}"].value or "TOTAL", "B", 12)
    put(col_x[6] - 40, y + 4, col_w[6] + 38, 16, fmt_money(total), "B", 12, "R")
    pdf.output(str(pdf_path))
    return pdf_path


# ---------- flow ----------

def output_path(log):
    start = log.period_start.isoformat() if log.period_start else "unknown"
    end = log.period_end.isoformat() if log.period_end else "unknown"
    return INVOICES_DIR / f"Weekly_Invoice_{start}_to_{end}.xlsx"


def ask_missing_amounts(log):
    missing = [j for j in log.jobs if j.amount is None]
    if missing:
        print(f"\n{len(missing)} job(s) have no amount in the email. "
              "Enter it, or press Enter to leave it blank.")
    for job in missing:
        value = _ask(questionary.text(f"  {job.source} - amount:",
                                      validate=_validator(parse_money, allow_blank=True)))
        job.amount = parse_money(value) if value.strip() else None


def print_summary(contractor, log):
    width = 78
    print("\n" + "=" * width)
    print(f" WEEKLY INVOICE  {contractor['name']}")
    start = log.period_start.strftime("%m/%d/%Y") if log.period_start else "?"
    end = log.period_end.strftime("%m/%d/%Y") if log.period_end else "?"
    print(f" Period: {start} - {end}")
    print("-" * width)
    for job in log.jobs:
        amount = fmt_money(job.amount) if job.amount is not None else "(blank)"
        print(f" {job.date:%m/%d}  {job.client[:20]:<20}  {job.address[:34]:<34}  {amount:>12}")
    print("-" * width)
    total = sum((j.amount for j in log.jobs if j.amount is not None), Decimal("0.00"))
    print(f" {'TOTAL':>{width - 15}}{fmt_money(total):>13}")
    print("=" * width)
    if log.stated_total is not None and log.stated_total != total:
        print(f" Note: the email says Total: {fmt_money(log.stated_total)} "
              f"(difference {fmt_money(total - log.stated_total)}).")
    blanks = sum(1 for j in log.jobs if j.amount is None)
    if blanks:
        print(f" Note: {blanks} job(s) have a blank amount.")
    for note in log.notes:
        print(f" Email note (not on the invoice): {note}")
    print()


def run_from_email(data, eml_path):
    log = parse_log(read_eml_text(eml_path))
    if not log.jobs:
        raise ValueError("No job lines (like '10/07- Client 123 Street $65') found in the email")
    if not data["weekly"]["contractor"]["name"] or not template_path().exists():
        print("Weekly invoice setup isn't done yet - let's do it first.")
        run_setup_weekly(data, _suggest_from_sender(log.sender_lines))
    contractor = data["weekly"]["contractor"]

    print(f"Found {len(log.jobs)} jobs in {Path(eml_path).name}.")
    ask_missing_amounts(log)
    print_summary(contractor, log)
    if not _ask(questionary.confirm("Create the weekly invoice spreadsheet?", default=True)):
        raise Cancelled()

    path = output_path(log)
    if path.exists() and not _ask(questionary.confirm(
            f"{path.name} already exists. Overwrite?", default=False)):
        raise Cancelled()
    path.parent.mkdir(parents=True, exist_ok=True)
    fill_workbook(template_path(), contractor, log, path)
    print(f"\nSpreadsheet saved: {path}")

    if _ask(questionary.confirm("Also save a PDF copy?", default=False)):
        try:
            print(f"PDF saved: {export_pdf(path)}")
        except Exception as e:
            print(f"Couldn't create the PDF: {e}")
    return path
