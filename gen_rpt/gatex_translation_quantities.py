"""Offline quantity protection for faithful Chinese PDF translations.

Only a source's spelled-out thousand/million/billion/trillion quantity permits
an exact change of numeric scale. Other numeric literals, codes, dates, rates,
links and per-unit suffixes remain literal. Written currency names have source-bound
Chinese counterparts; a bare monetary symbol never acquires a country identity.
This certifies numeric identity
only; the caller still checks complete prose, identities, independent semantic
review and the rendered PDF layout.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import re


class QuantityFidelityError(ValueError):
    """A source-free error code suitable for the caller's validation boundary."""


_SCALES = {"thousand": 1000, "million": 1000000, "billion": 1000000000,
           "trillion": 1000000000000, "百": 100, "千": 1000, "万": 10000,
           "亿": 100000000, "万亿": 1000000000000}
_CODES = "USD|CNY|RMB|AED|SAR|QAR|BHD|OMR|KWD|EUR|GBP|JPY|HKD|SGD|INR|IDR|KRW|AUD|CAD|CHF|MYR|THB|VND|PHP|TRY|ZAR|BRL|MXN|RUB|NZD"
_SYMBOLS = r"[A-Z]{1,3}\$|[$€£¥₹₩]"
# Currency words are source material too. Protect their identity instead of
# counting a legitimate Chinese currency noun as an unexplained added amount.
# Long country-qualified forms precede a generic dollar. Bare $ / ¥ retain
# their original ambiguous symbols and never acquire an ISO currency identity.
_CURRENCY_NAMES = [
    ("USD", "美元", ("United States dollar", "U.S. dollar", "US dollar", "dollar")),
    ("CAD", "加元", ("Canadian dollar",)),
    ("AUD", "澳元", ("Australian dollar",)),
    ("NZD", "新西兰元", ("New Zealand dollar",)),
    ("SGD", "新加坡元", ("Singapore dollar",)),
    ("HKD", "港元", ("Hong Kong dollar",)),
    ("EUR", "欧元", ("euro",)),
    ("GBP", "英镑", ("British pound", "pound sterling")),
    ("CNY", "人民币", ("Chinese yuan", "Chinese renminbi", "renminbi")),
    ("JPY", "日元", ("Japanese yen",)),
    ("INR", "卢比", ("Indian rupee",)),
    ("KRW", "韩元", ("South Korean won", "Korean won")),
    ("AED", "阿联酋迪拉姆", ("UAE dirham", "Emirati dirham")),
    ("SAR", "沙特里亚尔", ("Saudi riyal",)),
    ("QAR", "卡塔尔里亚尔", ("Qatari riyal",)),
]
_CURRENCY_NAME_ID, _CURRENCY_NAME_ZH = {}, {}
for _iso, _zh, _names in _CURRENCY_NAMES:
    _CURRENCY_NAME_ID[_zh] = _iso
    for _name in _names:
        _forms = (_name, _name + "s") if _name.endswith(("dollar", "pound", "euro", "rupee", "dirham", "riyal")) else (_name,)
        if _name == "pound sterling":
            _forms = (_name, "pounds sterling")
        for _form in _forms:
            _CURRENCY_NAME_ID[_form.casefold()] = _iso
            _CURRENCY_NAME_ZH[_form.casefold()] = _zh
# Unqualified yuan is an amount-bound written currency unit. Do not classify
# the surname Yuan or the Chinese character 元 inside ordinary prose as money.
_CURRENCY_NAME_ID.update({"yuan": "CNY", "元": "CNY"})
_CURRENCY_NAME_ZH["yuan"] = "元"
_WORD_NAMES = sorted(_CURRENCY_NAME_ZH, key=len, reverse=True)
_CURRENCY_EN = r"(?i:(?:" + "|".join(re.escape(name).replace(r"\ ", r"\s+") for name in _WORD_NAMES) + r"))(?![A-Za-z])"
_CURRENCY_ZH_NAMED = "|".join(sorted((row[1] for row in _CURRENCY_NAMES), key=len, reverse=True))
_CURRENCY_ZH = _CURRENCY_ZH_NAMED + "|元"
_CURRENCY = rf"(?:{_CURRENCY_EN}|{_CODES}|{_SYMBOLS}|{_CURRENCY_ZH_NAMED})"
_CURRENCY_ID = {"US$": "USD", "HK$": "HKD", "AU$": "AUD", "NZ$": "NZD",
                "CA$": "CAD", "SG$": "SGD", "£": "GBP", "€": "EUR", "₹": "INR", "₩": "KRW",
                "RMB": "CNY", "美元": "USD", "英镑": "GBP", "欧元": "EUR",
                "人民币": "CNY", "元": "CNY", "日元": "JPY", "卢比": "INR",
                "韩元": "KRW"}
