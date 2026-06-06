"""A deterministic, in-process model of the System Under Test.

WHY THIS EXISTS (read this before trusting any number it produces):
A *live* existential gate needs Browser Use + an LLM + a real SUT. None of those
run in CI or this sandbox. `simapp` is a stand-in that lets the harness, metrics,
oracle integration, mutation flow, and the kill/continue logic execute end-to-end
and produce a coherent verdict. It validates the MACHINERY, not the thesis.

The token magnitudes below are EXPLICIT ASSUMPTIONS that encode the thesis premise
(recognition via a remembered oracle is cheaper than re-deriving it cold). They are
NOT measurements. Replace this module with the Browser Use adapter wired to a real
app to get empirical numbers (see harness.py `LiveRuntime` stub).

What is modeled faithfully (this part is real):
  - the believed-knowledge oracle (seeded, diversity rule) drives "did it succeed".
  - a recorded script binds to brittle (label, position) coordinates and BREAKS on
    each mutation; a knowledge-driven agent matches by invariant purpose/role and
    goal-seeks, so it recovers. That asymmetry is the experiment's whole subject.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

try:
    from . import mutate
except ImportError:  # run as a plain script (python experiments/ui-mutation/simapp.py)
    import mutate  # type: ignore[no-redef]

# --- Token-cost ASSUMPTIONS (placeholders, not measurements) -----------------
NAV_TOKENS_PER_STAGE = 400        # base agent capability: navigate one screen
COLD_ORACLE_DERIVE_PER_STAGE = 600  # cold agent must infer "what does success look like"
COLD_EXPLORE_PENALTY = 500        # cold agent wastes effort with no recognition signals
MEMORY_READ_TOKENS = 200          # one-time cost to read believed knowledge


@dataclass(frozen=True)
class Control:
    purpose: str   # INVARIANT semantic role, e.g. "credential.identifier" (survives redesign)
    role: str      # "input" | "submit"
    label: str     # display text (coordinate-like; mutations change this)
    position: int  # order on screen (coordinate-like; mutations change this)


@dataclass
class Stage:
    name: str
    inputs: list[Control]
    submit: Control
    endpoint: str  # network signal that fires when the stage completes


@dataclass(frozen=True)
class Sig:
    type: str
    value: str


@dataclass
class Flow:
    goal_id: str
    goal: str
    app: str
    stages: list[Stage]
    success_signals: list[Sig]
    failure_signals: list[Sig]


# --------------------------------------------------------------------------- flows

def _login() -> Flow:
    return Flow(
        goal_id="authenticate-user",
        goal="A returning user can establish an authenticated session.",
        app="acme-web",
        stages=[
            Stage(
                name="credentials",
                inputs=[
                    Control("credential.identifier", "input", "Email", 0),
                    Control("credential.secret", "input", "Password", 1),
                ],
                submit=Control("submit.login", "submit", "Sign in", 2),
                endpoint="POST /session",
            )
        ],
        success_signals=[
            Sig("behavioral", "a logout/sign-out action becomes available"),
            Sig("network", "POST to the session endpoint returns 2xx and sets a session cookie"),
        ],
        failure_signals=[
            Sig("text", "an inline error indicates invalid credentials and no session"),
        ],
    )


def _search() -> Flow:
    return Flow(
        goal_id="search-catalog",
        goal="A user can find catalog items matching a query.",
        app="acme-web",
        stages=[
            Stage(
                name="query",
                inputs=[Control("search.query", "input", "Search", 0)],
                submit=Control("submit.search", "submit", "Search", 1),
                endpoint="GET /search",
            )
        ],
        success_signals=[
            Sig("behavioral", "a non-empty list of results is displayed"),
            Sig("network", "GET /search returns 2xx with a results payload"),
        ],
        failure_signals=[Sig("text", "a 'no results found' message is shown")],
    )


def _checkout() -> Flow:
    return Flow(
        goal_id="checkout-cart",
        goal="A user with items in the cart can place an order.",
        app="acme-web",
        stages=[
            Stage(
                name="cart",
                inputs=[],
                submit=Control("submit.checkout", "submit", "Proceed to checkout", 0),
                endpoint="POST /cart/checkout",
            ),
            Stage(
                name="payment",
                inputs=[Control("payment.card", "input", "Card number", 0)],
                submit=Control("submit.pay", "submit", "Place order", 1),
                endpoint="POST /order",
            ),
        ],
        success_signals=[
            Sig("behavioral", "an order confirmation with an order number is shown"),
            Sig("network", "POST /order returns 2xx"),
        ],
        failure_signals=[Sig("text", "a 'payment declined' message is shown")],
    )


_BUILDERS = {"login": _login, "search": _search, "checkout": _checkout}


def base_flow(name: str) -> Flow:
    """The unmutated flow definition."""
    return _BUILDERS[name]()


# --------------------------------------------------------------------------- mutations

def _apply_one(flow: Flow, mutation: "mutate.Mutation") -> Flow:
    """Apply a single mutation to a flow copy (pure; no global state). Mutations
    change HOW (labels/positions/extra steps), never WHETHER the goal is reachable —
    every mutated flow still completes via invariant purposes."""
    flow = copy.deepcopy(flow)

    if mutation is mutate.Mutation.RENAME_CONTROL:
        # The final submit control gets renamed ("Sign in" -> "Log in", etc.).
        last = flow.stages[-1]
        last.submit = _relabel(last.submit, last.submit.label + " (renamed)")

    elif mutation is mutate.Mutation.SWAP_LABEL:
        # The identifier field's label changes; its PURPOSE is unchanged.
        for st in flow.stages:
            for i, c in enumerate(st.inputs):
                if c.purpose == "credential.identifier":
                    st.inputs[i] = _relabel(c, "Username")

    elif mutation is mutate.Mutation.MOVE_FIELD:
        # Reorder inputs of the first multi-input stage (positions change).
        for st in flow.stages:
            if len(st.inputs) >= 2:
                st.inputs.reverse()
                for i, c in enumerate(st.inputs):
                    st.inputs[i] = _reposition(c, i)
                break

    elif mutation is mutate.Mutation.INSERT_STEP:
        # An interstitial "Continue" screen before the terminal stage.
        interstitial = Stage(
            name="interstitial",
            inputs=[],
            submit=Control("interstitial.continue", "submit", "Continue", 0),
            endpoint="",
        )
        flow.stages.insert(len(flow.stages) - 1, interstitial)

    return flow


def _shape(flow: Flow) -> tuple:
    """Structural fingerprint of a flow's controls (labels + positions + stages)."""
    return tuple(
        (st.name,
         tuple((c.role, c.label, c.position) for c in st.inputs),
         (st.submit.role, st.submit.label, st.submit.position))
        for st in flow.stages
    )


