# ADR-0020: PyPI packaging and distribution

Status: Proposed

## Context

ADR-0018 reframed Phase 3 as a library plus git, no SaaS: the product is a
pip-installable Python package plus a git convention. ADR-0019 fixed the
body-vs-brain split, named the local Claude Code skills as the free local
brain surface, and named the `live` extra as the paid CI brain, deferring
the packaging mechanics ("how the skills ship", "schema as package data")
explicitly to this ADR. This ADR owns the second Phase 3 item: how Praxis
is packaged and published so that the body installs clean, the extras stay
optional, the schema rides the wheel, and the skills reach the user's
project.

The current `pyproject.toml` is not greenfield. It already declares
`name = "praxis"`, `version = "0.0.1"`, the CLI entry point
`praxis = "praxis.cli:main"`, a src layout under `src/praxis`, the
hatchling build backend, and two optional extras `browser-use` and `live`
(anthropic). ADR-0003 already made the adapters optional extras to keep the
core runtime-agnostic, and the `live` extra already isolates the Anthropic
SDK so the core and tests run without it. This ADR refines that existing
file rather than inventing a new one; the actual `pyproject.toml` edits are
Phase 3 implementation in a later task, recorded here as a decision.

One distribution fact forces a name change. The PyPI distribution name
`praxis` is TAKEN (the JSON API returns HTTP 200 for it); `praxis-qa` is
free (HTTP 404). The import name `praxis` and the CLI command `praxis` do
not collide with anything and can stay; only the published distribution
name must move. Pursuing the literal `praxis` distribution name would
require a PyPI naming dispute outside this repo and is out of scope.

The package is pure Python. There is no compiled extension, no
platform-specific binary, and no C dependency in the core, so it builds as
ONE universal wheel rather than per-platform wheels. The handoff assumed
per-platform builds; that assumption is corrected here.

## Decision

### 1. Distribution name `praxis-qa`; import name and CLI command stay `praxis`.

The package is published to PyPI under the distribution name `praxis-qa`
(the `[project] name`), because `praxis` is taken. The import name stays
`praxis` (the user writes `import praxis` and `from praxis import ...`) and
the CLI command stays `praxis` (the `[project.scripts]` entry point
`praxis = "praxis.cli:main"` is unchanged). Users install with
`pip install praxis-qa` and then use `praxis` everywhere else. The
distribution name is the only identifier that changes; nothing in the
import path, the CLI, the schema, or the adapter SPI is renamed.

### 2. One pure-Python universal wheel.

Praxis builds and publishes a single universal wheel
(`py3-none-any`-style), not per-platform wheels. The core is pure Python
with no compiled extension and no platform-specific binary, so there is no
reason to fan out across operating systems or CPython ABIs. A source
distribution (sdist) ships alongside the universal wheel as the standard
fallback. Hatchling stays the build backend and the src layout stays; the
build target packages `src/praxis` as today.

### 3. Optional extras keep the core install runtime-agnostic AND brain-agnostic.

The base install (`pip install praxis-qa`) pulls only the two core
dependencies, pydantic and pyyaml. It carries no browser runtime and no LLM
SDK. The core stays runtime-agnostic per ADR-0003 (browser runtimes live
behind the adapter SPI as extras such as `browser-use`, and future
Stagehand or Playwright adapters are added the same way) and brain-agnostic
per ADR-0019 (no brain SDK is a base dependency). Anything that ties Praxis
to a particular browser or a particular brain installs through a named
optional extra, never into the base install. A user who only reads, writes,
and projects knowledge installs nothing beyond pydantic and pyyaml.

### 4. The `live` extra carries the API-key agent for the CI brain.

The `live` optional extra (Anthropic SDK) is the install path for the CI
brain that ADR-0019 named: the API-key agent that runs the agentic
operations in CI where there is no human subscription session to borrow.
The `live` extra is the only extra that pulls an LLM SDK, it is optional,
and it is NOT required for the local Claude Code skill path, which runs on
the user's subscription with no API key and therefore needs no SDK in the
install. Installing the CI brain is `pip install "praxis-qa[live]"`;
installing the body for local skill-driven use needs neither `live` nor any
brain SDK.

