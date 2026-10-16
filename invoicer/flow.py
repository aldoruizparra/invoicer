"""Interactive terminal flows: setup, new, repeat, list."""

import copy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import questionary

from . import store
from .money import compute_totals, fmt_money, fmt_rate, parse_money, parse_rate
from .pdf import render_invoice

DATE_FMT = "%m/%d/%Y"
ADD_NEW = "+ Add new"
INVOICES_DIR = Path(__file__).resolve().parent.parent / "invoices"


class Cancelled(Exception):
    pass


# ---------- prompt helpers ----------

def _ask(question):
    """Run a questionary prompt; Ctrl-C raises KeyboardInterrupt instead of returning None."""
    return question.unsafe_ask()


def _validator(parse, allow_blank=False):
    def check(text):
        if allow_blank and not text.strip():
            return True
        try:
            parse(text)
            return True
        except ValueError as e:
            return str(e)
    return check


def _required(text):
    return True if text.strip() else "This field is required"


def text(message, default="", required=False):
    return _ask(questionary.text(message, default=default or "",
                                 validate=_required if required else None)).strip()


def parse_date(value):
    try:
        return datetime.strptime(value.strip(), DATE_FMT).date()
    except ValueError:
        raise ValueError("Use MM/DD/YYYY, e.g. 10/16/2026")


def parse_number(value):
    value = value.strip().lstrip("#")
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("Invoice number must be a positive whole number")
    return int(value)


# ---------- setup ----------

def run_setup(data):
    sender = data["sender"]
    print("\nSender profile (press Enter to keep the value shown)\n")
    sender["name"] = text("Business / sender name:", sender["name"], required=True)
    sender["phone"] = text("Phone:", sender["phone"])
    sender["email"] = text("Email:", sender["email"])
    sender["street"] = text("Street address:", sender["street"])
    sender["city_state_zip"] = text("City, State ZIP:", sender["city_state_zip"])
    sender["payable_to"] = text("Make checks payable to:",
                                sender["payable_to"] or sender["name"], required=True)
    sender["footer_text"] = text("Footer note:", sender["footer_text"])
    data["default_tax_rate"] = fmt_rate(parse_rate(_ask(questionary.text(
        "Default tax rate (%):", default=str(data["default_tax_rate"]),
        validate=_validator(parse_rate)))))
    data["last_invoice_number"] = int(_ask(questionary.text(
        "Last invoice number used (next invoice will be this + 1):",
        default=str(data["last_invoice_number"]),
        validate=lambda v: True if v.strip().isdigit() else "Enter a whole number")))
    store.save(data)
    print(f"\nSaved to {store.data_path()}")


def ensure_sender(data):
    if not data["sender"].get("name"):
        print("No sender profile yet - let's set it up first.")
        run_setup(data)


# ---------- invoice fields ----------

def ask_number(data, current):
    while True:
        number = parse_number(_ask(questionary.text(
            "Invoice number:", default=str(current), validate=_validator(parse_number))))
        if number > data["last_invoice_number"]:
            return number
        if _ask(questionary.confirm(
                f"Invoice #{number} is not above the last one used "
                f"(#{data['last_invoice_number']}). Use it anyway?", default=False)):
            return number


def ask_date(current):
    value = _ask(questionary.text("Date (MM/DD/YYYY):", default=current,
                                  validate=_validator(parse_date)))
    return parse_date(value).strftime(DATE_FMT)


def _contact_label(c):
    parts = [c.get("company"), "Attn: " + c["attention"] if c.get("attention") else ""]
    return " - ".join(p for p in parts if p) or "(blank)"


def new_contact():
    print("New bill-to contact:")
    contact = {
        "attention": text("  Attention (name):", required=True),
        "attention2": text("  Second contact (optional):"),
        "company": text("  Company:"),
        "street": text("  Street address:"),
        "city_state": text("  City, State:"),
    }
    return contact


def ask_bill_to(data, current=None):
    choices = [questionary.Choice(_contact_label(c), value=i)
               for i, c in enumerate(data["contacts"])]
    choices.append(questionary.Choice(ADD_NEW, value=ADD_NEW))
    default = None
    if current is not None:
        key = store.contact_key(current)
        default = next((ch for ch in choices if ch.value != ADD_NEW
                        and store.contact_key(data["contacts"][ch.value]) == key), None)
    pick = _ask(questionary.select("Bill to:", choices=choices, default=default))
    if pick == ADD_NEW:
        contact = new_contact()
        store.push_front(data["contacts"], contact, store.contact_key)
        store.save(data)
        return contact
    return copy.deepcopy(data["contacts"][pick])


