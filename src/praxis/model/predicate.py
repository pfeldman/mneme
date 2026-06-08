"""Structured signal predicate: parse, validate, and evaluate (ADR-0030).

A signal's optional `value_predicate` is a template string that makes the
signal a CHECKABLE FACT instead of free-text prose. Everything OUTSIDE a
`{slot}` is the INVARIANT and is matched EXACTLY (after case-folding and
whitespace normalization, the same two normalizations the prose path already
implies); everything INSIDE a slot is a per-run instance token the run binds
at observation time and the matcher does NOT compare literally.

This module is the load-bearing guard against a FALSE PASS (ADR-0030 decision
3, AGENTS.md non-negotiable 5). The structured path is STRICTER than Jaccard,
never looser:

  - outside a slot the match is EXACT (a `returns 500` observation can never
    satisfy a `returns 2xx` invariant, where 0.5 Jaccard could have admitted
    it on coincidental word overlap),
  - inside a slot the matcher requires the slot be FILLED by a non-empty
    instance token (and, with a declared shape, that the token has that shape);
    an empty or shape-violating filler is a NON-match, never a free pass,
  - a predicate that is nothing but one slot, or carries no durable invariant
    token (stopwords only), is REJECTED at validation time so it can never
    match everything.

Pure stdlib, zero runtime/browser deps (ADR-0003, AGENTS.md non-negotiable 4):
the validator runs at the write boundary (the same posture as
`trigger_validator.validate_trigger`) and the evaluator runs in the matcher.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The stopword floor a predicate's invariant is checked against: a predicate
# whose only non-slot text is stopwords ("the {x}") cannot smuggle past as an
# invariant (ADR-0030 decision 6). This is the CANONICAL definition; the
# free-text Jaccard path in `runner.regression` imports `_STOPWORDS` from here
# so there is one set, never two drifting copies. It lives in the core (this
# module) because the predicate parser is core (ADR-0003) and the runner
# (which depends on the core) is the one that imports down, never the reverse.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "and", "or", "of", "to", "in", "on",
    "with", "for", "by", "at", "as", "be", "this", "that",
})

# The slot shape vocabulary is deliberately tiny (ADR-0030 decision 5):
#   {slot}          -> presence-only (any non-empty instance token),
#   {slot:numeric}  -> filler must be all digits,
#   {slot:uuid}     -> filler must be a UUID shape.
# Richer shapes (regex, enum, semver) are a future ADR, NOT this one (decision
# D). An unknown shape keyword is a loud rejection, never a silent downgrade.
_KNOWN_SHAPES: frozenset[str] = frozenset({"numeric", "uuid"})

# A slot name is a simple identifier (letters / digits / underscore), so the
# braces are unambiguous and a stray `{` or `}` is caught as malformed.
_SLOT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_NUMERIC_RE = re.compile(r"^[0-9]+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class PredicateError(ValueError):
    """A `value_predicate` that is malformed or carries no invariant.

    Raised at parse/validate time so a bad predicate is a LOUD rejection at the
    write boundary (ADR-0030 decision 6), never a silent downgrade to the
    free-text path.
    """


@dataclass(frozen=True)
class Slot:
    """One declared variable slot: a name and an optional shape.

    `shape` is None for a bare `{slot}` (presence-only) or one of
    `_KNOWN_SHAPES` for a typed slot.
    """

    name: str
    shape: str | None = None


@dataclass(frozen=True)
class Predicate:
    """A parsed, validated predicate template.

    `template` is the raw string; `slots` is the ordered list of declared
    slots; `_match_re` is the compiled matcher (built over the NORMALIZED
    template) whose literal segments are the invariant and whose groups are the
    slot fillers. Build via `parse()`, which validates; do not construct
    directly with an unvalidated template.
    """

    template: str
    slots: tuple[Slot, ...]
    _match_re: re.Pattern[str]

    def evaluate(self, observed_value: str) -> bool:
        """True iff the OBSERVED value satisfies this predicate (ADR-0030
        decision 2).

        The invariant text matches EXACTLY (case-folded + whitespace-
        normalized); each declared slot must be FILLED by a non-empty instance
        token, and a slot that declares a shape requires the filler to satisfy
        that shape. The slot's literal value is NEVER compared between the seed
        and the run; only its PRESENCE (and optional shape) is checked. There
        is no Jaccard and no fuzzy comparison anywhere.
        """
        normalized = _normalize(observed_value)
        # Containment, not whole-string equality: the invariant (with its slots)
        # must APPEAR somewhere in the observed value, so an agent that wraps the
        # fact in narration ("after saving, the route matches /box/editor/329419
        # ok") still matches. The invariant text itself is still matched
        # literally and the slots still shape-checked, so a wrong invariant (a
        # 500 where 2xx is required, a wrong route) still does NOT match. This
        # replaced an earlier whole-string `fullmatch`, which was too brittle
        # against an LLM agent's run-to-run phrasing variance (a live run dropped
        # to 2/4 only because of surrounding words).
        m = self._match_re.search(normalized)
        if m is None:
            return False
        # Every slot must be filled by a non-empty token; a declared shape
        # tightens that to a shape check on the OBSERVED filler. The regex
        # group is `\S+` so it is already non-empty, but check explicitly so
        # the invariant is documented and survives any future loosening.
        for slot in self.slots:
            filler = m.group(slot.name)
            if not filler:
                return False
            if slot.shape == "numeric" and not _NUMERIC_RE.fullmatch(filler):
                return False
            if slot.shape == "uuid" and not _UUID_RE.fullmatch(filler):
                return False
        return True


def _normalize(s: str) -> str:
    """Case-fold + collapse runs of whitespace to a single space, then strip.

    The only two normalizations the prose path already implies (ADR-0030
    decision 1). Punctuation is NOT normalized away: the invariant text must be
    authored to match what the agent reports.
    """
    return " ".join(s.casefold().split())


# A slot occurrence in the raw template: `{name}` or `{name:shape}`.
_SLOT_RE = re.compile(r"\{([^{}]*)\}")


def _invariant_tokens(literal_segments: list[str]) -> set[str]:
    """Content tokens of the invariant (the non-slot literal text), reusing the
    free-text tokenizer's stopword set so a stopword-only invariant is caught.

    Mirrors `regression._tokens` (alphanumeric runs, `/` kept) but is kept
    local so this module stays a pure parser and does not depend on the
    matcher's tokenizer beyond the shared `_STOPWORDS` set.
    """
    out: set[str] = set()
    for seg in literal_segments:
        cur: list[str] = []
        for ch in seg.casefold():
            if ch.isalnum() or ch == "/":
                cur.append(ch)
            else:
                if cur:
                    out.add("".join(cur))
                    cur.clear()
        if cur:
            out.add("".join(cur))
    return {t for t in out if t and t not in _STOPWORDS}


def parse(template: str) -> Predicate:
    """Parse + validate a `value_predicate` template (ADR-0030 decisions 5, 6).

    Rejects (raising `PredicateError`):
      - an empty / whitespace-only template,
      - a malformed slot: unbalanced braces, an empty slot name, a bad slot
        name, an unknown shape keyword, a duplicate slot name,
      - a predicate that is entirely one slot or has no non-slot text at all
        (no invariant -> would match everything),
      - a predicate whose only non-slot text is stopwords (no durable invariant
        token).

    On success returns a `Predicate` whose `_match_re` matches the NORMALIZED
    observed value: literal (invariant) segments are matched exactly and each
    slot is a non-empty `\\S+` capture group keyed by the slot name.
    """
    if not template or not template.strip():
        raise PredicateError("value_predicate is empty or whitespace only")

    # Unbalanced braces: any `{` or `}` left after stripping well-formed
    # `{...}` occurrences is malformed. Check before the slot scan so a stray
    # brace is a clear rejection rather than a confusing later failure.
    stripped = _SLOT_RE.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise PredicateError(
            f"value_predicate has unbalanced braces: {template!r}"
        )

    slots: list[Slot] = []
    seen_names: set[str] = set()
    # Build the matcher regex AND the literal-segment list in one left-to-right
    # pass over the template, so the invariant text and the slot order stay in
    # lockstep with the source string. Whitespace is normalized by matching any
    # run of literal whitespace AND any whitespace at a slot boundary with
    # `\s+` (the observed value is also whitespace-normalized in `evaluate`), so
    # spacing differences between the seed and the run never break a match while
    # the non-space invariant text is still matched EXACTLY.
    # No leading/trailing anchors: the matcher is `search` (containment), so the
    # invariant can appear surrounded by an agent's narration. The literal
    # invariant text is still matched verbatim; only whole-string equality is
    # relaxed.
    regex_parts: list[str] = []
    literal_segments: list[str] = []
    pos = 0
    for m in _SLOT_RE.finditer(template):
        literal = template[pos:m.start()]
        literal_segments.append(literal)
        regex_parts.append(_literal_to_regex(literal))
        inner = m.group(1).strip()
        name, shape = _parse_slot_inner(inner, template)
        if name in seen_names:
            raise PredicateError(
                f"value_predicate declares slot {name!r} more than once: "
                f"{template!r}"
            )
        seen_names.add(name)
        slots.append(Slot(name=name, shape=shape))
        # A slot binds one non-empty instance TOKEN: `\S+` (no whitespace), so
        # it never swallows a following invariant word. The shape (numeric /
        # uuid) is checked on the captured filler in `evaluate`, not here, so a
        # malformed filler is a non-match rather than a regex miss that is hard
        # to attribute.
        regex_parts.append(rf"(?P<{name}>\S+)")
        pos = m.end()
    trailing = template[pos:]
    literal_segments.append(trailing)
    regex_parts.append(_literal_to_regex(trailing))

    # No-invariant guard (decision 3 + 6): the literal text must carry at least
    # one durable (non-stopword) token. A predicate that is only a slot, or
    # whose literal text is whitespace / stopwords, would match everything.
    if not _invariant_tokens(literal_segments):
        raise PredicateError(
            f"value_predicate has no invariant: its non-slot text is empty or "
            f"only stopwords, so it would match everything: {template!r}"
        )

    match_re = re.compile("".join(regex_parts))
    return Predicate(template=template, slots=tuple(slots), _match_re=match_re)


def _literal_to_regex(literal: str) -> str:
    """Turn one invariant literal segment into a whitespace-tolerant regex.

    Each run of NON-whitespace is escaped and matched EXACTLY (after case-fold);
    each run of whitespace becomes `\\s+`, including a leading / trailing run so
    the boundary against an adjacent slot stays whitespace-tolerant. An empty
    or whitespace-only segment yields `\\s*` (no invariant text, optional
    spacing). Case-folding is applied here; the observed value is case-folded
    in `evaluate`, so the two sides agree.
    """
    if literal == "":
        return ""
    folded = literal.casefold()
    parts: list[str] = []
    for piece in re.split(r"(\s+)", folded):
        if piece == "":
            continue
        if piece.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(piece))
    return "".join(parts)


def _parse_slot_inner(inner: str, template: str) -> tuple[str, str | None]:
    """Parse the inside of a `{...}` into (name, shape).

    Accepts `name` (bare, shape None) or `name:shape`. Rejects an empty name,
    a malformed name, an unknown shape, or extra colons.
    """
    if inner == "":
        raise PredicateError(
            f"value_predicate has an empty slot `{{}}`: {template!r}"
        )
    if ":" in inner:
        parts = inner.split(":")
        if len(parts) != 2:
            raise PredicateError(
                f"value_predicate slot {inner!r} is malformed (expected "
                f"`name` or `name:shape`): {template!r}"
            )
        name, shape = parts[0].strip(), parts[1].strip()
        if not _SLOT_NAME_RE.match(name):
            raise PredicateError(
                f"value_predicate slot name {name!r} is malformed: {template!r}"
            )
        if shape not in _KNOWN_SHAPES:
            raise PredicateError(
                f"value_predicate slot {name!r} declares unknown shape "
                f"{shape!r}; known shapes are {sorted(_KNOWN_SHAPES)} or a bare "
                f"slot with no shape: {template!r}"
            )
        return name, shape
    name = inner
    if not _SLOT_NAME_RE.match(name):
        raise PredicateError(
            f"value_predicate slot name {name!r} is malformed: {template!r}"
        )
    return name, None
