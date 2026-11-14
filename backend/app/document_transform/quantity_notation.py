import re


HORIZONTAL_SPACE = r"[^\S\r\n\v\f\u0085\u2028\u2029]"
# ISO 4217 List One, published by SIX on 2026-09-17:
# https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
# Shared by literal preservation and table presentation; never convert amounts.
# Newly supported codes stay uppercase so "all 1" and "try 2" remain prose.
# Retain the existing case-insensitive behavior of the five legacy codes.
_ISO_CURRENCY_CODES = (
    "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BOV BRL BSD "
    "BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK "
    "DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF "
    "IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP "
    "LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD "
    "NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD "
    "SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD "
    "TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAD XAF XAG XAU XBA "
    "XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWG"
)
CURRENCY_CODE = rf"(?:(?i:USD|EUR|GBP|SAR|CHF)|(?-i:{'|'.join(_ISO_CURRENCY_CODES.split())}))"
CURRENCY_SYMBOL = r"[$€£¥₹₩₽₺₱₫₦₪฿₴₡₲₵₭₮₸﷼]"
CURRENCY_PREFIX = rf"(?:{CURRENCY_SYMBOL}|{CURRENCY_CODE}(?={HORIZONTAL_SPACE}))"

TEMPERATURE_UNIT = rf"(?:°{HORIZONTAL_SPACE}*[CFcf]|[℃℉KK])"
# BIPM SI prefixes and unit symbols are case-sensitive, even when the caller
# matches surrounding prose without case. Keep both authored micro characters.
# https://www.bipm.org/en/measurement-units/si-prefixes/
_SI_PREFIX = r"(?:da|[QRYZEPTGMkhdcmnpfazyrqµμ])"
_SI_UNIT = r"(?:mol|kat|rad|sr|Hz|Pa|Wb|lm|lx|Bq|Gy|Sv|cd|[mgsAKNJWCVFΩSTH])"
_PREFIXED_SI_UNIT = rf"(?-i:{_SI_PREFIX}?{_SI_UNIT}|{_SI_PREFIX}?[Ll])"
UNIT_SYMBOL = (
    r"(?:[\"'′″]|ns|[µμu]s|ms|s|min|h|d|bit|[kKMGTPE]i?bit|B|[KMGTPE]i?B|"
    r"[kKMGT]bps|Hz|[kMGT]Hz|"
    rf"mm|cm|m|km|mg|g|kg|mL|L|{TEMPERATURE_UNIT}|mol|mmol|Pa|[kMGT]Pa|W|[kMGT]W|"
    r"J|[kMGT]J|V|mV|A|mA|Ω|kΩ|N)"
)
UNIT_POWER = rf"(?:{HORIZONTAL_SPACE}*(?:\^|\*\*){HORIZONTAL_SPACE}*[+−-]?\d+|[⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)"
UNIT_PRODUCT = rf"(?:{HORIZONTAL_SPACE}*[/*·]{HORIZONTAL_SPACE}*|{HORIZONTAL_SPACE}+)"
# Full expressions can resolve symbols such as 'as' (attosecond) exactly.
# Keep the conservative UNIT_SYMBOL inventory for unmarked products in prose,
# where the same letters can instead introduce a clause ('3 m as measured').
# Resolve the complete symbol before committing to an alternative. Otherwise
# overlapping SI/legacy symbols make a failed chain exponential to reject.
UNIT_ATOM = rf"(?>(?:{_PREFIXED_SI_UNIT}|{UNIT_SYMBOL})(?![A-Za-zµμ°ΩÅ]))(?:{UNIT_POWER})?"
# Parentheses bind the complete denominator, not just the first unit.
UNIT_GROUP = (
    rf"\({HORIZONTAL_SPACE}*{UNIT_ATOM}(?:{UNIT_PRODUCT}{UNIT_ATOM})*"
    rf"{HORIZONTAL_SPACE}*\)(?:{UNIT_POWER})?"
)
UNIT_EXPRESSION = rf"(?:{UNIT_ATOM}|{UNIT_GROUP})(?:{UNIT_PRODUCT}(?:{UNIT_ATOM}|{UNIT_GROUP}))*"

UNIT_ANNOTATION_POLICY_VERSION = "explicit-imperial-annotation-v2"
_ANNOTATION_NUMBER = (
    rf"[+−-]?(?:\d+(?:{HORIZONTAL_SPACE}+\d+)?{HORIZONTAL_SPACE}*/"
    rf"{HORIZONTAL_SPACE}*\d+|\d+(?:\.\d+)?)"
)
UNIT_ANNOTATION_PATTERN = (
    rf"\b(?:inch(?:es)?{HORIZONTAL_SPACE}*\({HORIZONTAL_SPACE}*{_ANNOTATION_NUMBER}"
    rf"{HORIZONTAL_SPACE}*(?P<inch_mark>[\"″])|"
    rf"(?:foot|feet){HORIZONTAL_SPACE}*\({HORIZONTAL_SPACE}*{_ANNOTATION_NUMBER}"
    rf"{HORIZONTAL_SPACE}*(?P<foot_mark>['′])){HORIZONTAL_SPACE}*\)"
)
_UNIT_ANNOTATION = re.compile(UNIT_ANNOTATION_PATTERN, re.IGNORECASE)


def mask_unit_annotation_marks(source: str) -> str:
    # Explicit unit words disambiguate their parenthetical abbreviation. Do not
    # guess whether an arbitrary quote after a digit closes quoted prose.
    characters = list(source)
    for match in _UNIT_ANNOTATION.finditer(source):
        mark = "inch_mark" if match["inch_mark"] is not None else "foot_mark"
        characters[match.start(mark)] = " "
    return "".join(characters)
