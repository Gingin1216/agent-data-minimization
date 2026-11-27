"""Translate concrete strings without Z3 interpreting their contents."""

import z3


def string_literal(value: str) -> z3.SeqRef:
    if any(ord(character) > 0x2FFFF for character in value):
        raise NotImplementedError("String literal exceeds the Z3 Unicode alphabet")

    # StringVal interprets escape sequences. Construct each backslash as an
    # individual character so that the neighboring text stays literal.
    parts = value.split("\\")
    literal = z3.StringVal(parts[0])
    for part in parts[1:]:
        literal = z3.Concat(literal, z3.Unit(z3.CharVal("\\")), z3.StringVal(part))
    return literal
