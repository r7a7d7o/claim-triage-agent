# Python pinned to 3.13

**Status:** accepted · 2026-09-24

The project pins `requires-python = ">=3.13,<3.14"` and `.python-version` `3.13`, because 3.14
removes the guardrail frameworks the safety layer is evaluated against, while 3.13 still satisfies
every dependency the pipeline itself uses. Published `Requires-Python` as of 2026-09-24:

|Package|Version|Requires-Python|3.13 installs|
|---|---|---|---|
|`guardrails-ai`|0.11.0|`>=3.10,<3.14`|yes|
|`nemoguardrails`|0.24.1|`>=3.10,<3.14`|yes|
|`llm-guard`|0.3.16|`>=3.10,<3.13`|**no** — its cap excludes 3.13 too|
|`presidio-analyzer`|2.2.364|`>=3.10,<3.15`|yes|
|`rapidocr`|3.9.2|`>=3.8,<4`|yes|
|`langgraph`|1.2.12|`>=3.10`|yes|
|`langfuse`|4.15.4|`>=3.10,<4.0`|yes|
|`locust`|2.46.6|`>=3.11`|yes|
|`onnxruntime`|1.30.0|`>=3.11`|yes|

The guardrail-framework comparison (ticket 43) therefore runs `guardrails-ai` against hand-written
typed guards on this interpreter, and if `llm-guard` is included it runs in a 3.12 side environment
— a constraint of that package, not of this project.

## Considered Options

- **Unpinned / latest Python**, which today resolves to 3.14. Rejected: `guardrails-ai` and
  `nemoguardrails` become uninstallable, so the build-versus-buy comparison would have to be argued
  from documentation rather than measured.
- **3.12**, which every package in the table installs on, including `llm-guard`. Rejected: it buys
  nothing the pipeline needs, gives up the 3.13 language and standard-library improvements, and the
  only package it adds is one no ticket requires in the main environment.
- **3.14 with the guardrail frameworks dropped from the comparison.** Rejected: dropping the evidence
  is worse than pinning the interpreter.

## Consequences

The pin lives in `pyproject.toml` (`requires-python`) and `.python-version`, and CI installs the
pinned interpreter through `uv`. Raising it is a deliberate act: it needs `guardrails-ai` or
`nemoguardrails` to publish a 3.14-wheeled release, and it should be recorded here when it happens.
