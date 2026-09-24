"""What the running service serves, against what the contract says it serves.

The comparison is a projection of both documents onto the shape of the API: which operations exist,
what each one is called, what it accepts by name, and what it answers with by name — with references
resolved on both sides, so `HTTPValidationError` where the contract names `ErrorResponse` is visible
as the disagreement it is.

Field *types* are deliberately not part of the projection: pydantic renders them (a decimal as a
string with a pattern, a nested model as a reference), which is what it derives rather than what the
contract states, and the generated models already enforce them — a response whose fields are not the
ones the contract declares is refused by the client in
`test_the_client_against_the_running_systems.py`, which fails for the same reason this file does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

FRAMEWORK_ROUTES: Final = ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
"""What FastAPI serves for its own documentation: no contract about the systems describes these."""


def test_the_service_serves_every_operation_the_contract_declares(
    contract: Mapping[str, Any], served: Mapping[str, Any]
) -> None:
    assert _operations(served) == _operations(contract)


def test_the_served_contract_is_the_committed_one(
    contract: Mapping[str, Any], served: Mapping[str, Any]
) -> None:
    assert _projection(served) == _projection(contract)


def test_every_operation_has_one_success_status(
    contract: Mapping[str, Any], served: Mapping[str, Any]
) -> None:
    """One success status per operation is what the generated client's `expected` argument means."""
    for document in (contract, served):
        for operation in _projection(document).values():
            successes = [status for status in operation["responses"] if status.startswith("2")]
            assert len(successes) == 1, operation["operationId"]


def _operations(document: Mapping[str, Any]) -> dict[str, str]:
    """Where every operation lives and what it is called: `POST /claims` reads as `create_claim`."""
    return {
        f"{method.upper()} {path}": operation["operationId"]
        for path, item in document["paths"].items()
        if path not in FRAMEWORK_ROUTES
        for method, operation in item.items()
    }


def _projection(document: Mapping[str, Any]) -> dict[str, Any]:
    """Everything the document says about the shape of the API, in one comparable form."""
    return {
        operation: {
            "operationId": declared["operationId"],
            "parameters": {
                parameter["name"]: {
                    "in": parameter["in"],
                    "required": bool(parameter.get("required", False)),
                    "accepts": _accepts(document, parameter),
                }
                for parameter in (
                    _resolved(document, node) for node in declared.get("parameters", [])
                )
            },
            "request": _request(document, declared),
            "responses": {
                status: _shape(document, _response_schema(document, response))
                for status, response in declared["responses"].items()
            },
        }
        for operation, declared in _declared_operations(document).items()
    }


def _declared_operations(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        f"{method.upper()} {path}": operation
        for path, item in document["paths"].items()
        if path not in FRAMEWORK_ROUTES
        for method, operation in item.items()
    }


def _accepts(document: Mapping[str, Any], parameter: Mapping[str, Any]) -> dict[str, Any]:
    """What a parameter accepts, as far as the document declares it: type, format, bounds."""
    schema = _resolved(document, parameter["schema"])
    return {
        keyword: schema[keyword]
        for keyword in ("type", "format", "minLength", "maxLength", "pattern")
        if keyword in schema
    }


def _request(document: Mapping[str, Any], operation: Mapping[str, Any]) -> Any:
    if "requestBody" not in operation:
        return None
    body: Mapping[str, Any] = operation["requestBody"]
    return {
        "required": bool(body.get("required", False)),
        "shape": _shape(document, _json_schema(body)),
    }


def _response_schema(document: Mapping[str, Any], response: Mapping[str, Any]) -> Any:
    return _json_schema(_resolved(document, response))


def _json_schema(node: Mapping[str, Any]) -> Any:
    return node["content"]["application/json"]["schema"]


def _shape(document: Mapping[str, Any], node: Any) -> Any:
    """One schema as the fields it declares, however it is referenced or nested."""
    resolved = _resolved(document, node)
    if not isinstance(resolved, dict):
        return {"kind": "unnamed"}
    if "properties" in resolved:
        return {
            "fields": sorted(resolved["properties"]),
            "required": sorted(resolved.get("required", [])),
        }
    if "items" in resolved:
        return {"items": _shape(document, resolved["items"])}
    if "enum" in resolved:
        return {"enum": sorted(resolved["enum"])}
    return {"kind": resolved.get("type", "unnamed")}


def _resolved(document: Mapping[str, Any], node: Any) -> Any:
    """Follow one reference — inline or into `components` — to the schema it names."""
    while isinstance(node, dict) and "$ref" in node:
        node = _pointer(document, node["$ref"])
    return node


def _pointer(document: Mapping[str, Any], reference: str) -> Any:
    assert reference.startswith("#/"), reference
    target: Any = document
    for step in reference[2:].split("/"):
        target = target[step]
    return target