# An unqualified $, ¥ or short A$/C$/S$ does not prove a country/currency. Preserve its symbol;
# do not silently identify dollars as USD or yen/yuan as JPY/CNY.
_NUMBER = r"(?:[+\-−]\s*)?(?:\d+(?:[,\u202f]\d{3})*(?:\.\d+)?|\.\d+)"
# A marker directly followed by another numeric value belongs to that next
# amount, rather than being consumed as the preceding amount's suffix.
_CURRENCY_SUFFIX = rf"(?:{_CURRENCY}(?![A-Za-z0-9_]|\s*{_NUMBER})|{_CURRENCY_ZH})"
_SCALE = r"(?i:(?:thousand|million|billion|trillion)s?)(?![A-Za-z])|万亿|亿|万|千|百|[KMBT](?![A-Za-z])"
_PERCENT = r"[%‰％]|(?i:-?(?:percentage[\s-]+points?|percent[\s-]+points?|per[\s-]+cent|percent))(?![A-Za-z])|个百分点"
_PER_UNIT = r"/[A-Za-z][A-Za-z0-9_.-]*"
_CONSTRAINT = r"(?:<=|>=|!=|==|[~∼±∓≤≥<>≈≠=])"
_MATH_SYMBOL = re.compile(r"[~∼+\-−–±∓≤≥<>≈≠=×÷*/^]")
_MATH_GAP = re.compile(r"[\s()+\-−–±∓≤≥<>≈≠=×÷*/^]*")
_QUANTITY_BODY = (
    rf"(?:(?P<constraint>{_CONSTRAINT})\s*)?"
    rf"(?:(?:(?P<outer>[+\-−])\s*)?(?P<currency>{_CURRENCY})\s*)?"
    rf"(?:(?P<constraint_after_currency>{_CONSTRAINT})\s*)?"
    rf"(?P<number>{_NUMBER})(?P<scale_gap>\s*)(?P<scale>{_SCALE})?"
    rf"(?P<lower_bound>\+(?!\s*{_NUMBER}))?"
    rf"(?P<percent>{_PERCENT})?(?P<currency_gap>\s*)(?P<currency_suffix>{_CURRENCY_SUFFIX})?"
    rf"(?P<per_gap>\s*)(?P<per>{_PER_UNIT})?"
    rf"(?P<currency_final_gap>\s*)(?P<currency_final>{_CURRENCY_SUFFIX})?"
)
_QUANTITY = re.compile(rf"(?<![A-Za-z0-9_.]){_QUANTITY_BODY}(?![A-Za-z0-9_]|\.[A-Za-z0-9]|-[A-Za-z])")
_PARSE_QUANTITY = re.compile(_QUANTITY_BODY)
_ORDINAL = r"(?:第\d+|\d+(?:st|nd|rd|th))(?![A-Za-z0-9])"
_PAGE_REFERENCE = r"(?<![A-Za-z0-9_.+\-–—/])(?:(?i:p\.|page)\s*\d+|\d+(?i:st|nd|rd|th)\s+(?i:page)|第\d+页)(?![A-Za-z0-9]|\.[A-Za-z0-9]|\s*[-–—/]\s*\d)"
# A complete tracked chapter banner is typography, not a division with a
# missing operand. Only this explicit label shape receives a Chinese form;
# arbitrary slashes, calculations and prose keep their existing protection.
_CHAPTER_LABEL = r"(?m:^[ \t]*(?:C[ \t]+H[ \t]+A[ \t]+P[ \t]+T[ \t]+E[ \t]+R[ \t]+\d(?:[ \t]*\d)*[ \t]*/[ \t]*C[ \t]+O[ \t]+N[ \t]+T[ \t]+I[ \t]+N[ \t]+U[ \t]+E[ \t]+D|第\d+章[ \t]*/[ \t]*续)[ \t]*$)"
_LINK = r"(?:https?://|www\.)[^\s<>\u3000-\u303f\u3400-\u9fff\uff00-\uffef]+|[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+\.[A-Za-z]{2,}"
_DOMAIN = r"(?<![A-Za-z0-9_.])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s<>\u3000-\u303f\u3400-\u9fff\uff00-\uffef]*)?"
_DATE = r"(?<!\d)(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4})(?!\d)"
_TIME = r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)"
_NUMBER_CODE = r"(?<![A-Za-z0-9_.])(?:\d+(?:\.\d+){2,}|\d+(?:/\d+)+)(?![A-Za-z0-9_.])"
_CODE = r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_.:/+\-]*[A-Za-z])(?=[A-Za-z0-9_.:/+\-]*\d)[A-Za-z0-9][A-Za-z0-9_.:/+\-]*(?![A-Za-z0-9_])"
_PLACEHOLDER = r"\{[^{}\n]+\}"
# Bare 元 is a currency only when attached to a numeric quantity, not when it
# occurs inside ordinary words such as 元件、多元化 or 元素.
_STANDALONE_CURRENCY = rf"(?<![A-Za-z])(?!(?i:yuan)(?![A-Za-z]))(?:{_CURRENCY}|{_CURRENCY_ZH_NAMED})(?![A-Za-z])"
_SCAN = re.compile("|".join([
    rf"(?P<chapter_label>{_CHAPTER_LABEL})",
    rf"(?P<link>{_LINK})", rf"(?P<domain>{_DOMAIN})", rf"(?P<date>{_DATE})",
    rf"(?P<time>{_TIME})", rf"(?P<placeholder>{_PLACEHOLDER})",
    rf"(?P<number_code>{_NUMBER_CODE})", rf"(?P<page_reference>{_PAGE_REFERENCE})", rf"(?P<ordinal>{_ORDINAL})",
    rf"(?P<quantity>{_QUANTITY.pattern})", rf"(?P<code>{_CODE})",
    rf"(?P<standalone_currency>{_STANDALONE_CURRENCY})",
]))
_TOKEN_PATTERN = re.compile(r"\{GATEX_QUANTITY_(\d+)\}")


