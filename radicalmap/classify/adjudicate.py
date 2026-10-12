"""Stage two: adjudicate candidates for precision.

The lexicon over-selects on purpose. This is what turns a candidate into a
finding, a rejection, or an explicit "uncertain" — and it records all three,
because a corpus that only stores confirmations cannot report a false-positive
rate and therefore cannot report precision at all.

Cost control, in order of how much they save:

  Only candidates are adjudicated, never the whole corpus.
  Identical texts are adjudicated once and the verdict fanned out — comment
  text repeats heavily ("based", a copied slogan), so this is not marginal.
  Bulk work goes through the Batches API at half price.

Nothing here contacts the API unless explicitly enabled. An adjudication run
spends real money, so it does not start as a side effect of anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from ..db import engine
from . import lexicon as L
from . import runs

PROMPT_PATH = Path(__file__).parent / "prompts" / "adjudicate.md"
DEFAULT_MODEL = "claude-sonnet-5"
VALID_LABELS = {"antisemitic", "white_nationalist", "conspiracy", "radicalisation",
                "dehumanisation", "esoteric", "incel", "islamophobic", "accelerationist"}
VALID_VERDICTS = {"confirmed", "rejected", "uncertain"}
VALID_SEVERITY = {"low", "medium", "high", None}


def prompt_text() -> str:
    return PROMPT_PATH.read_text()


def prompt_hash() -> str:
    return hashlib.sha256(prompt_text().encode()).hexdigest()


# ─── units of work ───────────────────────────────────────────────────────────

@dataclass
class Unit:
    """One text to adjudicate, with every candidate that fired on it."""
    key: str
    text: str
    context: str
    matched: list[dict[str, str]]
    targets: list[tuple[str, int]] = field(default_factory=list)   # (type, id)
    candidate_ids: list[int] = field(default_factory=list)

    def payload(self) -> dict[str, object]:
        return {"id": self.key, "text": self.text[:4000],
                "context": self.context[:1500], "matched": self.matched}


@dataclass
class Verdict:
    key: str
    verdict: str
    label: str | None
    severity: str | None
    confidence: float
    evidence: str | None
    rationale: str
    innocent_reading: str = ""

    @classmethod
    def parse(cls, raw: dict) -> "Verdict | None":
        verdict = str(raw.get("verdict", "")).strip().lower()
        if verdict not in VALID_VERDICTS:
            return None
        label = raw.get("label")
        label = str(label).strip().lower() if label else None
        if label not in VALID_LABELS:
            label = None
        severity = raw.get("severity")
        severity = str(severity).strip().lower() if severity else None
        if severity not in VALID_SEVERITY:
            severity = None
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        # A confirmation with no label is not a usable finding.
        if verdict == "confirmed" and label is None:
            verdict = "uncertain"
        return cls(str(raw.get("id", "")), verdict, label, severity, confidence,
                   (str(raw["evidence"])[:500] if raw.get("evidence") else None),
                   str(raw.get("rationale", ""))[:1000],
                   str(raw.get("innocent_reading", ""))[:500])


def collect_units(run_id: int, *, limit: int = 0) -> list[Unit]:
    """Gather candidates from a lexicon run, deduplicated by text.

    Grouping by text hash rather than by target is the main cost lever: the
    same comment text recurs across a corpus, and the verdict for a given
    string with a given set of matched terms is the same every time.
    """
    rows = engine.query(
        """SELECT c.id AS cand_id, c.target_type, c.target_id, c.label, c.evidence_span,
                  c.rationale, e.slug, e.tier, e.definition
             FROM classifications c
             LEFT JOIN lexicon_entries e ON e.id = c.lexicon_entry_id
            WHERE c.run_id = ? AND c.verdict = 'candidate'
            ORDER BY c.target_type, c.target_id""", (run_id,))

    texts: dict[tuple[str, int], tuple[str, str]] = {}
    grouped: dict[tuple[str, int], list] = {}
    for r in rows:
        grouped.setdefault((r["target_type"], int(r["target_id"])), []).append(r)

    for (ttype, tid) in grouped:
        texts[(ttype, tid)] = _text_and_context(ttype, tid)

    units: dict[str, Unit] = {}
    for (ttype, tid), cands in grouped.items():
        text, context = texts[(ttype, tid)]
        slugs = tuple(sorted({r["slug"] or "?" for r in cands}))
        key = hashlib.sha256(("\x00".join(slugs) + "\x01" + text).encode()).hexdigest()[:24]
        unit = units.get(key)
        if unit is None:
            unit = units[key] = Unit(
                key=key, text=text, context=context,
                matched=[{"term": r["slug"] or "?", "tier": r["tier"] or "?",
                          "matched_text": r["evidence_span"] or "",
                          "note": (r["definition"] or "")[:300]}
                         for r in cands])
        unit.targets.append((ttype, tid))
        unit.candidate_ids += [int(r["cand_id"]) for r in cands]
        if limit and len(units) >= limit:
            break
    return list(units.values())


def _text_and_context(target_type: str, target_id: int) -> tuple[str, str]:
    if target_type == "comment":
        row = engine.query_one(
            """SELECT c.text, i.caption, a.handle, a.bio
                 FROM comments c JOIN items i ON i.id = c.item_id
                 LEFT JOIN accounts a ON a.id = i.account_id
                WHERE c.id = ?""", (target_id,))
        if not row:
            return "", ""
        return (row["text"] or "",
                f"Posted under a video captioned: {(row['caption'] or '')[:400]!r}"
                + (f"\nVideo posted by @{row['handle']}" if row["handle"] else ""))
    if target_type == "item":
        row = engine.query_one(
            """SELECT i.caption, i.title, a.handle, a.bio
                 FROM items i LEFT JOIN accounts a ON a.id = i.account_id
                WHERE i.id = ?""", (target_id,))
        if not row:
            return "", ""
        return (" ".join(filter(None, [row["caption"], row["title"]])),
                f"Account @{row['handle'] or '?'} bio: {(row['bio'] or '')[:300]!r}")
    row = engine.query_one("SELECT handle, bio FROM accounts WHERE id = ?", (target_id,))
    if not row:
        return "", ""
    return (row["bio"] or "", f"Account handle: @{row['handle'] or '?'}")


# ─── adjudicators ────────────────────────────────────────────────────────────

class Adjudicator(Protocol):
    name: str
    model: str

    def adjudicate(self, units: Sequence[Unit]) -> list[Verdict]: ...


class StubAdjudicator:
    """Deterministic test double.

    Exists so the full pipeline — collection, dedup, persistence, evaluation —
    is testable without an API key and without spending anything. Its rules are
    crude on purpose; it must never be mistaken for the real thing, so it says
    so in the run's model field.
    """

    name = "stub"
    model = "stub:deterministic"

    INNOCENT = re.compile(
        r"\b(my|our)\s+\w*\s*(grandmother|grandfather|family|friend)\b"
        r"|\bi\s+am\s+jewish\b|\bdissertation\b|\bresearch(?:ing)?\b|\bnews\b"
        r"|\bcommunity\s+centre\b|\blinguistic\b|\beclipse\b|\bhearings?\b",
        re.IGNORECASE)

    def adjudicate(self, units: Sequence[Unit]) -> list[Verdict]:
        out = []
        for u in units:
            tiers = {m["tier"] for m in u.matched}
            label = u.matched[0]["term"] if u.matched else None
            category = None
            for m in u.matched:
                entry = L.BY_SLUG.get(m["term"])
                if entry and (category is None or entry.tier < "C"):
                    category = entry.category
            if self.INNOCENT.search(u.text):
                out.append(Verdict(u.key, "rejected", None, None, 0.8, None,
                                   "stub: innocent-context marker present"))
            elif "A" in tiers:
                out.append(Verdict(u.key, "confirmed", category, "high", 0.9,
                                   u.matched[0]["matched_text"], "stub: tier-A match"))
            elif "B" in tiers:
                out.append(Verdict(u.key, "uncertain", category, "medium", 0.5,
                                   u.matched[0]["matched_text"],
                                   "stub: tier-B needs real adjudication"))
            else:
                out.append(Verdict(u.key, "rejected", None, None, 0.6, None,
                                   "stub: tier-C only"))
        return out


class AnthropicAdjudicator:
    """The real adjudicator."""

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, *, batch_size: int = 20,
                 max_tokens: int = 4096) -> None:
        self.model = model
        self.batch_size = batch_size
        self.max_tokens = max_tokens
        self._client = None

    def _client_or_fail(self):
        if os.environ.get("RM_ALLOW_LLM", "").lower() not in ("1", "true", "yes"):
            raise RuntimeError(
                "LLM adjudication is disabled. It spends money against a real API "
                "key, so it never runs implicitly — set RM_ALLOW_LLM=1 to permit it.")
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def build_request(self, units: Sequence[Unit]) -> dict[str, object]:
        """Pure: the request body, buildable and testable with no API key."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": prompt_text(),
            "messages": [{"role": "user", "content": json.dumps(
                [u.payload() for u in units], ensure_ascii=False, indent=1)}],
        }

    @staticmethod
    def parse_response(text: str) -> list[Verdict]:
        """Tolerant of the model wrapping JSON in prose or a fenced block."""
        cleaned = text.strip()
        fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.S)
        if fence:
            cleaned = fence.group(1).strip()
        if not cleaned.startswith("["):
            m = re.search(r"\[.*\]", cleaned, re.S)
            cleaned = m.group(0) if m else cleaned
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = [data]
        return [v for v in (Verdict.parse(d) for d in data if isinstance(d, dict)) if v]

    def adjudicate(self, units: Sequence[Unit]) -> list[Verdict]:
        client = self._client_or_fail()
        out: list[Verdict] = []
        for i in range(0, len(units), self.batch_size):
            chunk = units[i:i + self.batch_size]
            resp = client.messages.create(**self.build_request(chunk))
            out += self.parse_response("".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"))
        return out

    # ── Batches API: half price, and the right shape for tens of thousands ──

    def submit_batch(self, units: Sequence[Unit]) -> str:
        client = self._client_or_fail()
        requests = []
        for i in range(0, len(units), self.batch_size):
            chunk = units[i:i + self.batch_size]
            requests.append({"custom_id": f"rm-{i // self.batch_size:06d}",
                             "params": self.build_request(chunk)})
        batch = client.messages.batches.create(requests=requests)
        return batch.id

    def poll_batch(self, batch_id: str, *, interval: float = 30.0,
                   timeout: float = 86400.0) -> list[Verdict]:
        client = self._client_or_fail()
        deadline = time.time() + timeout
        while time.time() < deadline:
            batch = client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                out: list[Verdict] = []
                for entry in client.messages.batches.results(batch_id):
                    if entry.result.type != "succeeded":
                        continue
                    out += self.parse_response("".join(
                        b.text for b in entry.result.message.content
                        if getattr(b, "type", "") == "text"))
                return out
            time.sleep(interval)
        raise TimeoutError(f"batch {batch_id} did not finish within {timeout}s")


# ─── persistence ─────────────────────────────────────────────────────────────

def persist(verdicts: Sequence[Verdict], units: Sequence[Unit], run_id: int) -> dict[str, int]:
    """Write one verdict row per (target, candidate), linked to its candidate.

    A verdict fans out to every target that shared the deduplicated text, and
    each row keeps parent_id so the chain lexicon-entry -> candidate -> verdict
    stays walkable. That chain is what makes per-pattern precision computable.
    """
    by_key = {u.key: u for u in units}
    counts = {"confirmed": 0, "rejected": 0, "uncertain": 0, "rows": 0, "unmatched": 0}
    rows = []
    for v in verdicts:
        unit = by_key.get(v.key)
        if unit is None:
            counts["unmatched"] += 1
            continue
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
        parents = {cid: None for cid in unit.candidate_ids}
        parent_rows = engine.query(
            "SELECT id, target_type, target_id FROM classifications WHERE id IN (%s)"
            % ",".join("?" * len(parents)), tuple(parents)) if parents else []
        for pr in parent_rows:
            rows.append((pr["target_type"], int(pr["target_id"]), run_id, "llm",
                         v.label or (unit.matched[0]["term"] if unit.matched else "unknown"),
                         v.severity, v.confidence, v.evidence, None, None,
                         (v.rationale + (f" | innocent reading considered: {v.innocent_reading}"
                                         if v.innocent_reading else ""))[:1000],
                         None, int(pr["id"]), v.verdict))
    if rows:
        with engine.transaction():
            engine.connect().executemany(
                """INSERT INTO classifications
                       (target_type, target_id, run_id, stage, label, severity, confidence,
                        evidence_span, evidence_start, evidence_end, rationale,
                        lexicon_entry_id, parent_id, verdict)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    counts["rows"] = len(rows)
    return counts


def run(candidate_run_id: int | None = None, *, adjudicator: Adjudicator | None = None,
        limit: int = 0, verbose: bool = True) -> dict[str, object]:
    candidate_run_id = candidate_run_id or runs.latest("lexicon")
    if candidate_run_id is None:
        raise RuntimeError("no lexicon run to adjudicate — run `classify scan` first")

    adj = adjudicator or StubAdjudicator()
    units = collect_units(candidate_run_id, limit=limit)
    if verbose:
        total_targets = sum(len(u.targets) for u in units)
        saved = total_targets - len(units)
        print(f"  {len(units)} unique texts to adjudicate across {total_targets} targets"
              + (f" ({saved} saved by deduplication)" if saved else ""))
    if not units:
        return {"units": 0}

    run_id = runs.start("llm", f"adjudicate-{adj.name}",
                        model=adj.model, prompt_hash=prompt_hash(),
                        params=json.dumps({"candidate_run": candidate_run_id,
                                           "units": len(units)}),
                        notes=f"adjudication of lexicon run {candidate_run_id}")
    verdicts = adj.adjudicate(units)
    counts = persist(verdicts, units, run_id)
    runs.finish(run_id, counts["rows"])
    counts["run_id"] = run_id
    counts["units"] = len(units)
    if verbose:
        print(f"  run {run_id}: {counts['confirmed']} confirmed, "
              f"{counts['rejected']} rejected, {counts['uncertain']} uncertain "
              f"({counts['rows']} rows)")
    return counts
