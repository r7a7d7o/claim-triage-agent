# The application image: one Python distribution, one console script per deployable.
#
# Every service in `compose.yaml` runs from this image, so the developer's stack and the CI container
# jobs exercise the same artefact. A service declares its own health check in `compose.yaml`, because
# what "ready" means differs per deployable: each one that serves a surface answers on `/healthz`
# with what ready means for it, and a service that needs another waits on that check.
#
# Build the shipped artefact with `--squash-all`:
#
#     podman build --squash-all --tag localhost/claim-triage-agent:dev .
#
# Squashing is what makes the deletions below real. Files removed in a later layer are only hidden by
# a whiteout — the bytes stay in the layer underneath, which is what a scanner reads and what an
# attacker with the image can extract. Flattened to one layer, the image carries the runtime
# filesystem and nothing that was deleted on the way to it. Without the flag the build is still
# correct; only the packaging and the scan result differ.

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# The lock and the manifest change less often than the source, so the dependencies install in their
# own layer. `--locked` fails the build rather than resolving versions the lock does not pin.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-editable --no-install-project

COPY src/ ./src/
RUN uv sync --locked --no-dev --no-editable

# Nothing at runtime needs a package manager: uv installed the environment, and the deployables run
# from it. The installers go, along with the build's 34 MB download cache, and with it the base
# image's pip and its vendored msgpack and pkg_resources — the only HIGH findings Trivy reports for
# this image, unfixable from here because they live in `python:3.13-slim`'s own layer.
RUN rm -rf /root/.cache/uv /usr/local/lib/python3.13/ensurepip \
           /usr/local/lib/python3.13/site-packages/pip* /usr/local/bin/pip*

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin claim-triage
USER claim-triage
