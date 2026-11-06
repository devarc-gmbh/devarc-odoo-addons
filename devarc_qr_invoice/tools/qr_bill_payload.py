import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from odoo.tools.misc import mod10r

QR_TYPE = "SPC"
QR_VERSION = "0200"
CODING_TYPE = "1"
TRAILER = "EPD"
MANDATORY_LINE_COUNT = 31
MAX_LINE_COUNT = 34
MAX_MESSAGE_LENGTH = 140
CURRENCIES = ("CHF", "EUR")
MIN_AMOUNT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999.99")
QR_IID_RANGE = range(30000, 32000)
LINE_SEPARATOR = re.compile(r"\r\n|\n|\r")
IBAN_PATTERN = re.compile(r"(CH|LI)\d{7}[0-9A-Z]{12}")
AMOUNT_PATTERN = re.compile(r"\d{1,9}(\.\d{1,2})?")
COUNTRY_PATTERN = re.compile(r"[A-Z]{2}")
QR_REFERENCE_PATTERN = re.compile(r"\d{27}")
CREDITOR_REFERENCE_PATTERN = re.compile(r"RF\d{2}[0-9A-Z]{1,21}")
SWICO_S1_PREFIX = "//S1/"
SWICO_TAG_PATTERN = re.compile(r"\d{2}")
SWICO_DATE_PATTERN = re.compile(r"(\d{6})(\d{6})?")
SWICO_INVOICE_NUMBER_TAG = "10"
SWICO_INVOICE_DATE_TAG = "11"


class QrBillPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class QrBillAddress:
    address_type: str
    name: str
    street_or_line_1: str
    building_or_line_2: str
    zip: str
    city: str
    country_code: str


@dataclass(frozen=True)
class QrBillPayload:
    iban: str
    creditor: QrBillAddress
    ultimate_creditor: QrBillAddress | None
    amount: Decimal | None
    currency: str
    debtor: QrBillAddress | None
    reference_type: str
    reference: str
    unstructured_message: str
    billing_info: str
    alt_procedures: tuple[str, ...]
    invoice_number: str = ""
    invoice_date: date | None = None

    @property
    def is_qr_iban(self):
        return is_qr_iban(self.iban)


def is_qr_iban(iban):
    return int(iban[4:9]) in QR_IID_RANGE


def parse(text):
    lines = _split_lines(text)
    _check_header(lines)
    iban = _parse_iban(lines[3])
    reference_type, reference = _parse_reference(lines[27], lines[28], iban)
    unstructured_message = lines[29]
    billing_info = lines[31] if len(lines) > 31 else ""
    if len(unstructured_message) + len(billing_info) > MAX_MESSAGE_LENGTH:
        raise QrBillPayloadError(f"The unstructured message and billing information exceed {MAX_MESSAGE_LENGTH} characters.")
    swico_values = _parse_swico_s1(billing_info)
    return QrBillPayload(
        iban=iban,
        creditor=_parse_address(lines[4:11], "creditor", required=True),
        ultimate_creditor=_parse_address(lines[11:18], "ultimate creditor"),
        amount=_parse_amount(lines[18]),
        currency=_parse_currency(lines[19]),
        debtor=_parse_address(lines[20:27], "debtor"),
        reference_type=reference_type,
        reference=reference,
        unstructured_message=unstructured_message,
        billing_info=billing_info,
        alt_procedures=tuple(line for line in lines[32:] if line),
        invoice_number=swico_values.get(SWICO_INVOICE_NUMBER_TAG, ""),
        invoice_date=_parse_swico_date(swico_values.get(SWICO_INVOICE_DATE_TAG, "")),
    )


def _parse_swico_s1(billing_info):
    if not billing_info.startswith(SWICO_S1_PREFIX):
        return {}
    values = _split_swico_values(billing_info.removeprefix(SWICO_S1_PREFIX))
    tags, contents = values[::2], values[1::2]
    if len(tags) != len(contents) or not all(SWICO_TAG_PATTERN.fullmatch(tag) for tag in tags):
        return {}
    return dict(zip(tags, contents))


