"""Generate the contract package — the wire models and the typed client — from the contract.

`contracts/core-sim.openapi.yaml` is the single source of truth for the surrounding insurance
systems. This module reads it and writes `claim_triage.contract`: one Python class per object
and per named enum in the document, and one client method per operation, named after the
operation's own `operationId`. The package is committed, and the `contract` CI job regenerates it
and fails when what is committed differs from what this produces, so the client cannot drift.

The document is written by hand, so this generator reads a deliberately small subset of OpenAPI and
refuses the rest by name rather than quietly ignoring it:

* `paths` operations over `get`/`post`/`put`/`patch`/`delete`, each with an `operationId`;
* parameters `in: path` and `in: header`, declared inline or through `components.parameters`
  — a parameter's type becomes the generated method's annotation, while its bounds are the
  service's to enforce, and the contract tests compare them on both sides;
* a `requestBody` of `application/json` holding one named schema;
* responses keyed by status, declared inline or through `components.responses`, carrying one
  `application/json` schema — a named schema, or an array of one;
* schemas that are closed objects (`additionalProperties: false`), named string enums, arrays of a
  named schema, or primitives (`string` with `format` `uuid`/`date`/`date-time`/`decimal`, plain
  `string`, `integer`);
* the keywords those need — `description`, `required`, `properties`, `items`, `enum`, `pattern`,
  `minimum`, `exclusiveMinimum`, `minLength`, `maxLength`, `minItems`, `maxItems`, `x-max-digits`,
  `x-decimal-places` and `x-enum-descriptions`.

Anything else raises `UnsupportedContract`, naming the document path that carried it.

Two conventions cross the language boundary, and the document declares both: a *named* enum becomes
a `StrEnum` the service and the client can both name, while an enum written inline in a field
becomes a `Literal`; and a *named* primitive (the contract's `EurAmount`) generates no class of its
own, because its constraints belong at each site that holds one — so a field referencing it is
annotated with the type and constrained by the keywords that schema carries.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

CONTRACT_PATH: Final = Path(__file__).parents[2] / "contracts" / "core-sim.openapi.yaml"
"""The contract this generator reads: beside the repository, not inside the installed package."""

PACKAGE_PATH: Final = Path(__file__).parent / "contract"
"""The contract package this generator writes."""

GENERATED_FILES: Final = ("__init__.py", "models.py", "client.py")
"""Every file the package is made of, so the drift check regenerates all three or none."""

HTTP_METHODS: Final = ("get", "post", "put", "patch", "delete")

SCHEMA_PREFIX: Final = "#/components/schemas/"
PARAMETER_PREFIX: Final = "#/components/parameters/"
RESPONSE_PREFIX: Final = "#/components/responses/"

FORMATS: Final = {
    "uuid": "UUID",
    "date": "date",
    "date-time": "datetime",
    "decimal": "Decimal",
}
"""The string formats the contract may use, and the type each one is held as."""

SUPPORTED_KEYWORDS: Final = frozenset(
    {
        "$ref",
        "type",
        "format",
        "enum",
        "description",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "pattern",
        "minimum",
        "exclusiveMinimum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "x-max-digits",
        "x-decimal-places",
        "x-enum-descriptions",
    }
)
"""The keywords the generator reads, so a contract that grows one it cannot read fails the build."""

WIDTH: Final = 92
"""How wide a generated docstring or comment line may be before it wraps."""

MODELS_HEADER: Final = '''"""The wire models of the surrounding systems' contract.

Generated from `contracts/core-sim.openapi.yaml` by `uv run poe generate`. Do not edit: the
`contract` CI job regenerates this package and fails when it differs from what is committed.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
'''

CLIENT_ERRORS_BASE: Final = '''

DEFAULT_TIMEOUT_SECONDS: Final = 5.0
"""How long one call may take before the surrounding systems count as unreachable."""


