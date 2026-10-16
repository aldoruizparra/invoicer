"""Local JSON storage at ~/.invoicer/data.json (override with INVOICER_HOME)."""

import copy
import json
import os
import tempfile
from pathlib import Path

DEFAULT_FOOTER = "Materials and labors included in this invoice."

DEFAULT_DATA = {
    "sender": {
        "name": "",
        "phone": "",
        "email": "",
        "street": "",
        "city_state_zip": "",
        "payable_to": "",
        "footer_text": DEFAULT_FOOTER,
    },
    "last_invoice_number": 0,
    "default_tax_rate": "8.25",
    "contacts": [],
    "job_addresses": [],
    "items": [],
    "last_invoice": None,
}

CONTACT_FIELDS = ("attention", "attention2", "company", "street", "city_state")


def data_dir():
    return Path(os.environ.get("INVOICER_HOME") or Path.home() / ".invoicer")


def data_path():
    return data_dir() / "data.json"


def load():
    """Load data.json, creating it with defaults on first run."""
    path = data_path()
    data = copy.deepcopy(DEFAULT_DATA)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        data.update(stored)
        data["sender"] = {**DEFAULT_DATA["sender"], **stored.get("sender", {})}
    else:
        save(data)
    return data


def save(data):
    """Write atomically so a crash never leaves a half-written file."""
    path = data_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".data-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _norm(value):
    return " ".join(str(value or "").split()).casefold()


def contact_key(contact):
    return tuple(_norm(contact.get(k)) for k in CONTACT_FIELDS)


def item_key(item):
    return _norm(item["description"])


def address_key(address):
    return _norm(address)


def push_front(entries, entry, key):
    """Move entry to the front of entries (most recently used first), replacing duplicates."""
    k = key(entry)
    entries[:] = [entry] + [e for e in entries if key(e) != k]


def find_item(data, description):
    k = _norm(description)
    return next((i for i in data["items"] if item_key(i) == k), None)
