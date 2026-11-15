"""Strict conversion of environment strings and Python fallback values."""
import json
import re

from .core import SafetyError


def boolean_setting(name, value):
    if type(value) is bool:
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1"):
            return True
        if text in ("false", "0"):
            return False
    raise SafetyError(f"{name} must be true/false or 1/0; no default was substituted.")


def integer_setting(name, value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        return int(value)
    raise SafetyError(f"{name} must be an integer; no default was substituted.")


def _json_value(name, value):
    if not isinstance(value, str):
        return value

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise SafetyError(f"{name} contains duplicate JSON keys.")
            result[key] = item
        return result

    try:
        return json.loads(value, object_pairs_hook=unique_object)
    except ValueError:
        # Never include the raw JSON: security-question answers may be in it.
        raise SafetyError(f"{name} must contain valid JSON; no default was substituted.") from None


def answer_setting(name, value):
    result = _json_value(name, value)
    if not isinstance(result, dict) or any(
        not isinstance(key, str) or not isinstance(answer, str)
        for key, answer in result.items()
    ):
        raise SafetyError(f"{name} must be a JSON object mapping question strings to answer strings.")
    return dict(result)


def range_setting(name, value):
    result = _json_value(name, value)
    if not isinstance(result, (list, tuple)) or any(
        not isinstance(pair, (list, tuple)) or len(pair) != 2
        or any(not isinstance(day, str) for day in pair)
        for pair in result
    ):
        raise SafetyError(f"{name} must be a JSON array of [start-date, end-date] string pairs.")
    return tuple(tuple(pair) for pair in result)


def time_setting(name, value):
    result = _json_value(name, value)
    if not isinstance(result, (list, tuple)) or any(not isinstance(item, str) for item in result):
        raise SafetyError(f"{name} must be a JSON array of HH:MM strings.")
    return tuple(result)
