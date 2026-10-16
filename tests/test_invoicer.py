"""Run with: python -m unittest discover tests"""

import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from invoicer import flow, store
from invoicer.money import compute_totals, fmt_money, fmt_rate, parse_money, parse_rate


class MoneyTests(unittest.TestCase):
    def test_parse_money(self):
        self.assertEqual(parse_money("$1,234.5"), Decimal("1234.50"))
        self.assertEqual(parse_money("250"), Decimal("250.00"))
        for bad in ("", "abc", "1.234", "nan", "inf"):
            with self.assertRaises(ValueError):
                parse_money(bad)

    def test_tax_rounds_half_up(self):
        # 10.06 * 8.25% = 0.82995 -> 0.83 ; 0.30 * 8.25% = 0.02475 -> 0.02
        self.assertEqual(compute_totals(["10.06"], Decimal("8.25"))[1], Decimal("0.83"))
        self.assertEqual(compute_totals(["0.30"], Decimal("8.25"))[1], Decimal("0.02"))
        # 2.00 * 6.25% = 0.125 -> 0.13 (banker's rounding would give 0.12)
        self.assertEqual(compute_totals(["2.00"], Decimal("6.25"))[1], Decimal("0.13"))

    def test_totals(self):
        sub, tax, total = compute_totals(["250.00", "180.00", "95.50"], Decimal("8.25"))
        self.assertEqual((sub, tax, total),
                         (Decimal("525.50"), Decimal("43.35"), Decimal("568.85")))

    def test_formatting(self):
        self.assertEqual(fmt_money(Decimal("1234.5")), "$1,234.50")
        self.assertEqual(fmt_money(Decimal("-5")), "-$5.00")
        self.assertEqual(fmt_rate(Decimal("8.250")), "8.25")
        self.assertEqual(fmt_rate(Decimal("10")), "10")
        with self.assertRaises(ValueError):
            parse_rate("101")


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.out = Path(tempfile.mkdtemp())
        patches = [mock.patch.dict(os.environ, {"INVOICER_HOME": self.home}),
                   mock.patch.object(flow, "INVOICES_DIR", self.out)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.home)
        self.addCleanup(shutil.rmtree, self.out)
        self.data = store.load()
        self.data["sender"].update(name="Test Sender", phone="555-0100")
        store.save(self.data)

    def script(self, *answers):
        """Feed answers to prompts in order, bypassing the terminal UI."""
        queue = list(answers)
        return mock.patch.object(flow, "_ask", side_effect=lambda q: queue.pop(0))

    def new_invoice_answers(self, final_action):
        return ["1", "10/16/2026",
                flow.ADD_NEW, "John Smith", "", "Acme LLC", "1 Main St", "Dallas, TX",
                flow.ADD_NEW, "456 Oak Ave", "Plano, TX",
                "Deep clean", "250", "Extra", "1,000.50", "",
                "8.25", final_action]

    def test_new_invoice_confirm(self):
        with self.script(*self.new_invoice_answers("Confirm & generate PDF")):
            path = flow.run_new(self.data)
        self.assertEqual(path.name, "Invoice_1_2026-10-16.pdf")
        self.assertTrue(path.exists())
        data = store.load()
        self.assertEqual(data["last_invoice_number"], 1)
        self.assertEqual(data["contacts"][0]["company"], "Acme LLC")
        self.assertEqual(data["job_addresses"], ["456 Oak Ave\nPlano, TX"])
        self.assertEqual([i["description"] for i in data["items"]], ["Deep clean", "Extra"])
        self.assertEqual(data["items"][1]["price"], "1000.50")

    def test_cancel_does_not_increment(self):
        with self.script(*self.new_invoice_answers("Cancel")):
            with self.assertRaises(flow.Cancelled):
                flow.run_new(self.data)
        self.assertEqual(store.load()["last_invoice_number"], 0)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_repeat_and_mru(self):
        with self.script(*self.new_invoice_answers("Confirm & generate PDF")):
            flow.run_new(self.data)
        data = store.load()
        # Repeat: change the price of the second item, then confirm.
        with self.script("Edit a field", "Line items", 1, "Change price", "75",
                         "done", "Confirm & generate PDF"):
            path = flow.run_repeat(data)
        self.assertTrue(path.name.startswith("Invoice_2_"))
        data = store.load()
        self.assertEqual(data["last_invoice_number"], 2)
        self.assertEqual(data["last_invoice"]["items"][1]["price"], "75.00")
        self.assertEqual(store.find_item(data, "extra")["price"], "75.00")
        self.assertEqual(len(data["contacts"]), 1)

    def test_edit_contact_from_bill_to(self):
        with self.script(*self.new_invoice_answers("Confirm & generate PDF")):
            flow.run_new(self.data)
        data = store.load()
        # Edit the saved contact's company, then pick it from the refreshed list.
        with self.script(flow.EDIT_CONTACT, 0, "John Smith", "", "Acme Inc", "1 Main St",
                         "Dallas, TX", 0):
            contact = flow.ask_bill_to(data)
        self.assertEqual(contact["company"], "Acme Inc")
        data = store.load()
        self.assertEqual(len(data["contacts"]), 1)
        self.assertEqual(data["contacts"][0]["company"], "Acme Inc")
        self.assertEqual(data["last_invoice"]["bill_to"]["company"], "Acme Inc")

    def test_remove_contact_from_bill_to(self):
        self.data["contacts"] = [{"attention": "A", "company": "One"},
                                 {"attention": "B", "company": "Two"}]
        # Remove "One", decline removing another, then pick the remaining contact.
        with self.script(flow.REMOVE_CONTACT, 0, True,
                         flow.REMOVE_CONTACT, 0, False, 0):
            contact = flow.ask_bill_to(self.data)
        self.assertEqual(contact["company"], "Two")
        self.assertEqual([c["company"] for c in store.load()["contacts"]], ["Two"])

    def test_validators(self):
        check_date = flow._validator(flow.parse_date)
        self.assertIs(check_date("02/29/2028"), True)
        self.assertIsInstance(check_date("02/30/2026"), str)
        self.assertIsInstance(check_date("2026-10-16"), str)
        self.assertIsInstance(flow._validator(parse_money)("12.x"), str)
        self.assertIsInstance(flow._validator(flow.parse_number)("0"), str)


if __name__ == "__main__":
    unittest.main()