### 5. Versioning: pre-1.0 schema-may-break window, then `schema_version` stability.

Praxis follows semantic versioning. While the package is pre-1.0 (the `0.x`
series, starting from the current `0.0.1`), the knowledge schema MAY change
in breaking ways between minor versions; the schema is not yet frozen and a
`0.x` bump may require migrating `.praxis/` knowledge files. From `1.0.0`
onward the published `schema_version` is the stability contract: a
backward-incompatible schema change requires a new `schema_version` and a
major package version bump, so a consumer pinned to a major version can rely
on the schema shape. This makes the schema-stability promise explicit and
version-anchored rather than implicit, and it gives the Phase 3 reframe room
to iterate the on-disk shape before `1.0` without breaking a stability
promise it never made.

### 6. The JSON schema ships as package data inside the wheel.

`schema/knowledge.schema.json` (and the active examples needed to validate
against it) ship as package data inside the wheel, so an installed Praxis
validates knowledge against the SAME schema the source tree tests against,
with no network fetch and no separate download. The shipped schema is the
single source of truth at runtime exactly as it is in the repo: the
pydantic model mirrors it and the model-vs-schema agreement test
(per ADR-0002 and the AGENTS.md convention) keeps them from drifting. The
schema is data the package carries, not a file the user must supply.

### 7. The Claude Code skills ship with the package; `praxis init` scaffolds them.

The Praxis Claude Code skills (the local brain surface from ADR-0019:
`/praxis:teach`, `/praxis:regress`, `/praxis:explore`, and the deterministic
helpers) ship inside the wheel as package data. The `praxis init` command
scaffolds them into the consuming project's `.claude/skills/` directory,
alongside scaffolding the `.praxis/` tree (the tree layout is owned by
ADR-0021). This is how a `pip install praxis-qa` followed by `praxis init`
gives a project both the body and the local brain without any manual skill
copying. Shipping Claude Code skills via a pip wheel and installing them
with `praxis init` is novel for a Python package; the mechanism is fixed
here and the implementation task proves it.

### 8. The public API surface is the stable contract.

Three surfaces are the stable public contract of `praxis-qa`, the things a
consumer may depend on across compatible versions:

- **The adapter SPI**: the two-method boundary (`read_knowledge`,
  `write_observations`) from ADR-0003. It stays tiny and stable; new
  adapters are additive extras and do not widen it.
- **The pydantic knowledge model**: the typed model that mirrors the JSON
  schema. Its shape is governed by the `schema_version` stability rule in
  decision 5.
- **The CLI**: the `praxis` command and its bare subcommands
  (`init / regress / explore / review / status`, per ADR-0019's
  operation set), including exit-code behavior for the agentic console
  surface. The teach operation is skill-only per ADR-0019 (delivered as
  `/praxis:teach`, never a bare `praxis teach` command), so it is not a
  CLI subcommand and is part of the skill contract, not the CLI contract.

Everything else (internal store mechanics, projection internals, the
specific brain wiring) is private and may change without a major bump.

### Forbidden alternatives

DO NOT, in the packaging or any Phase 3 implementation:

- Pull a browser runtime or an LLM SDK into the base install. Browser
  runtimes are extras behind the adapter SPI (ADR-0003); the only brain SDK
  is the optional `live` extra (ADR-0019). The base install carries only
  pydantic and pyyaml.
- Build platform-specific wheels. The package is pure Python and ships one
  universal wheel plus an sdist; there is no compiled extension to justify
  per-platform builds.
- Add any dependency beyond pydantic / pyyaml and the declared extras
  without a new ADR. The AGENTS.md "ask before adding any dependency" rule
  binds; a new runtime or a new brain is a new named extra recorded by an
  ADR, never a silent base dependency.
- Rename the import name or the CLI command. Only the published
  distribution name moves to `praxis-qa`; `import praxis` and the `praxis`
  command are unchanged.
- Fetch the schema over the network at runtime or require the user to supply
  it. The schema ships as package data inside the wheel.

## Consequences

Positive:

- A base `pip install praxis-qa` is small and dependency-light (pydantic +
  pyyaml only), which keeps the core install runtime-agnostic and
  brain-agnostic and keeps the adoption barrier low, the posture ADR-0018
  bet on.
