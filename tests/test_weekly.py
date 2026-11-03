"""Tests for the weekly work-log email -> .xlsx invoice. All data here is made up."""

import shutil
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

import openpyxl

from invoicer.email_log import parse_log, read_eml_text
from invoicer.weekly import export_pdf, fill_workbook

SAMPLE = """\
To: Example Client Co
PO Box 1
Springfield, TX 75000
From: Jane Sample
1 Demo St.
Springfield, Tx. 75001
Invoice Date: 10/12//2026
Work Week:10/04//2026-10/11/2026
10/07- Acme Homes 100 First St $65
10/07- Acme Homes  200 Second . $65
This week, I also cleaned the patio furniture.
10/08- Beta 300 Third St $50.                          10/08- Gamma Co 400 Fourth Ln $60
10/09- Delta S. 500 Fifth Dr.$1,050.25
10/09- Echo H. 600 Sixth G $
Total: $1,290.25
"""


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.log = parse_log(SAMPLE)

    def test_header(self):
        self.assertEqual(self.log.invoice_date, date(2026, 10, 12))
        self.assertEqual((self.log.period_start, self.log.period_end),
                         (date(2026, 10, 4), date(2026, 10, 11)))
        self.assertEqual(self.log.sender_lines,
                         ["Jane Sample", "1 Demo St.", "Springfield, Tx. 75001"])
        self.assertEqual(self.log.stated_total, Decimal("1290.25"))

    def test_jobs(self):
        got = [(j.date.day, j.client, j.address, j.amount) for j in self.log.jobs]
        self.assertEqual(got, [
            (7, "Acme Homes", "100 First St", Decimal("65.00")),
            (7, "Acme Homes", "200 Second", Decimal("65.00")),
            (8, "Beta", "300 Third St", Decimal("50.00")),
            (8, "Gamma Co", "400 Fourth Ln", Decimal("60.00")),
            (9, "Delta S.", "500 Fifth Dr.", Decimal("1050.25")),
            (9, "Echo H.", "600 Sixth G", None),
        ])
        self.assertEqual(self.log.notes, ["This week, I also cleaned the patio furniture."])

    def test_year_rollover(self):
        log = parse_log("Work Week: 12/28/2026-01/03/2027\n12/30- A 1 St $5\n01/02- B 2 St $5\n")
        self.assertEqual([j.date for j in log.jobs], [date(2026, 12, 30), date(2027, 1, 2)])

    def test_reads_eml(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        msg = EmailMessage()
        msg["Subject"] = "Invoice"
        msg.set_content(SAMPLE)
        (tmp / "log.eml").write_bytes(bytes(msg))
        self.assertEqual(len(parse_log(read_eml_text(tmp / "log.eml")).jobs), 6)


class FillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A8"], ws["A9"], ws["F9"] = "Weekly Invoice Sheet", "Contractor Name", "client@example.com"
        ws["D10"], ws["D11"] = "Period Start Date:", "Period End Date:"
        for col, label in zip("ABCDEFG", ["Client", "Job #", "Address", "City, State",
                                          "Date", "Service", "Amount"]):
            ws[f"{col}16"] = label
        ws["A101"], ws["G101"] = "TOTAL", "=SUM(G17:G100)"
        wb.save(self.tmp / "template.xlsx")

    def test_fill_and_pdf(self):
        contractor = {"name": "Jane Sample", "street": "1 Demo St.", "address2": "",
                      "city_state_zip": "Springfield, TX 75001", "email": "", "phone": ""}
        out = self.tmp / "out.xlsx"
        fill_workbook(self.tmp / "template.xlsx", contractor, parse_log(SAMPLE), out)
        ws = openpyxl.load_workbook(out).active
        self.assertEqual(ws["C9"].value, "Jane Sample")
        self.assertIsNone(ws["C11"].value)
        self.assertEqual(ws["E10"].value, datetime(2026, 10, 4))
        self.assertEqual([ws["A17"].value, ws["C17"].value, ws["G17"].value],
                         ["Acme Homes", "100 First St", 65])
        self.assertIsNone(ws["G22"].value)          # blank amount stays blank
        self.assertIsNone(ws["A23"].value)          # nothing past the last job
        self.assertEqual(ws["G101"].value, "=SUM(G17:G100)")
        self.assertTrue(export_pdf(out).exists())


if __name__ == "__main__":
    unittest.main()
