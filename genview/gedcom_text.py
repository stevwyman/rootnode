"""Parse GEDCOM 5.5.1 text into records and a merge-friendly payload."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_STRUCTURAL_TAGS = {
    "NAME",
    "SEX",
    "FAMC",
    "FAMS",
    "CHIL",
    "HUSB",
    "WIFE",
    "OBJE",
    "NOTE",
    "SOUR",
    "RFN",
    "RIN",
    "RESN",
    "CHAN",
    "REFN",
    "AFN",
}


def parse_gedcom_date(raw_date_str: str | None) -> date | None:
    """Best-effort GEDCOM date to a Python date (for sorting / matching)."""
    if not raw_date_str:
        return None

    d = raw_date_str.upper().strip()
    if d.startswith("BET ") and " AND " in d:
        d = d.split(" AND ")[0].replace("BET ", "").strip()
    elif d.startswith("FROM ") and " TO " in d:
        d = d.split(" TO ")[0].replace("FROM ", "").strip()

    for prefix in ("ABT ", "CAL ", "EST ", "BEF ", "AFT ", "ABOUT "):
        if d.startswith(prefix):
            d = d.replace(prefix, "", 1).strip()

    year_match = re.search(r"\d{4}", d)
    if not year_match:
        return None
    year = int(year_match.group())
    month = 1
    day = 1
    for token, num in _MONTHS.items():
        if token in d:
            month = num
            break
    day_match = re.search(r"^\d{1,2}\b", d)
    if day_match:
        day = int(day_match.group())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def year_from_raw_or_parsed(raw_date: str | None, parsed_iso: str | None) -> int | None:
    if parsed_iso:
        try:
            return int(parsed_iso[:4])
        except (TypeError, ValueError):
            pass
    if raw_date:
        match = re.search(r"\d{4}", raw_date)
        if match:
            return int(match.group())
    return None


def format_gedcom_date(value: date | None) -> str:
    if value is None:
        return ""
    months = (
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
    )
    return f"{value.day} {months[value.month - 1]} {value.year}"


def decode_gedcom_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_to_records_from_text(text: str) -> list[list[str]]:
    records: list[list[str]] = []
    current_record: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue
        parts = line.split(" ", 2)
        tag = parts[1] if len(parts) > 1 else ""
        value = parts[2] if len(parts) > 2 else ""

        if tag == "CONC":
            if current_record:
                current_record[-1] += value
            continue
        if tag == "CONT":
            if current_record:
                current_record[-1] += "\n" + value
            continue

        level = parts[0] if parts else ""
        if level == "0" and current_record:
            records.append(current_record)
            current_record = []
        current_record.append(line)

    if current_record:
        records.append(current_record)
    return records


def parse_to_records(file_path: str) -> list[list[str]]:
    with open(file_path, "rb") as handle:
        text = decode_gedcom_bytes(handle.read())
    return parse_to_records_from_text(text)


def _split_line(line: str) -> tuple[str, str, str]:
    parts = line.split(" ", 2)
    level = parts[0] if parts else ""
    tag = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""
    return level, tag, value


def _extract_events(record: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in record:
        level, tag, value = _split_line(line)
        value = value.strip()
        if tag and "NOTE" in tag.upper() and tag not in ("CONT", "CONC"):
            continue
        if level == "1":
            if current:
                events.append(current)
                current = None
            if tag and tag not in _STRUCTURAL_TAGS:
                parsed = parse_gedcom_date(None)
                current = {
                    "tag": tag,
                    "raw_date": "",
                    "parsed_date": parsed.isoformat() if parsed else None,
                    "place": "",
                    "description": value,
                }
        elif level == "2" and current:
            if tag == "DATE":
                parsed = parse_gedcom_date(value)
                current["raw_date"] = value
                current["parsed_date"] = parsed.isoformat() if parsed else None
            elif tag == "PLAC":
                current["place"] = value
            elif tag == "NOTE":
                if current["description"]:
                    current["description"] += f"\n\n{value}"
                else:
                    current["description"] = value
    if current:
        events.append(current)
    return events


def _extract_notes(record: list[str]) -> str:
    notes: list[str] = []
    for line in record:
        level, tag, value = _split_line(line)
        if level == "1" and tag == "NOTE":
            notes.append(value.strip())
    return "\n\n".join(part for part in notes if part)


def extract_individual(record: list[str]) -> dict[str, Any] | None:
    header = record[0].split(" ", 2)
    if len(header) < 3 or header[2] != "INDI":
        return None
    xref = header[1]
    given_name = ""
    surname = ""
    sex = "U"
    primary_name_set = False
    alt_names: list[dict[str, str]] = []

    for i, line in enumerate(record):
        if line.startswith("1 NAME "):
            raw_name = line[7:].strip()
            current_given = ""
            current_surname = ""
            current_type = "unknown"
            if "/" in raw_name:
                parts = raw_name.split("/")
                if len(parts) >= 2:
                    current_given = parts[0].strip()
                    current_surname = parts[1].strip()
            else:
                current_given = raw_name
            j = i + 1
            while j < len(record) and record[j].startswith("2 "):
                if record[j].startswith("2 GIVN "):
                    current_given = record[j][7:].strip()
                elif record[j].startswith("2 SURN "):
                    current_surname = record[j][7:].strip()
                elif record[j].startswith("2 TYPE "):
                    current_type = record[j][7:].strip().lower()
                j += 1
            if not primary_name_set:
                given_name = current_given
                surname = current_surname
                primary_name_set = True
            else:
                alt_names.append(
                    {
                        "given_name": current_given,
                        "surname": current_surname,
                        "type": current_type,
                    }
                )
        elif line.startswith("1 SEX "):
            parsed_sex = line[6:].strip().upper()
            sex = parsed_sex if parsed_sex in ("M", "F") else "U"

    events = _extract_events(record)
    birth_event = next((ev for ev in events if ev["tag"] == "BIRT"), None)
    death_event = next((ev for ev in events if ev["tag"] == "DEAT"), None)
    return {
        "xref": xref,
        "given_name": given_name,
        "surname": surname,
        "sex": sex,
        "names": alt_names,
        "events": events,
        "notes": _extract_notes(record),
        "birth_date": (birth_event or {}).get("raw_date") or "",
        "birth_place": (birth_event or {}).get("place") or "",
        "birth_year": year_from_raw_or_parsed(
            (birth_event or {}).get("raw_date"),
            (birth_event or {}).get("parsed_date"),
        ),
        "death_date": (death_event or {}).get("raw_date") or "",
        "death_place": (death_event or {}).get("place") or "",
        "death_year": year_from_raw_or_parsed(
            (death_event or {}).get("raw_date"),
            (death_event or {}).get("parsed_date"),
        ),
    }


def extract_family(record: list[str]) -> dict[str, Any] | None:
    header = record[0].split(" ", 2)
    if len(header) < 3 or header[2] != "FAM":
        return None
    xref = header[1]
    husb = ""
    wife = ""
    children: list[str] = []
    for line in record:
        if line.startswith("1 HUSB "):
            husb = line[7:].strip()
        elif line.startswith("1 WIFE "):
            wife = line[7:].strip()
        elif line.startswith("1 CHIL "):
            children.append(line[7:].strip())
    return {
        "xref": xref,
        "husb": husb,
        "wife": wife,
        "children": children,
        "events": _extract_events(record),
        "notes": _extract_notes(record),
    }


def payload_from_records(records: list[list[str]]) -> dict[str, Any]:
    individuals: list[dict[str, Any]] = []
    families: list[dict[str, Any]] = []
    for record in records:
        if not record:
            continue
        header = record[0].split(" ", 2)
        if len(header) < 3:
            continue
        kind = header[2]
        if kind == "INDI":
            person = extract_individual(record)
            if person:
                individuals.append(person)
        elif kind == "FAM":
            family = extract_family(record)
            if family:
                families.append(family)
    return {"individuals": individuals, "families": families}


def payload_from_path(file_path: str) -> dict[str, Any]:
    return payload_from_records(parse_to_records(file_path))


def payload_from_bytes(data: bytes) -> dict[str, Any]:
    return payload_from_records(parse_to_records_from_text(decode_gedcom_bytes(data)))


def xref_form_key(xref: str) -> str:
    return (xref or "").replace("@", "").replace("/", "_")