class CoreSimError(Exception):
    """What the surrounding systems did instead of answering the contract."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CoreSimUnreachable(CoreSimError):
    """The surrounding systems could not be reached at all."""


class UnexpectedResponse(CoreSimError):
    """The systems answered with something the contract does not allow."""


class Refusal(CoreSimError):
    """An error the contract documents: the systems refused the call and named why.

    It narrows `status_code` to an int: a refusal is built from a response, so it always carries the
    status the systems answered, and a caller relaying one never has to ask whether it has a status.
    """

    status_code: int

    def __init__(self, status_code: int, response: ErrorResponse) -> None:
        super().__init__(f"{response.code}: {response.detail}", status_code=status_code)
        self.response = response

    @property
    def code(self) -> ErrorCode:
        """What the systems refused the call for."""
        return self.response.code

    @property
    def detail(self) -> str:
        """What exactly was wrong, as the systems named it."""
        return self.response.detail
'''

CODES_OPEN: Final = """
_CODES: Final[dict[ErrorCode, type[Refusal]]] = {
"""

CLIENT_HELPERS: Final = '''}


def _document(response: httpx2.Response) -> object:
    """The answer as JSON, or the failure that says the systems answered something else."""
    try:
        document: object = response.json()
    except ValueError as not_json:
        raise UnexpectedResponse(
            f"{response.request.method} {response.request.url} answered non-JSON",
            status_code=response.status_code,
        ) from not_json
    return document


def _refusal(response: httpx2.Response) -> NoReturn:
    """Raise the typed error the contract names for what the systems answered."""
    try:
        error = ErrorResponse.model_validate(_document(response))
    except ValidationError as undocumented:
        raise UnexpectedResponse(
            f"{response.request.url} answered {response.status_code} outside the contract",
            status_code=response.status_code,
        ) from undocumented
    # The table and the enum are generated from the same codes, so every answer has an exception
    # waiting for it: there is no "code the contract does not name" to fall back on.
    raise _CODES[error.code](response.status_code, error)
'''

CLIENT_CLASS: Final = '''
class CoreSimClient:
    """The surrounding systems, as the contract describes them.

    One method per operation, one typed exception per error code the contract documents. Pass a
    `transport` to drive it without a socket (the unit tests do); otherwise it speaks HTTP.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Point the client at one running set of surrounding systems."""
        self._http = httpx2.Client(base_url=base_url, timeout=timeout, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the connections the client holds."""
        self._http.close()

    def _request[T](
        self,
        method: str,
        path: str,
        *,
        parse: Callable[[object], T],
        body: object | None = None,
        headers: Mapping[str, str] | None = None,
        expected: int = 200,
    ) -> T:
        """One call: the answer parsed as the contract describes it, or the error it names."""
        try:
            response = self._http.request(method, path, json=body, headers=headers)
        except httpx2.TransportError as unreachable:
            raise CoreSimUnreachable(f"{method} {path} unreachable: {unreachable}") from None
        if response.status_code != expected:
            _refusal(response)
        document: object = _document(response)
        try:
            return parse(document)
        except ValidationError as outside_the_contract:
            raise UnexpectedResponse(
                f"{method} {path} answered a body the contract does not cover",
                status_code=response.status_code,
            ) from outside_the_contract
'''


@dataclass(frozen=True, slots=True)
class Parameter:
    """One parameter of one operation, as the generated method declares it."""

    name: str
    location: str
    annotation: str
    required: bool
    header: str
    """The wire name, for a header parameter whose Python name cannot be the wire name."""


@dataclass(frozen=True, slots=True)
class Returns:
    """What one operation answers with: the annotation, how it is parsed, what it needs imported."""

    annotation: str
    parse: str
    imports: tuple[str, ...]
    adapter: str | None = None


@dataclass(frozen=True, slots=True)
class Operation:
    """One operation of the contract, as the generated client method needs it."""

    operation_id: str
    method: str
    path: str
    summary: str
    description: str | None
    path_parameters: tuple[Parameter, ...]
    header_parameters: tuple[Parameter, ...]
    body: str | None
    body_argument: str | None
    success: int
    returns: Returns


class UnsupportedContract(Exception):
    """A construct outside the subset the generator reads, named by where the document had it."""


def main() -> int:
    """Write the contract package from the contract, and report what was written."""
    generate(CONTRACT_PATH, PACKAGE_PATH)
    print(f"generated {PACKAGE_PATH} from {CONTRACT_PATH.name}: {', '.join(GENERATED_FILES)}")
    return 0


def generate(contract_path: Path, package_path: Path) -> None:
    """Write every file of the contract package, formatted as the rest of the repository is."""
    document = _document(contract_path)
    sources = {
        "__init__.py": _package_source(),
        "models.py": _models_source(document),
        "client.py": _client_source(document),
    }
    package_path.mkdir(parents=True, exist_ok=True)
    for name in GENERATED_FILES:
        (package_path / name).write_text(sources[name], encoding="utf-8")
    _pass_over(package_path, "check", "--fix")
    _pass_over(package_path, "format")


def _document(contract_path: Path) -> dict[str, Any]:
    """The contract as data, checked for the shape it needs before anything is written."""
    loaded: object = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise UnsupportedContract(f"{contract_path} does not hold an OpenAPI document")
    document: dict[str, Any] = loaded
    for key in ("openapi", "info", "paths", "components"):
        if key not in document:
            raise UnsupportedContract(f"{contract_path} declares no {key}")
    if "schemas" not in document["components"]:
        raise UnsupportedContract(f"{contract_path} declares no components.schemas")
    return document


def _pass_over(package_path: Path, *command: str) -> None:
    """Run one ruff pass over the generated package, as the repository gates its own code."""
    passed = subprocess.run(  # a fixed argv of the toolchain this repository pins
        [sys.executable, "-m", "ruff", *command, str(package_path)],
        capture_output=True,
        check=False,
        text=True,
    )
    if passed.returncode != 0:
        refused = f"ruff {' '.join(command)} refused the generated package:\n{passed.stdout}"
        raise UnsupportedContract(refused)


def _package_source() -> str:
    return '''"""The surrounding systems' contract: its wire models, and the client that speaks it.

Generated from `contracts/core-sim.openapi.yaml` — see `claim_triage.codegen`.
"""
'''


def _models_source(document: Mapping[str, Any]) -> str:
    schemas: Mapping[str, Any] = document["components"]["schemas"]
    blocks = [_schema_block(name, schema, schemas) for name, schema in schemas.items()]
    return MODELS_HEADER + "\n\n" + "\n\n".join(blocks) + "\n"


def _schema_block(name: str, schema: Mapping[str, Any], schemas: Mapping[str, Any]) -> str:
    """One class for the schema — or nothing at all, for a primitive its uses inline."""
    if _is_enum(schema):
        return _enum_class(name, schema)
    if _is_object(schema):
        return _model_class(name, schema, schemas)
    _check_supported(schema, name)
    if _is_class(schema):
        raise UnsupportedContract(f"{name}: a named schema must be an object or a string enum")
    return ""


def _enum_class(name: str, schema: Mapping[str, Any]) -> str:
    """A named string enum, as the `StrEnum` the service and the client both name."""
    _check_supported(schema, name)
    if schema.get("type") != "string":
        raise UnsupportedContract(f"{name}: only string enums are supported")
    described: Mapping[str, str] = schema.get("x-enum-descriptions", {})
    lines = [f"class {name}(StrEnum):", *_text(schema.get("description"), indent="    ")]
    for value in schema["enum"]:
        lines.extend(_comments(described.get(value), indent="    "))
        lines.append(f'    {_member_name(value, where=name)} = "{value}"')
    return "\n".join(lines)


def _model_class(name: str, schema: Mapping[str, Any], schemas: Mapping[str, Any]) -> str:
    """A closed object schema: a model that refuses every field the contract does not declare."""
    _check_supported(schema, name)
    if schema.get("additionalProperties") is not False:
        raise UnsupportedContract(f"{name}: every object schema sets additionalProperties: false")
    required = set(schema.get("required", []))
    properties: Mapping[str, Any] = schema.get("properties", {})
    if not properties:
        raise UnsupportedContract(f"{name}: an object schema with no properties says nothing")
    lines = [
        f"class {name}(BaseModel):",
        *_text(schema.get("description"), indent="    "),
        '    model_config = ConfigDict(extra="forbid")',
        "",
    ]
    lines.extend(
        line
        for field, field_schema in properties.items()
        for line in _field(
            field, field_schema, schemas, required=field in required, where=f"{name}.{field}"
        )
    )
    return "\n".join(lines)


def _field(
    name: str,
    schema: Mapping[str, Any],
    schemas: Mapping[str, Any],
    *,
    required: bool,
    where: str,
) -> list[str]:
    """One model field: what it means, then its annotation and the contract's constraints on it."""
    annotation, constraints = _annotation(schema, schemas, where=where)
    if not required:
        annotation = f"{annotation} | None"
    arguments = ["default=None", *constraints] if not required else list(constraints)
    described = schema.get("description") or _referenced_description(schema, schemas)
    fields = [*_comments(described, indent="    ")]
    if arguments:
        fields.append(f"    {name}: {annotation} = Field({', '.join(arguments)})")
    else:
        fields.append(f"    {name}: {annotation}")
    return fields


def _annotation(
    schema: Mapping[str, Any], schemas: Mapping[str, Any], *, where: str
) -> tuple[str, list[str]]:
    """The Python annotation for one schema, and the pydantic constraints it carries."""
    if "$ref" in schema:
        name = _reference_name(schema, SCHEMA_PREFIX, where=where)
        target: Mapping[str, Any] = schemas[name]
        if _is_class(target):
            return name, []
        return _annotation(target, schemas, where=name)
    if "enum" in schema:
        return _literal(schema["enum"], where=where), _constraints(schema, where=where)
    kind = schema.get("type")
    if kind == "array":
        items, item_constraints = _annotation(schema["items"], schemas, where=f"{where}[]")
        if item_constraints:
            raise UnsupportedContract(f"{where}: constraints on array items are not supported")
        return f"list[{items}]", _constraints(schema, where=where)
    if kind == "string":
        return _string_annotation(schema, where=where), _constraints(schema, where=where)
    if kind == "integer":
        return "int", _constraints(schema, where=where)
    raise UnsupportedContract(f"{where}: no support for a schema of type {kind!r}")


def _string_annotation(schema: Mapping[str, Any], *, where: str) -> str:
    format_ = schema.get("format")
    if format_ is None:
        return "str"
    if format_ not in FORMATS:
        raise UnsupportedContract(f"{where}: no support for the string format {format_!r}")
    return FORMATS[format_]


def _constraints(schema: Mapping[str, Any], *, where: str) -> list[str]:
    """The pydantic constraints one schema asks for, in the order the document declares them."""
    constraints: list[str] = []
    for keyword, argument in (
        ("exclusiveMinimum", "gt"),
        ("minimum", "ge"),
        ("minLength", "min_length"),
        ("maxLength", "max_length"),
        ("minItems", "min_length"),
        ("maxItems", "max_length"),
        ("x-max-digits", "max_digits"),
        ("x-decimal-places", "decimal_places"),
    ):
        if keyword in schema:
            constraints.append(f"{argument}={schema[keyword]}")
    if "pattern" in schema:
        constraints.append(f"pattern={schema['pattern']!r}")
    _check_supported(schema, where)
    return constraints


def _check_supported(schema: Mapping[str, Any], where: str) -> None:
    unsupported = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unsupported:
        raise UnsupportedContract(f"{where}: the generator does not read {', '.join(unsupported)}")


def _is_object(schema: Mapping[str, Any]) -> bool:
    return schema.get("type") == "object" or "properties" in schema


def _is_enum(schema: Mapping[str, Any]) -> bool:
    return "enum" in schema and not _is_object(schema)


def _is_class(schema: Mapping[str, Any]) -> bool:
    """Whether a reference to this schema names a type the generated code can refer to."""
    return _is_enum(schema) or _is_object(schema)


def _literal(values: Sequence[str], *, where: str) -> str:
    if not all(isinstance(value, str) for value in values):
        raise UnsupportedContract(f"{where}: only string enums are supported")
    return "Literal[" + ", ".join(f'"{value}"' for value in values) + "]"


def _member_name(value: str, *, where: str) -> str:
    member = re.sub(r"[^0-9a-zA-Z]+", "_", value).upper()
    if not member or member[0].isdigit():
        raise UnsupportedContract(f"{where}: {value!r} has no usable member name")
    return member


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _referenced_description(schema: Mapping[str, Any], schemas: Mapping[str, Any]) -> str | None:
    """A field that references a schema which documents itself carries that documentation."""
    if "$ref" not in schema:
        return None
    target = schemas[_reference_name(schema, SCHEMA_PREFIX, where=str(schema))]
    described = target.get("description")
    return described if isinstance(described, str) else None


def _reference_name(schema: Mapping[str, Any], prefix: str, *, where: str) -> str:
    reference = schema["$ref"]
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise UnsupportedContract(f"{where}: only references into {prefix} are supported")
    return reference[len(prefix) :]


def _client_source(document: Mapping[str, Any]) -> str:
    """The client module: the runtime every call shares, then one method per operation."""
    operations = _operations(document)
    adapters = {
        returns.adapter: returns
        for returns in (operation.returns for operation in operations)
        if returns.adapter
    }
    models = sorted({*_client_models(operations), "ErrorCode", "ErrorResponse"})
    header = [
        '"""The typed client of the surrounding systems\' contract.',
        "",
        "Generated from `contracts/core-sim.openapi.yaml` by `uv run poe generate`. Do not edit:",
        "the `contract` CI job regenerates this package and fails when it differs from what is",
        "committed.",
        "",
        "One method per operation, named after the operation's `operationId`, and one typed",
        "exception per error code the contract documents: a caller handles the contract, not HTTP.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Callable, Mapping",
        "from types import TracebackType",
        "from typing import Final, NoReturn, Self",
        *(("from urllib.parse import quote",) if _uses_quote(operations) else ()),
        *(("from uuid import UUID",) if _uses_uuid(operations) else ()),
        "",
        "import httpx2",
        f"from pydantic import {'TypeAdapter, ValidationError' if adapters else 'ValidationError'}",
        "",
        "from claim_triage.contract.models import (",
        *(f"    {name}," for name in models),
        ")",
    ]
    body = [
        CLIENT_ERRORS_BASE,
        _error_classes(document),
        CODES_OPEN,
        _code_entries(document),
        CLIENT_HELPERS,
    ]
    for adapter, returns in adapters.items():
        body.append(f"{adapter}: Final = TypeAdapter({returns.annotation})\n")
    body.append(CLIENT_CLASS.rstrip())
    body.extend(f"\n{_method_source(operation)}" for operation in operations)
    return "\n".join(header) + "\n".join(body) + "\n"


def _operations(document: Mapping[str, Any]) -> tuple[Operation, ...]:
    """Every operation of the contract, in the order the document declares them."""
    components: Mapping[str, Any] = document["components"]
    schemas: Mapping[str, Any] = components["schemas"]
    parameters: Mapping[str, Any] = components.get("parameters", {})
    responses: Mapping[str, Any] = components.get("responses", {})
    operations: list[Operation] = []
    for path, item in document["paths"].items():
        for method, declared in item.items():
            if method not in HTTP_METHODS:
                raise UnsupportedContract(f"{path}: no support for the {method} of an operation")
            operations.append(
                _operation(
                    declared,
                    method=method,
                    path=path,
                    schemas=schemas,
                    parameters=parameters,
                    responses=responses,
                    where=f"{method.upper()} {path}",
                )
            )
    return tuple(operations)


def _operation(
    declared: Mapping[str, Any],
    *,
    method: str,
    path: str,
    schemas: Mapping[str, Any],
    parameters: Mapping[str, Any],
    responses: Mapping[str, Any],
    where: str,
) -> Operation:
    operation_id = declared.get("operationId")
    if not operation_id:
        raise UnsupportedContract(f"{where}: every operation declares an operationId")
    path_parameters = tuple(
        parameter
        for parameter in (
            _parameter(node, parameters, schemas=schemas, where=where)
            for node in declared.get("parameters", [])
        )
        if parameter.location == "path"
    )
    header_parameters = tuple(
        parameter
        for parameter in (
            _parameter(node, parameters, schemas=schemas, where=where)
            for node in declared.get("parameters", [])
        )
        if parameter.location == "header"
    )
    declared_responses: Mapping[str, Any] = declared["responses"]
    success = _success_status(declared_responses, where=where)
    body_schema = _body_schema(declared, schemas=schemas, where=where)
    answer = _response_schema(
        _response(declared_responses[str(success)], responses, where=where),
        where=f"{where} {success}",
    )
    return Operation(
        operation_id=str(operation_id),
        method=method.upper(),
        path=path,
        summary=str(declared.get("summary", operation_id)),
        description=declared.get("description"),
        path_parameters=path_parameters,
        header_parameters=header_parameters,
        body=body_schema,
        body_argument=_snake(body_schema) if body_schema else None,
        success=success,
        returns=_returns(answer, schemas=schemas, where=f"{where} {success}"),
    )


def _parameter(
    node: Mapping[str, Any],
    parameters: Mapping[str, Any],
    *,
    schemas: Mapping[str, Any],
    where: str,
) -> Parameter:
    """One declared parameter, resolved and typed, wherever it sits and however it is declared."""
    if "$ref" in node:
        name = _reference_name(node, PARAMETER_PREFIX, where=where)
        if name not in parameters:
            raise UnsupportedContract(f"{where}: {name} names no component parameter")
        return _parameter(parameters[name], parameters, schemas=schemas, where=where)
    location = node["in"]
    if location not in ("path", "header"):
        raise UnsupportedContract(f"{where}: no support for a parameter in {location}")
    annotation, _ = _annotation(node["schema"], schemas, where=f"{where} {node['name']}")
    return Parameter(
        name=str(node["name"]),
        location=str(location),
        annotation=annotation,
        required=bool(node.get("required", False)),
        header=re.sub(r"[^0-9a-zA-Z]+", "_", str(node["name"])).strip("_").lower(),
    )


def _body_schema(
    declared: Mapping[str, Any], *, schemas: Mapping[str, Any], where: str
) -> str | None:
    """The schema an operation accepts, named, or nothing when the operation takes no body."""
    if "requestBody" not in declared:
        return None
    if not declared["requestBody"].get("required", False):
        raise UnsupportedContract(f"{where}: every declared request body is required")
    schema = declared["requestBody"]["content"]["application/json"]["schema"]
    name = _reference_name(schema, SCHEMA_PREFIX, where=f"{where} requestBody")
    if name not in schemas:
        raise UnsupportedContract(f"{where}: the request body names no schema")
    return name


def _response(
    declared: Mapping[str, Any], responses: Mapping[str, Any], *, where: str
) -> Mapping[str, Any]:
    if "$ref" in declared:
        name = _reference_name(declared, RESPONSE_PREFIX, where=where)
        if name not in responses:
            raise UnsupportedContract(f"{where}: {name} names no component response")
        resolved: Mapping[str, Any] = responses[name]
        return resolved
    return declared


def _success_status(declared_responses: Mapping[str, Any], *, where: str) -> int:
    successes = sorted(int(status) for status in declared_responses if status.startswith("2"))
    if len(successes) != 1:
        raise UnsupportedContract(f"{where}: an operation answers with exactly one success status")
    return successes[0]


def _response_schema(response: Mapping[str, Any], *, where: str) -> Mapping[str, Any]:
    if "content" not in response or "application/json" not in response["content"]:
        raise UnsupportedContract(f"{where}: every response carries an application/json schema")
    schema: Mapping[str, Any] = response["content"]["application/json"]["schema"]
    return schema


def _returns(schema: Mapping[str, Any], *, schemas: Mapping[str, Any], where: str) -> Returns:
    """What one operation answers with: how the client names it, and how it parses it."""
    if "$ref" in schema:
        name = _reference_name(schema, SCHEMA_PREFIX, where=where)
        if name not in schemas or not _is_class(schemas[name]):
            raise UnsupportedContract(f"{where}: the answer must be a named object or enum")
        return Returns(annotation=name, parse=f"{name}.model_validate", imports=(name,))
    if schema.get("type") == "array":
        items = schema["items"]
        name = _reference_name(items, SCHEMA_PREFIX, where=f"{where} items")
        adapter = f"_{_snake(name).upper()}"
        return Returns(
            annotation=f"list[{name}]",
            parse=f"{adapter}.validate_python",
            imports=(name,),
            adapter=adapter,
        )
    raise UnsupportedContract(f"{where}: the answer must be a named object or an array of one")


def _client_models(operations: Sequence[Operation]) -> Iterator[str]:
    """The contract models the generated signatures name, so the client imports nothing else."""
    for operation in operations:
        yield from operation.returns.imports
        if operation.body:
            yield operation.body


def _uses_uuid(operations: Sequence[Operation]) -> bool:
    return any(
        parameter.annotation == "UUID"
        for operation in operations
        for parameter in operation.path_parameters
    )


def _uses_quote(operations: Sequence[Operation]) -> bool:
    return any(operation.path_parameters for operation in operations)


def _error_classes(document: Mapping[str, Any]) -> str:
    """One typed exception per documented error code, documented as the contract documents it."""
    codes, described = _error_codes(document)
    lines: list[str] = []
    for value in codes:
        lines.append("")
        lines.append(f"class {_error_name(value)}(Refusal):")
        lines.extend(_text(described.get(value), indent="    "))
    return "\n".join(lines) + "\n"


def _code_entries(document: Mapping[str, Any]) -> str:
    """The map from a code the service names to the exception a caller handles it as."""
    codes, _ = _error_codes(document)
    return "\n".join(
        f"    ErrorCode.{_member_name(value, where=value)}: {_error_name(value)},"
        for value in codes
    )


def _error_codes(document: Mapping[str, Any]) -> tuple[list[str], Mapping[str, str]]:
    enum: Mapping[str, Any] = document["components"]["schemas"]["ErrorCode"]
    described: Mapping[str, str] = enum.get("x-enum-descriptions", {})
    return list(enum["enum"]), described


def _error_name(code: str) -> str:
    return "".join(part.capitalize() for part in code.split("_")) + "Error"


def _method_source(operation: Operation) -> str:
    """One client method: its signature, what the operation means, and the single call it makes."""
    lines = [f"    def {operation.operation_id}(", "        self,"]
    for parameter in operation.path_parameters:
        lines.append(f"        {parameter.header}: {parameter.annotation},")
    if operation.body_argument:
        lines.append(f"        {operation.body_argument}: {operation.body},")
    if operation.header_parameters:
        lines.append("        *,")
        for parameter in operation.header_parameters:
            lines.append(f"        {parameter.header}: {parameter.annotation},")
    lines.append(f"    ) -> {operation.returns.annotation}:")
    lines.extend(_text(_meaning(operation), indent="        "))
    lines.append("        return self._request(")
    lines.append(f'            "{operation.method}",')
    lines.append(f"            {_path_expression(operation)},")
    lines.append(f"            parse={operation.returns.parse},")
    if operation.body_argument:
        lines.append(f'            body={operation.body_argument}.model_dump(mode="json"),')
    for parameter in operation.header_parameters:
        lines.append(f'            headers={{"{parameter.name}": {parameter.header}}},')
    lines.append(f"            expected={operation.success},")
    lines.append("        )")
    return "\n".join(lines)


def _meaning(operation: Operation) -> str:
    if not operation.description:
        return operation.summary
    return f"{operation.summary}\n\n{operation.description}"


def _path_expression(operation: Operation) -> str:
    """The request path, quoted where a parameter stands in it so nothing can escape the route."""
    if not operation.path_parameters:
        return json.dumps(operation.path)
    rendered = re.sub(
        r"\{(\w+)\}",
        lambda match: "{quote(str(" + match.group(1) + "), safe='')}",
        operation.path,
    )
    return f'f"{rendered}"'


def _text(text: str | None, *, indent: str) -> list[str]:
    """One docstring, as the lines the generated code carries, or nothing when there is none.

    Paragraphs stay apart: what an operation is and what it does in detail are two statements, and
    running them together reads as neither.
    """
    if not text:
        return []
    paragraphs = [
        _wrapped(paragraph, width=WIDTH - len(indent) - len('"""'))
        for paragraph in text.split("\n\n")
        if paragraph.strip()
    ]
    if len(paragraphs) == 1 and len(paragraphs[0]) == 1:
        return [f'{indent}"""{paragraphs[0][0]}"""']
    lines = [f'{indent}"""{paragraphs[0][0]}', *(f"{indent}{line}" for line in paragraphs[0][1:])]
    for paragraph in paragraphs[1:]:
        lines.append("")
        lines.extend(f"{indent}{line}" for line in paragraph)
    lines.append(f'{indent}"""')
    return lines


def _comments(text: str | None, *, indent: str) -> list[str]:
    if not text:
        return []
    return [f"{indent}# {line}" for line in _wrapped(text, width=WIDTH - len(indent))]


def _wrapped(text: str, *, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