@dataclass(frozen=True)
class ProtectedToken:
    placeholder: str
    original: str
    replacement: str
    kind: str


@dataclass(frozen=True)
class ProtectedQuantities:
    masked: str
    tokens: tuple[ProtectedToken, ...]

    @property
    def hints(self) -> list[dict[str, str]]:
        return [{"placeholder": token.placeholder, "value": token.replacement,
                 "kind": token.kind} for token in self.tokens]

    def restore(self, text: str) -> str:
        if not isinstance(text, str):
            raise QuantityFidelityError("QUANTITY_PLACEHOLDER_MISMATCH")
        expected = {token.placeholder: token.replacement for token in self.tokens}
        found = Counter(match.group(0) for match in _TOKEN_PATTERN.finditer(text))
        reserved_starts = len(re.findall(r"\{GATEX_QUANTITY_", text))
        if (reserved_starts != sum(found.values()) or set(found) != set(expected)
                or any(count != 1 for count in found.values())):
            raise QuantityFidelityError("QUANTITY_PLACEHOLDER_MISMATCH")
        return _TOKEN_PATTERN.sub(lambda match: expected[match.group(0)], text)


@dataclass(frozen=True)
class _Atom:
    start: int
    end: int
    original: str
    replacement: str
    kind: str
    literal_key: tuple
    equivalent_key: tuple | None = None
    convertible: bool = False
    operator_prefix: str = ""
    operands: tuple[_Atom, ...] = ()
    separators: tuple[str, ...] = ()


def _compact_number(value: str) -> str:
    return re.sub(r"\s+", "", value.replace(",", "").replace("−", "-"))


