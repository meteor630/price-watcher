"""openapi/v1.yaml: валидный OpenAPI 3.1 и общие правила контракта из его описания."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError
from referencing.exceptions import PointerToNowhere

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi" / "v1.yaml"
SPEC: dict[str, Any] = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
MUTATING = {"post", "put", "patch", "delete"}


def operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if method in MUTATING | {"get"}
    ]


def walk(node: Any, trail: str = ""):
    """Все пары (путь в документе, узел-словарь)."""
    if isinstance(node, dict):
        yield trail, node
        for key, value in node.items():
            yield from walk(value, f"{trail}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{trail}/{index}")


def test_document_is_valid_openapi() -> None:
    validate(SPEC)


def test_structural_error_is_rejected() -> None:
    broken = copy.deepcopy(SPEC)
    del broken["paths"]["/me"]["get"]["responses"]["200"]["description"]

    with pytest.raises(OpenAPIValidationError):
        validate(broken)


def test_dangling_reference_is_rejected() -> None:
    broken = copy.deepcopy(SPEC)
    broken["paths"]["/me"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Absent"
    }

    with pytest.raises(PointerToNowhere):
        validate(broken)


def test_mutating_operations_require_idempotency_key() -> None:
    missing = [
        f"{method.upper()} {path}"
        for path, method, operation in operations(SPEC)
        if method in MUTATING
        and not path.startswith("/auth/")
        and {"$ref": "#/components/parameters/IdempotencyKey"} not in operation.get("parameters", [])
    ]

    assert missing == []


def test_only_auth_endpoints_skip_bearer() -> None:
    public = sorted(path for path, _, operation in operations(SPEC) if operation.get("security") == [])

    assert SPEC["security"] == [{"bearer": []}]
    assert public == ["/auth/pair", "/auth/refresh"]


def test_money_is_never_a_float() -> None:
    floats = [trail for trail, node in walk(SPEC) if node.get("type") == "number" or "number" in (node.get("type") or [])]

    assert floats == []
    assert SPEC["components"]["schemas"]["MoneyMinor"] == {
        "type": "integer",
        "format": "int64",
        "description": "Сумма в копейках, только RUB.",
    }


def test_minor_fields_reference_money_or_integer() -> None:
    wrong = []
    for trail, node in walk(SPEC["components"]["schemas"]):
        for name, schema in node.get("properties", {}).items() if isinstance(node.get("properties"), dict) else []:
            if not name.endswith("_minor"):
                continue
            variants = schema.get("oneOf", [schema])
            if not any(v.get("$ref") == "#/components/schemas/MoneyMinor" or v.get("type") == "integer" for v in variants):
                wrong.append(f"{trail}/{name}")

    assert wrong == []


def test_field_names_are_snake_case() -> None:
    bad = [
        f"{trail}/{name}"
        for trail, node in walk(SPEC)
        if isinstance(node.get("properties"), dict)
        for name in node["properties"]
        if name != name.lower() or "-" in name
    ]

    assert bad == []


def test_request_bodies_reject_unknown_fields() -> None:
    open_bodies = []
    for path, method, operation in operations(SPEC):
        body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if body is None:
            continue
        resolved = SPEC["components"]["schemas"][body["$ref"].rsplit("/", 1)[1]] if "$ref" in body else body
        variants = resolved.get("oneOf", [resolved])
        if any(v.get("additionalProperties") is not False for v in variants):
            open_bodies.append(f"{method.upper()} {path}")

    assert open_bodies == []