- One universal wheel means one artifact to build, test, and publish across
  every platform, with no per-platform CI matrix for the package itself.
- Shipping the schema as package data means an installed Praxis validates
  against the exact schema the repo tests against, so schema drift between
  "what the package validates" and "what the model expects" is caught by
  the same agreement test that guards the repo.
- Anchoring schema stability to `schema_version` and semver gives consumers
  a clear contract (free to break pre-1.0, stable per major version after)
  and gives the Phase 3 reframe room to iterate the on-disk shape before
  `1.0`.
- Shipping the skills in the wheel and scaffolding them with `praxis init`
  means a user gets the local free brain with two commands and no manual
  skill management.

Negative:

- The distribution name `praxis-qa` differs from the import name `praxis`,
  a small but real source of confusion (`pip install praxis-qa`, then
  `import praxis`). The alternative, disputing the taken `praxis` name on
  PyPI, is worse and out of scope.
- Skill distribution via a pip wheel is novel and untested for a Python
  package; the mechanism is recorded here but the implementation task
  carries the risk of proving that `praxis init` reliably scaffolds skills
  into `.claude/skills/`.
- Declaring the public API surface (SPI, model, CLI) as the stable contract
  constrains future refactors: changing any of the three is a major-version
  event, which is intentional but reduces freedom to reshape them quietly.

Invariants respected:

- `runtime-agnostic-core`: the base install carries no browser runtime;
  every runtime stays an optional extra behind the ADR-0003 adapter SPI, so
  the packaged core imports and tests with no browser present.
- `adapter-spi-tiny-and-stable`: the two-method SPI (`read_knowledge`,
  `write_observations`) is named as part of the public contract and is not
  widened by packaging; new adapters are additive extras, not SPI changes.
- `schema-is-single-source-of-truth`: the JSON schema ships as package data
  and remains the single source of shape at runtime; the pydantic model
  mirrors it and the agreement test (ADR-0002 convention) guards drift in
  the installed package as in the repo.

Invariants this ADR does NOT cover:

- The `.praxis/` directory convention, the committed-vs-gitignored split,
  and where `praxis init` writes the knowledge tree on disk: owned by
  ADR-0021. This ADR fixes that the skills and the schema ship in the wheel
  and that `praxis init` scaffolds the skills; ADR-0021 owns the on-disk
  layout that `praxis init` also creates.
- `brain-agnostic` as a behavioral split (which operation needs a brain,
  which surface each runs on): owned by ADR-0019. This ADR only carries the
  packaging consequence (the `live` extra is the CI brain install; the base
  install pulls no brain SDK).
- The per-operation behavior of teach, regress, and explore: owned by
  ADR-0022 (teach) and ADR-0023 (regress / explore). This ADR fixes how the
  commands ship, not what they do.

## Relation to prior ADRs

Refines ADR-0003 (runtime-specific code behind an adapter SPI, Accepted):
ADR-0003 made the adapters optional install extras to keep the core
runtime-agnostic; this ADR makes that the explicit packaging rule (base
install = pydantic + pyyaml only, every runtime an extra) and adds the
brain-agnostic packaging consequence (the only brain SDK is the optional
`live` extra). The two-method SPI is unchanged and is named here as part of
the stable public contract.

Depends on ADR-0002 (the knowledge schema is the neutral interop layer,
Accepted) for the schema-as-package-data decision: the schema is the neutral
interop layer, so it ships inside the wheel as the single runtime source of
shape rather than being supplied per consumer.

Depends on ADR-0019 (brain pluggability and execution surfaces, Proposed)
for the brain split this ADR packages: the `live` extra carries the CI brain
ADR-0019 named, the local Claude Code skills are the free local brain
surface, and this ADR fixes how both ship (the `live` extra as an optional
dependency, the skills as package data scaffolded by `praxis init`).

Sits under ADR-0018 (Phase 3 scope and the library-plus-git reframe,
Proposed) as the second of its seven owned items. Does not supersede any
prior ADR; ADR-0001 through ADR-0019 stay binding into Phase 3.