def _decimal_text(value: Fraction) -> str:
    sign = "-" if value < 0 else ""
    numerator, denominator = abs(value.numerator), value.denominator
    whole, remainder = divmod(numerator, denominator)
    digits = []
    while remainder:
        digit, remainder = divmod(remainder * 10, denominator)
        digits.append(str(digit))
    return sign + str(whole) + ("." + "".join(digits) if digits else "")


def _currency_name(value: str) -> tuple[str, str] | None:
    key = " ".join(value.split()).casefold()
    identity = _CURRENCY_NAME_ID.get(key)
    if identity is None:
        return None
    chinese = _CURRENCY_NAME_ZH.get(key, value)
    return identity, chinese


def _currency_id(value: str) -> str:
    name = _currency_name(value)
    return name[0] if name else _CURRENCY_ID.get(value, value)


def _percent_id(value: str) -> str:
    normalized = " ".join(value.casefold().replace("-", " ").split())
    if normalized in {"%", "％", "per cent", "percent"}:
        return "%"
    if normalized in {"percentage point", "percentage points", "percent point", "percent points", "个百分点"}:
        return "个百分点"
    return normalized


def _quantity_atom(match: re.Match, text: str) -> _Atom:
    original = match.group(0).rstrip()
    end = match.start() + len(original)
    parsed = _PARSE_QUANTITY.fullmatch(original)
    if parsed is None:
        raise QuantityFidelityError("UNSUPPORTED_QUANTITY")
    parts = parsed.groupdict()
    number, outer = parts["number"], parts["outer"] or ""
    constraint = parts["constraint"] or parts["constraint_after_currency"] or ""
    currency = parts["currency"] or ""
    suffix_currency = parts["currency_suffix"] or parts["currency_final"] or ""
    scale = parts["scale"] or ""
    normalized_scale = scale.casefold().removesuffix("s") if re.fullmatch(r"(?i:thousands?|millions?|billions?|trillions?)", scale) else scale
    percent, per = parts["percent"] or "", parts["per"] or ""
    lower_bound = parts["lower_bound"] or ""
    percent_unit = _percent_id(percent)
    unsupported = bool(text[end:].lstrip()[:1] in {"十", "百", "千", "万", "亿"})
    unsupported |= bool(re.match(_PERCENT, text[end:].lstrip()))
    unsupported |= bool(outer and number.startswith(("+", "-", "−")))
    unsupported |= bool(currency and suffix_currency)
    unsupported |= bool(parts["currency_suffix"] and parts["currency_final"])
    unsupported |= bool(parts["constraint"] and parts["constraint_after_currency"])
    convertible = normalized_scale in {"thousand", "million", "billion", "trillion"} and not percent
    numeric = None
    if sum(character.isdigit() for character in number) <= 256:
        try:
            numeric = Fraction(Decimal(_compact_number(number)))
        except (InvalidOperation, ValueError):
            unsupported = True
    elif convertible:
        unsupported = True
    if numeric is not None:
        if outer in {"-", "−"}:
            numeric = -numeric
        numeric *= _SCALES.get(normalized_scale, 1)
    negative = outer in {"-", "−"} or number.startswith(("-", "−"))
    explicit_plus = outer == "+" or number.startswith("+")
    sign = "negative" if negative else "explicit_positive" if explicit_plus else "positive"
    if unsupported:
        return _Atom(match.start(), end, original, original, "unsupported",
                     ("unsupported", original))
    # Plain values and numeric abbreviations retain their exact digits,
    # leading zeros, decimal places, commas and currency marker. Written
    # percent and percentage-point units have distinct canonical forms.
    currency_name = _currency_name(currency or suffix_currency)
    literal_currency = ("name", currency_name[0]) if currency_name else currency
    literal_suffix = "" if currency_name else suffix_currency
    # Currency nouns can move around their own amount in Chinese. Symbols and
    # ISO codes keep the previous literal-position contract for plain numbers.
    literal = ("quantity", number, literal_currency, normalized_scale, percent_unit, per, literal_suffix, outer, constraint, lower_bound)
    equivalent = (numeric, _currency_id(currency or suffix_currency), sign, percent_unit, per, constraint, lower_bound) if numeric is not None else None
    replacement = original
    if percent and percent != percent_unit:
        start, stop = parsed.span("percent")
        replacement = original[:start].rstrip() + percent_unit + original[stop:]
    if convertible and numeric is not None:
        absolute = abs(numeric)
        unit = "万亿" if absolute >= 10**12 else "亿" if absolute >= 10**8 else "万" if absolute >= 10**4 else ""
        value = numeric / _SCALES.get(unit, 1)
        rendered = _decimal_text(value)
        # Fraction loses the spelling of signed zero. The source sign stays.
        if negative and value == 0:
            rendered = "-" + rendered
        if explicit_plus:
            rendered = "+" + rendered
        if outer:
            rendered = rendered.lstrip("+-")
        separator = " " if currency in _CODES.split("|") else ""
        suffix_separator = " " if suffix_currency in _CODES.split("|") else ""
        # Retain whether the source bound its currency before or after /unit.
        suffix = suffix_separator + suffix_currency if suffix_currency else ""
        ending = suffix + per if parts["currency_suffix"] else per + suffix
        replacement = constraint + outer + currency + separator + rendered + unit + lower_bound + ending
    if currency_name and " ".join((currency or suffix_currency).split()).casefold() in _CURRENCY_NAME_ZH:
        # Only written names acquire a Chinese rendering. Source $ / US$ /
        # USD notation is never rewritten by this branch.
        label = currency_name[1]
        if convertible and numeric is not None:
            amount = rendered + unit + lower_bound
        else:
            amount = number + scale + lower_bound + percent_unit
        ending = per + label if parts["currency_final"] else label + per
        replacement = constraint + outer + amount + ending
    operator_prefix = constraint or outer or (number[:1] if number.startswith(("+", "-", "−")) else "")
    return _Atom(match.start(), end, original, replacement,
                 "scaled_quantity" if convertible else "quantity", literal, equivalent, convertible,
                 operator_prefix)


