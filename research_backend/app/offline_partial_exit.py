"""Exact normalized exposure used by offline management events."""


def ratio(value):
    return {"numerator":value.numerator,"denominator":value.denominator}