def _split_swico_values(text):
    values, current, chars = [], [], iter(text)
    for char in chars:
        if char == "\\":
            current.append(next(chars, ""))
        elif char == "/":
            values.append("".join(current))
            current = []
        else:
            current.append(char)
    values.append("".join(current))
    return values


def _parse_swico_date(value):
    match = SWICO_DATE_PATTERN.fullmatch(value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%m%d").date()
    except ValueError:
        return None


def _split_lines(text):
    if not isinstance(text, str) or not text.strip():
        raise QrBillPayloadError("The QR-bill payload is empty.")
    lines = LINE_SEPARATOR.split(text.strip().lstrip("﻿"))
    if not MANDATORY_LINE_COUNT <= len(lines) <= MAX_LINE_COUNT:
        raise QrBillPayloadError(
            f"A Swiss QR-bill has {MANDATORY_LINE_COUNT} to {MAX_LINE_COUNT} lines, the payload has {len(lines)}."
        )
    return lines


def _check_header(lines):
    expected_values = {0: QR_TYPE, 1: QR_VERSION, 2: CODING_TYPE, 30: TRAILER}
    for index, expected_value in expected_values.items():
        if lines[index] != expected_value:
            raise QrBillPayloadError(
                f"Line {index + 1} of the QR-bill must be '{expected_value}', found '{lines[index]}'."
            )


def _parse_iban(iban):
    if not IBAN_PATTERN.fullmatch(iban) or not _is_mod97_valid(iban):
        raise QrBillPayloadError(f"The account '{iban}' is not a valid Swiss or Liechtenstein IBAN.")
    return iban


def _parse_address(lines, role, required=False):
    if not any(lines):
        if required:
            raise QrBillPayloadError(f"The {role} address is missing.")
        return None
    address = QrBillAddress(*lines)
    if address.address_type == "S":
        missing = not (address.name and address.zip and address.city)
    elif address.address_type == "K":
        missing = not (address.name and address.building_or_line_2) or bool(address.zip or address.city)
    else:
        raise QrBillPayloadError(f"The {role} address type must be 'S' or 'K', found '{address.address_type}'.")
    if missing or not COUNTRY_PATTERN.fullmatch(address.country_code):
        raise QrBillPayloadError(f"The {role} address is incomplete.")
    return address


def _parse_amount(amount):
    if not amount:
        return None
    if not AMOUNT_PATTERN.fullmatch(amount) or not MIN_AMOUNT <= Decimal(amount) <= MAX_AMOUNT:
        raise QrBillPayloadError(f"The amount '{amount}' must be between {MIN_AMOUNT} and {MAX_AMOUNT}.")
    return Decimal(amount)


def _parse_currency(currency):
    if currency not in CURRENCIES:
        raise QrBillPayloadError(f"The currency '{currency}' is not allowed on a Swiss QR-bill, use CHF or EUR.")
    return currency


def _parse_reference(reference_type, reference, iban):
    if reference_type == "QRR":
        if not is_qr_iban(iban):
            raise QrBillPayloadError("A QR reference (QRR) requires a QR-IBAN.")
        if not QR_REFERENCE_PATTERN.fullmatch(reference) or mod10r(reference[:26]) != reference:
            raise QrBillPayloadError(f"The QR reference '{reference}' has an invalid check digit.")
    elif reference_type in ("SCOR", "NON"):
        if is_qr_iban(iban):
            raise QrBillPayloadError(f"A QR-IBAN requires a QR reference (QRR), found '{reference_type}'.")
        if reference_type == "NON" and reference:
            raise QrBillPayloadError("A QR-bill without reference (NON) must not contain a reference.")
        if reference_type == "SCOR" and (
            not CREDITOR_REFERENCE_PATTERN.fullmatch(reference) or not _is_mod97_valid(reference)
        ):
            raise QrBillPayloadError(f"The creditor reference '{reference}' is not a valid ISO 11649 reference.")
    else:
        raise QrBillPayloadError(f"The reference type '{reference_type}' must be QRR, SCOR or NON.")
    return reference_type, reference


def _is_mod97_valid(value):
    rearranged = value[4:] + value[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1