def _expressions(text: str, atoms: list[_Atom]) -> list[_Atom]:
    """Protect local numeric expressions in order, without evaluating arithmetic.

    Prose separators leave independent amounts independent. Opaque codes,
    dates, links and per-unit suffixes are already whole atoms and are untouched.
    A nearby operator we cannot place in a numeric expression fails closed.
    """
    numeric = {"quantity", "scaled_quantity"}
    combined, index = [], 0
    while index < len(atoms):
        first = atoms[index]
        group, separators = [first], []
        following = index + 1
        while first.kind in numeric and following < len(atoms):
            previous, current = group[-1], atoms[following]
            if current.kind not in numeric:
                break
            gap = text[previous.end:current.start]
            if not _MATH_GAP.fullmatch(gap):
                break
            if not (_MATH_SYMBOL.search(gap) or (not gap.strip() and current.operator_prefix)):
                break
            separators.append(re.sub(r"\s+", "", gap))
            group.append(current)
            following += 1
        if len(group) == 1:
            combined.append(first)
            index += 1
            continue
        chunks, cursor = [], first.start
        for operand in group:
            chunks.extend((text[cursor:operand.start], operand.replacement))
            cursor = operand.end
        original = text[first.start:group[-1].end]
        combined.append(_Atom(first.start, group[-1].end, original, "".join(chunks),
                              "quantity_expression", ("quantity_expression", original),
                              operands=tuple(group), separators=tuple(separators)))
        index = following
    # Operators outside protected regions must not quietly become untracked
    # punctuation if they are next to a quantity or ordinal.
    for match in _MATH_SYMBOL.finditer(text):
        if any(atom.start <= match.start() < atom.end for atom in combined):
            continue
        before = next((atom for atom in reversed(combined) if atom.end <= match.start()), None)
        after = next((atom for atom in combined if atom.start >= match.end()), None)
        nearby = ((before and before.kind in numeric | {"ordinal"}
                   and re.fullmatch(r"[\s()]*", text[before.end:match.start()]))
                  or (after and after.kind in numeric | {"ordinal"}
                      and re.fullmatch(r"[\s()]*", text[match.end():after.start])))
        if nearby:
            combined.append(_Atom(match.start(), match.end(), match.group(), match.group(),
                                  "unsupported", ("unsupported", match.group())))
    return sorted(combined, key=lambda atom: atom.start)


