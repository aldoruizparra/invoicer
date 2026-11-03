"""Parse a weekly work-log email (.eml) into jobs.

Expected body, loosely (typos like '10/12//2026' and missing amounts are tolerated):

    From: Contractor Name
    123 Street
    City, ST 12345
    Invoice Date: 10/12/2026
    Work Week: 10/04/2026-10/11/2026
    10/07- Client Name 123 Some St $65
    10/08- Other Client 9 Main St $50   10/08- Third 4 Elm $60
    A free-text note line
    Total: $175
"""

from __future__ import annotations

import email
import html
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from email import policy

from .money import parse_money

DATE = r"\d{1,2}\s*/+\s*\d{1,2}\s*/+\s*\d{2,4}"
INVOICE_DATE_RE = re.compile(rf"Invoice\s*Date\s*:?\s*({DATE})", re.I)
WORK_WEEK_RE = re.compile(rf"Work\s*Week\s*:?\s*({DATE})\s*(?:-|–|to)\s*({DATE})", re.I)
TOTAL_RE = re.compile(r"^\s*Total\s*:?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
# "10/07-" at the start of a job entry; several entries may share one line.
ENTRY_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})\s*-\s*")
AMOUNT_RE = re.compile(r"\$\s*([\d,]*(?:\.\d{1,2})?)")
HEADER_RE = re.compile(r"^\s*(To|From|Invoice\s*Date|Work\s*Week)\s*:", re.I)


@dataclass
class Job:
    date: date
    client: str
    address: str
    amount: Decimal | None   # None when the email left the amount blank
    source: str              # the original text, for messages


@dataclass
class WeeklyLog:
    invoice_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    sender_lines: list = field(default_factory=list)
    jobs: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    stated_total: Decimal | None = None


def read_eml_text(path):
    """Return the plain-text body of an .eml file (HTML is stripped if that's all there is)."""
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        raise ValueError(f"{path} has no readable text body")
    text = body.get_content()
    if body.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return text


def parse_date(text):
    """'10/12//2026' or '10/12/26' -> date(2026, 10, 12)."""
    month, day, year = (int(p) for p in re.split(r"\s*/+\s*", text.strip()))
    if year < 100:
        year += 2000
    return date(year, month, day)


def _infer_year(month, day, start, end):
    """Pick the year for a 'MM/DD' entry from the work week (handles Dec -> Jan weeks)."""
    if start is None:
        return date.today().year
    candidate = date(start.year, month, day)
    if end and end.year > start.year and candidate < start:
        candidate = date(end.year, month, day)
    return candidate.year


def _clean(text):
    text = " ".join(text.split())
    return re.sub(r"\s+\.$", "", text).strip()   # drop stray trailing " ."


def _parse_entry(month, day, segment, log):
    amounts = list(AMOUNT_RE.finditer(segment))
    if amounts:
        last = amounts[-1]
        desc = segment[: last.start()]
        raw = last.group(1).rstrip(".")
        amount = parse_money(raw) if raw.replace(",", "") else None
    else:
        desc, amount = segment, None
    desc = _clean(desc)
    # Client name runs until the first token that starts with a digit (house number).
    m = re.match(r"^(.*?)\s*(\d.*)$", desc)
    client, address = (_clean(m.group(1)), _clean(m.group(2))) if m else (desc, "")
    year = _infer_year(month, day, log.period_start, log.period_end)
    return Job(date(year, month, day), client, address, amount,
               f"{month:02d}/{day:02d}- {_clean(segment)}")


def parse_log(text):
    log = WeeklyLog()
    lines = [line.rstrip() for line in text.splitlines()]

    for line in lines:
        if m := INVOICE_DATE_RE.search(line):
            log.invoice_date = parse_date(m.group(1))
        if m := WORK_WEEK_RE.search(line):
            log.period_start, log.period_end = parse_date(m.group(1)), parse_date(m.group(2))

    in_from = after_week = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            in_from = False
            continue
        if m := re.match(r"^\s*From\s*:\s*(.*)$", line, re.I):
            in_from = True
            if m.group(1).strip():
                log.sender_lines.append(m.group(1).strip())
            continue
        if HEADER_RE.match(line):
            in_from = False
            after_week = after_week or bool(WORK_WEEK_RE.search(line))
            continue
        if m := TOTAL_RE.match(line):
            log.stated_total = parse_money(m.group(1))
            in_from = False
            continue
        starts = list(ENTRY_RE.finditer(line))
        if starts:
            in_from = False
            for i, s in enumerate(starts):
                end = starts[i + 1].start() if i + 1 < len(starts) else len(line)
                log.jobs.append(_parse_entry(int(s.group(1)), int(s.group(2)),
                                             line[s.end():end], log))
        elif in_from:
            log.sender_lines.append(stripped)
        elif after_week:
            log.notes.append(stripped)
    return log
