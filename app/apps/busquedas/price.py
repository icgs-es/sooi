from decimal import Decimal, InvalidOperation
import re


def normalize_euro_price(value):
    """Return canonical euros or None; Spanish separators never imply thousands scaling."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))

    raw = str(value).strip()
    if not raw:
        return None
    clean = re.sub(r"[^0-9,.-]", "", raw)
    if not clean or clean in {"-", ".", ","}:
        return None

    if "," in clean and "." in clean:
        # Spanish notation: dots group thousands and comma is decimal.
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        parts = clean.split(",")
        if len(parts) != 2 or len(parts[1]) not in {1, 2}:
            return None
        clean = ".".join(parts)
    elif "." in clean:
        parts = clean.split(".")
        if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            clean = "".join(parts)
        elif len(parts) != 2 or len(parts[1]) not in {1, 2}:
            return None
    try:
        price = Decimal(clean)
    except InvalidOperation:
        return None
    return price if price >= 0 else None


def format_euro_price(value):
    price = normalize_euro_price(value)
    if price is None:
        return ""
    rounded = price.quantize(Decimal("0.01"))
    if rounded == rounded.to_integral():
        return f"{int(rounded):,}".replace(",", ".") + " €"
    integer, decimals = f"{rounded:.2f}".split(".")
    return f"{int(integer):,}".replace(",", ".") + f",{decimals} €"