def _atoms(text: str) -> list[_Atom]:
    atoms = []
    for match in _SCAN.finditer(text):
        kind = match.lastgroup
        # Inner named groups in the quantity alternative may become lastgroup.
        if match.group("quantity") is not None:
            atoms.append(_quantity_atom(match, text))
        elif match.group("chapter_label") is not None:
            original = match.group(0)
            digits = "".join(re.findall(r"\d", original))
            atoms.append(_Atom(match.start(), match.end(), original, "第" + digits + "章 / 续",
                               "chapter_label", ("chapter_label", digits, "continued")))
        elif match.group("page_reference") is not None:
            original = match.group(0)
            digits = re.search(r"\d+", original).group(0)
            atoms.append(_Atom(match.start(), match.end(), original, "第" + digits + "页",
                               "page_reference", ("page_reference", digits)))
        elif match.group("ordinal") is not None:
            original = match.group(0)
            digits = re.search(r"\d+", original).group(0)
            atoms.append(_Atom(match.start(), match.end(), original, "第"+digits,
                               "ordinal", ("ordinal", digits)))
        else:
            original = match.group(0)
            if kind in {"link", "domain"}:
                original = original.rstrip(".,;:!?)\u005d}")
            elif kind == "code":
                # A sentence terminator is not part of Q3 or GPT-4.1. Keep
                # internal version dots literal, leaving only trailing dots
                # outside the protected code for natural Chinese punctuation.
                original = original.rstrip(".")
            name = _currency_name(original) if kind == "standalone_currency" else None
            atoms.append(_Atom(match.start(), match.start()+len(original), original,
                               name[1] if name else original, kind,
                               ("currency_name", name[0]) if name else (kind, original)))
    return _expressions(text, atoms)


def protect_quantities(source: str) -> ProtectedQuantities:
    """Mask immutable tokens and render spelled scales with exact arithmetic.

    The returned hints describe complete values. A caller must instruct its
    model to retain every placeholder exactly once and append no new scale or
    currency. Restore before final quantity and independent semantic checks.
    """
    tokens, chunks, cursor = [], [], 0
    for atom in _atoms(source):
        if atom.kind == "unsupported":
            raise QuantityFidelityError("UNSUPPORTED_QUANTITY")
        placeholder = f"{{GATEX_QUANTITY_{len(tokens)}}}"
        chunks.extend((source[cursor:atom.start], placeholder))
        tokens.append(ProtectedToken(placeholder, atom.original, atom.replacement, atom.kind))
        cursor = atom.end
    chunks.append(source[cursor:])
    return ProtectedQuantities("".join(chunks), tuple(tokens))


def quantities_equivalent(source: str, target: str) -> bool:
    """Check exact literals plus narrowly permitted spelled-scale equivalents.

    This certifies numeric identity only. It cannot approve source attribution,
    omissions, added facts, changed uncertainty or the quality of Chinese prose.
    """
    originals, counterparts = _atoms(source), _atoms(target)
    if any(atom.kind == "unsupported" for atom in originals + counterparts):
        return False
    remaining = list(counterparts)
    for atom in originals:
        if atom.kind != "quantity_expression":
            continue
        index = next((i for i, candidate in enumerate(remaining)
                      if candidate.kind == "quantity_expression"
                      and candidate.separators == atom.separators
                      and len(candidate.operands) == len(atom.operands)
                      and all((left.equivalent_key == right.equivalent_key)
                              if left.convertible else (left.literal_key == right.literal_key)
                              for left, right in zip(atom.operands, candidate.operands))), None)
        if index is None:
            return False
        remaining.pop(index)
    originals = [atom for atom in originals if atom.kind != "quantity_expression"]
    for atom in originals:
        if atom.convertible:
            continue
        index = next((i for i, candidate in enumerate(remaining)
                      if candidate.literal_key == atom.literal_key
                      or (atom.kind == "page_reference" and candidate.kind == "quantity"
                          and candidate.original == atom.literal_key[1]
                          and target[candidate.end:].lstrip().startswith("页"))), None)
        if index is None:
            return False
        remaining.pop(index)
    expected = Counter(atom.equivalent_key for atom in originals if atom.convertible)
    actual = Counter(atom.equivalent_key for atom in remaining)
    return None not in actual and expected == actual