def _address_label(address):
    return address.replace("\n", ", ")


def ask_job_address(data, current=None):
    """Job addresses are stored as 'street\ncity, state' (city line optional)."""
    choices = [questionary.Choice(_address_label(a), value=a) for a in data["job_addresses"]]
    choices.append(questionary.Choice(ADD_NEW, value=ADD_NEW))
    default = next((ch for ch in choices if ch.value == current), None)
    pick = _ask(questionary.select("For (job address):", choices=choices, default=default))
    if pick == ADD_NEW:
        street = text("  Job street address:", required=True)
        city = text("  Job city, state (optional):")
        pick = "\n".join(p for p in (street, city) if p)
        store.push_front(data["job_addresses"], pick, store.address_key)
        store.save(data)
    return pick


def ask_price(description, default=""):
    value = _ask(questionary.text(f"  Price for '{description}':", default=default,
                                  validate=_validator(parse_money)))
    return str(parse_money(value))


def ask_description(data, number):
    message = f"Item {number} description (blank to finish):"
    saved = [i["description"] for i in data["items"]]
    if not saved:
        return _ask(questionary.text(message)).strip()
    meta = {i["description"]: f"last {fmt_money(Decimal(i['price']))}" for i in data["items"]}
    return _ask(questionary.autocomplete(
        message, choices=saved, meta_information=meta, match_middle=True,
        ignore_case=True)).strip()


def ask_items(data, items=None):
    items = list(items or [])
    print("Line items: type to search saved items (Tab/arrows to pick) or enter a new one.")
    while True:
        description = ask_description(data, len(items) + 1)
        if not description:
            if items:
                return items
            print("  Add at least one line item.")
            continue
        saved = store.find_item(data, description)
        if saved:
            description = saved["description"]
        price = ask_price(description, saved["price"] if saved else "")
        items.append({"description": description, "price": price})


def edit_items(data, items):
    items = list(items)
    while True:
        choices = [questionary.Choice(f"{it['description']}  {fmt_money(Decimal(it['price']))}",
                                      value=i) for i, it in enumerate(items)]
        choices += [questionary.Choice("+ Add item(s)", value="add"),
                    questionary.Choice("Done", value="done")]
        pick = _ask(questionary.select("Line items (pick one to change or remove):",
                                       choices=choices))
        if pick == "done":
            if items:
                return items
            print("  Add at least one line item.")
        elif pick == "add":
            items = ask_items(data, items)
        else:
            action = _ask(questionary.select(
                items[pick]["description"],
                choices=["Change price", "Change description", "Remove", "Back"]))
            if action == "Change price":
                items[pick]["price"] = ask_price(items[pick]["description"], items[pick]["price"])
            elif action == "Change description":
                items[pick]["description"] = text("  Description:",
                                                  items[pick]["description"], required=True)
            elif action == "Remove":
                items.pop(pick)


def ask_tax_rate(current):
    value = _ask(questionary.text("Tax rate (%):", default=fmt_rate(current),
                                  validate=_validator(parse_rate)))
    return fmt_rate(parse_rate(value))


# ---------- summary / confirm ----------

def print_summary(inv):
    c = inv["bill_to"]
    subtotal, tax, total = compute_totals([i["price"] for i in inv["items"]],
                                          Decimal(inv["tax_rate"]))
    width = 60
    print("\n" + "=" * width)
    print(f" INVOICE #{inv['number']}    Date: {inv['date']}")
    print("-" * width)
    print(f" Bill to: Attention: {c.get('attention', '')}")
    for key in ("attention2", "company", "street", "city_state"):
        if c.get(key):
            print(f"          {c[key]}")
    print(f" For:     {_address_label(inv['job_address'])}")
    print("-" * width)
    price_w = 14
    for item in inv["items"]:
        desc = item["description"]
        if len(desc) > width - price_w - 2:
            desc = desc[: width - price_w - 5] + "..."
        print(f" {desc:<{width - price_w - 2}}{fmt_money(Decimal(item['price'])):>{price_w}}")
    print("-" * width)
    label_w = width - price_w - 2
    print(f" {'Subtotal':>{label_w}}{fmt_money(subtotal):>{price_w}}")
    print(f" {'Tax (' + fmt_rate(inv['tax_rate']) + '%)':>{label_w}}{fmt_money(tax):>{price_w}}")
    print(f" {'TOTAL':>{label_w}}{fmt_money(total):>{price_w}}")
    print("=" * width + "\n")


