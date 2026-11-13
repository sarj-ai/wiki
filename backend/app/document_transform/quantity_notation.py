HORIZONTAL_SPACE = r"[^\S\r\n\v\f\u0085\u2028\u2029]"
UNIT_SYMBOL = (
    r"(?:ns|[µμu]s|ms|s|min|h|d|bit|[kKMGTPE]i?bit|B|[KMGTPE]i?B|"
    r"[kKMGT]bps|Hz|[kMGT]Hz|"
    r"mm|cm|m|km|mg|g|kg|mL|L|K|°[CF]|mol|mmol|Pa|[kMGT]Pa|W|[kMGT]W|"
    r"J|[kMGT]J|V|mV|A|mA|Ω|kΩ|N)"
)
UNIT_POWER = rf"(?:{HORIZONTAL_SPACE}*(?:\^|\*\*){HORIZONTAL_SPACE}*[+−-]?\d+|[⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)"
UNIT_PRODUCT = rf"(?:{HORIZONTAL_SPACE}*[/*·]{HORIZONTAL_SPACE}*|{HORIZONTAL_SPACE}+)"
UNIT_ATOM = rf"{UNIT_SYMBOL}(?:{UNIT_POWER})?"
# Parentheses bind the complete denominator, not just the first unit.
UNIT_GROUP = (
    rf"\({HORIZONTAL_SPACE}*{UNIT_ATOM}(?:{UNIT_PRODUCT}{UNIT_ATOM})*"
    rf"{HORIZONTAL_SPACE}*\)(?:{UNIT_POWER})?"
)
UNIT_EXPRESSION = rf"(?:{UNIT_ATOM}|{UNIT_GROUP})(?:{UNIT_PRODUCT}(?:{UNIT_ATOM}|{UNIT_GROUP}))*"
