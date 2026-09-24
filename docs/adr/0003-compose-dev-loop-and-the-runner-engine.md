# The development stack runs on Podman Compose, and CI runs the same file on the runner's Docker

**Status:** accepted · 2026-09-24

`compose.yaml` is the inner development loop at every increment: `podman compose up --detach --wait`
brings the stack up and returns only once every health check has passed, and the same file starts the
same stack in CI. The image is built by Podman in both places; the artefact CI scans is the one
`podman build --squash-all` produces, because flattening is what makes the build's deletions real,
and `compose.yaml` can only build it unsquashed, which is why the quickstart names the explicit build.
What differs between the two environments is the container engine that runs the stack: Podman
locally, the Docker daemon the hosted runner already has in the container job, because rootless Podman
in a hosted runner is not a foundation to gate a job on.

The image reference is `localhost/claim-triage-agent:dev` in Compose, in CI and in the README,
because a bare name means different things to the two engines — `docker.io/library/claim-triage-agent`
to Docker, `localhost/claim-triage-agent` to Podman — and the image loaded into the runner's daemon
would then not be the image the stack asks for.

## Considered Options

- **Rootless Podman in the container job too**, so one engine serves both. Rejected: rootless Podman in
  a hosted runner depends on user-namespace and cgroup delegation that the runner image does not
  guarantee, so the job would fail for reasons unrelated to the change under test, and a job that can
  only fail in CI teaches nothing.
- **Rootful Podman for the whole container job.** Rejected for the stack: the runner's Docker Compose
  is already installed and well exercised, and it reads the same file. The image is still built by
  rootful Podman, so the artefact under test is the artefact a local build produces.
- **Docker Compose locally as well.** Rejected: Podman is the local target this project is developed
  against — no daemon, rootless by default — and the development loop is the part a reader can
  actually run. The shared artefact is the compose file, not the engine.
- **Kubernetes as the inner loop.** Rejected already in the specification: a cluster takes tens of
  seconds per restart, which breaks the test-first loop. Kubernetes remains the deployment target from
  v0.5.

## Consequences

The `container` CI job is the only place that depends on the runner's Docker, and a change that breaks
Podman's build is caught before it, in the same job. Anyone reproducing CI locally substitutes
`podman compose` for `docker compose` and gets the same stack from the same file, which is what the
quickstart documents.