FIELDS = ["Invoice number", "Date", "Bill to", "For (job address)", "Line items",
          "Tax rate", "Back"]


def edit_field(data, inv):
    field = _ask(questionary.select("Edit which field?", choices=FIELDS))
    if field == "Invoice number":
        inv["number"] = ask_number(data, inv["number"])
    elif field == "Date":
        inv["date"] = ask_date(inv["date"])
    elif field == "Bill to":
        inv["bill_to"] = ask_bill_to(data, inv["bill_to"])
    elif field == "For (job address)":
        inv["job_address"] = ask_job_address(data, inv["job_address"])
    elif field == "Line items":
        inv["items"] = edit_items(data, inv["items"])
    elif field == "Tax rate":
        inv["tax_rate"] = ask_tax_rate(inv["tax_rate"])


def review(data, inv):
    """Summary loop. Returns the PDF path on confirm; raises Cancelled on cancel."""
    while True:
        print_summary(inv)
        action = _ask(questionary.select(
            "Generate this invoice?",
            choices=["Confirm & generate PDF", "Edit a field", "Cancel"]))
        if action == "Cancel":
            raise Cancelled()
        if action == "Edit a field":
            edit_field(data, inv)
            continue
        path = output_path(inv)
        if path.exists() and not _ask(questionary.confirm(
                f"{path.name} already exists. Overwrite?", default=False)):
            continue
        return finalize(data, inv, path)


def output_path(inv):
    iso = datetime.strptime(inv["date"], DATE_FMT).date().isoformat()
    return INVOICES_DIR / f"Invoice_{inv['number']}_{iso}.pdf"


def finalize(data, inv, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    render_invoice(data["sender"], inv, path)
    # Only now, after the PDF exists, touch the counters and MRU lists.
    data["last_invoice_number"] = max(data["last_invoice_number"], inv["number"])
    store.push_front(data["contacts"], copy.deepcopy(inv["bill_to"]), store.contact_key)
    store.push_front(data["job_addresses"], inv["job_address"], store.address_key)
    for item in reversed(inv["items"]):
        store.push_front(data["items"], dict(item), store.item_key)
    data["last_invoice"] = copy.deepcopy(inv)
    store.save(data)
    return path


# ---------- commands ----------

def today():
    return date.today().strftime(DATE_FMT)


def run_new(data):
    ensure_sender(data)
    inv = {"number": ask_number(data, data["last_invoice_number"] + 1),
           "date": ask_date(today())}
    inv["bill_to"] = ask_bill_to(data)
    inv["job_address"] = ask_job_address(data)
    inv["items"] = ask_items(data)
    inv["tax_rate"] = ask_tax_rate(data["default_tax_rate"])
    return review(data, inv)


def run_repeat(data):
    ensure_sender(data)
    last = data.get("last_invoice")
    if not last:
        print("No previous invoice yet - starting a new one.\n")
        return run_new(data)
    inv = copy.deepcopy(last)
    inv["number"] = data["last_invoice_number"] + 1
    inv["date"] = today()
    print(f"Prefilled from invoice #{last['number']}. Choose 'Edit a field' to change anything.")
    return review(data, inv)


LIST_KINDS = {
    "Bill-to contacts": ("contacts", _contact_label),
    "Job addresses": ("job_addresses", _address_label),
    "Saved line items": ("items", lambda i: f"{i['description']}  "
                                            f"(last {fmt_money(Decimal(i['price']))})"),
}


def run_list(data):
    while True:
        print()
        for title, (key, label) in LIST_KINDS.items():
            print(f"{title} ({len(data[key])}):")
            for entry in data[key]:
                print(f"  - {label(entry)}")
            if not data[key]:
                print("  (none)")
        kind = _ask(questionary.select("Delete entries from:",
                                       choices=list(LIST_KINDS) + ["Exit"]))
        if kind == "Exit":
            return
        key, label = LIST_KINDS[kind]
        if not data[key]:
            print("Nothing to delete.")
            continue
        picks = _ask(questionary.checkbox(
            "Select entries to delete (Space to select, Enter to confirm):",
            choices=[questionary.Choice(label(e), value=i) for i, e in enumerate(data[key])]))
        if picks and _ask(questionary.confirm(f"Delete {len(picks)} entr"
                                              f"{'y' if len(picks) == 1 else 'ies'}?",
                                              default=False)):
            data[key] = [e for i, e in enumerate(data[key]) if i not in set(picks)]
            store.save(data)
            print("Deleted.")