def mutation_changes_flow(name: str, mutation: "mutate.Mutation") -> bool:
    """True iff this mutation actually perturbs this flow. Robustness is only
    measured where a mutation changes something — a mutation that no-ops on a flow
    is not a test of robustness for that flow (it would unfairly credit the recorded
    script with a 'recovery')."""
    base = base_flow(name)
    return _shape(base) != _shape(_apply_one(base, mutation))


def current_flow(name: str) -> Flow:
    """The flow as the app currently presents it, with all active mutations applied."""
    flow = base_flow(name)
    for mutation in mutate.Mutation:
        if mutation in mutate.active():
            flow = _apply_one(flow, mutation)
    return flow


def _relabel(c: Control, label: str) -> Control:
    return Control(c.purpose, c.role, label, c.position)


def _reposition(c: Control, position: int) -> Control:
    return Control(c.purpose, c.role, c.label, position)


# --------------------------------------------------------------------------- recorded baseline

@dataclass(frozen=True)
class RecordedCoordinate:
    """A brittle binding the recorded script captured once: it requires BOTH the
    exact label AND the exact position to still match — the coordinate the schema
    forbids as durable knowledge."""

    role: str
    label: str
    position: int


def record_baseline(name: str) -> list[RecordedCoordinate]:
    """Capture a recorded script against the UNMUTATED flow."""
    flow = base_flow(name)
    coords: list[RecordedCoordinate] = []
    for st in flow.stages:
        for c in st.inputs:
            coords.append(RecordedCoordinate(c.role, c.label, c.position))
        coords.append(RecordedCoordinate(st.submit.role, st.submit.label, st.submit.position))
    return coords


@dataclass
class Outcome:
    succeeded: bool                 # ground truth: did the run reach the goal?
    tokens: int
    observed: list[Sig] = field(default_factory=list)  # signals the run actually saw


def run_recorded(name: str) -> Outcome:
    """Replay the recorded coordinates against the CURRENT (possibly mutated) flow.
    Breaks loudly when any captured (label, position) no longer matches, or when an
    inserted step leaves the app short of the goal. No LLM → 0 tokens, but brittle."""
    coords = record_baseline(name)
    flow = current_flow(name)
    # Flatten the current flow's controls into the structure the script expects.
    present: set[tuple[str, str, int]] = set()
    for st in flow.stages:
        for c in st.inputs:
            present.add((c.role, c.label, c.position))
        present.add((flow_submit := (st.submit.role, st.submit.label, st.submit.position)))
        _ = flow_submit
    for rc in coords:
        if (rc.role, rc.label, rc.position) not in present:
            return Outcome(succeeded=False, tokens=0)  # selector broke
    # All recorded controls matched — but an inserted interstitial stage means the
    # recorded steps don't drive the app all the way to the terminal stage.
    if len(flow.stages) > _baseline_stage_count(name):
        return Outcome(succeeded=False, tokens=0)
    return Outcome(succeeded=True, tokens=0, observed=list(flow.success_signals))


def _baseline_stage_count(name: str) -> int:
    return len(base_flow(name).stages)


# --------------------------------------------------------------------------- agent arms

def run_memory(name: str, *, app_broken: bool = False) -> Outcome:
    """Knowledge-driven agent: navigates by INVARIANT purpose/role and goal-seeks,
    recognizing completion via the remembered oracle. Robust to every mutation
    because it never depends on a label or position. Cheap because it does not have
    to re-derive what success looks like (it remembers)."""
    flow = current_flow(name)
    tokens = MEMORY_READ_TOKENS + NAV_TOKENS_PER_STAGE * len(flow.stages)
    if app_broken:
        # The app regressed: terminal signals never appear. The agent observes
        # nothing satisfying the oracle → it must NOT claim success.
        return Outcome(succeeded=False, tokens=tokens, observed=[])
    return Outcome(succeeded=True, tokens=tokens, observed=list(flow.success_signals))


def run_cold(name: str, *, app_broken: bool = False) -> Outcome:
    """Cold agent: same base navigation capability, but with NO memory it must
    explore and re-derive the success oracle every run → strictly more tokens. Just
    as capable of reaching the goal (that is why cost, not reliability, is the
    existential question)."""
    flow = current_flow(name)
    tokens = (
        NAV_TOKENS_PER_STAGE * len(flow.stages)
        + COLD_ORACLE_DERIVE_PER_STAGE * len(flow.stages)
        + COLD_EXPLORE_PENALTY
    )
    if app_broken:
        return Outcome(succeeded=False, tokens=tokens, observed=[])
    return Outcome(succeeded=True, tokens=tokens, observed=list(flow.success_signals))
