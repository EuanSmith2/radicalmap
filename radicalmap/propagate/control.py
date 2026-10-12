"""Keeping a self-directing crawl interpretable.

A crawler that follows the strongest signal will, left alone, do two things
that quietly destroy the resulting dataset:

  DRIFT. Every hop is locally reasonable and the destination is somewhere else
  entirely. Extremist content overlaps with gaming, fitness, history,
  metal and folk music; three plausible hops from a neo-Nazi account is a
  Warhammer painting channel. The corpus stops being a sample of anything.

  SATURATION BLINDNESS. Without a discovery-rate measure there is no principled
  moment to stop, so a crawl ends when someone loses patience. "We collected
  400k videos" is not a finding; "new accounts per hundred fetches fell below
  two and stayed there" is the evidence that a community was enumerated.

Both are instruments, not guardrails: they are measured and recorded whether or
not they change a decision, because the shape of a snowball sample is only
defensible if it can be described afterwards.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from ..classify import lexicon as L
from ..db import engine
from ..learn.features import FeatureSpec, extract


@dataclass
class DriftMonitor:
    """Distance from the seed set's centre of mass, in feature space.

    The centroid is built from the SEEDS, not from everything collected —
    updating it as the crawl proceeds would let it walk off with the crawler,
    which is precisely the failure it exists to catch.
    """
    spec: FeatureSpec = field(default_factory=FeatureSpec)
    centroid: dict[int, float] = field(default_factory=dict)
    norm: float = 0.0
    n_seeds: int = 0
    threshold: float = 0.80          # drift above this stops expansion
    history: list[float] = field(default_factory=list)

    families: dict[str, float] = field(default_factory=dict)

    def fit(self, texts: list[str]) -> "DriftMonitor":
        acc: dict[int, float] = {}
        fam: dict[str, float] = {}
        from .. import taxonomy as T
        for t in texts:
            matches = L.scan(t).matches
            for k, v in extract(t, matches, self.spec).items():
                acc[k] = acc.get(k, 0.0) + v
            for m in matches:
                d = T.DOMAIN_BY_SLUG.get(m.domain)
                if d and d.family != "baseline":
                    fam[d.family] = fam.get(d.family, 0.0) + 1.0
        if acc:
            n = float(len(texts)) or 1.0
            self.centroid = {k: v / n for k, v in acc.items()}
            self.norm = math.sqrt(sum(v * v for v in self.centroid.values())) or 1.0
        total = sum(fam.values()) or 1.0
        self.families = {k: v / total for k, v in fam.items()}
        self.n_seeds = len(texts)
        return self

    @property
    def fitted(self) -> bool:
        return bool(self.centroid or self.families)

    def drift(self, text: str) -> float:
        """0 = squarely inside the seed set's territory, 1 = unrelated.

        An UNFITTED monitor returns 0.0, not 1.0. The first version returned
        maximum drift when it had no seeds — which is defensible as a fail-closed
        default until you watch it happen: a Propagator built before the first
        item is collected has an empty centroid, refuses every candidate, and the
        crawl does nothing while reporting that it ran. Having no basis to judge
        relevance is not evidence of irrelevance, and a silent halt is a worse
        failure than a permissive one. Callers are warned instead.

        Measured at the DOMAIN-FAMILY level, not the vocabulary level. The first
        version used a raw lexical centroid and scored "remigration now, defend
        europe" at 0.869 against sourdough's 0.891 — treating legitimate
        expansion within the far right as almost as foreign as baking, because
        it shares a family with the seeds but not their words. Expanding across
        a family is what a snowball crawl is FOR; leaving every family the seeds
        occupy is the thing worth stopping.

        Lexical distance still contributes, but only as a tiebreaker for text
        the lexicon says nothing about.
        """
        from .. import taxonomy as T
        if not self.fitted:
            return 0.0
        if not (text or "").strip():
            return 1.0

        matches = L.scan(text).matches
        fams = {T.DOMAIN_BY_SLUG[m.domain].family for m in matches
                if m.domain in T.DOMAIN_BY_SLUG
                and T.DOMAIN_BY_SLUG[m.domain].family != "baseline"}

        lexical = 1.0
        if self.centroid:
            f = extract(text, matches, self.spec)
            if f:
                num = sum(v * self.centroid.get(k, 0.0) for k, v in f.items())
                den = (math.sqrt(sum(v * v for v in f.values())) or 1.0) * self.norm
                lexical = max(0.0, min(1.0, 1.0 - num / den))

        if not self.families or not fams:
            # Nothing to compare on the family axis: fall back to lexical
            # distance, but never report better than 0.5 on lexical evidence
            # alone — vocabulary overlap is weak grounds for staying.
            return max(0.5, lexical) if not fams else lexical

        shared = self.families.keys() & fams
        if shared:
            # Inside a seed family. Weight by how much of the seed set that
            # family accounts for, so a crawl seeded 90% far-right treats a
            # far-right hop as very close and a far-left hop as further out.
            mass = sum(self.families[f] for f in shared)
            return float(max(0.0, min(1.0, (1.0 - mass) * 0.5 + lexical * 0.15)))
        # Flagged, but in a family the seeds do not occupy. Real drift, though
        # less than untagged content — a crawl finding an adjacent movement is
        # a result, not a malfunction, and should be recorded rather than cut.
        return float(min(1.0, 0.65 + lexical * 0.2))

    def record(self, value: float) -> None:
        self.history.append(value)

    def summary(self) -> dict[str, object]:
        if not self.history:
            return {"seeds": self.n_seeds, "observations": 0}
        h = np.array(self.history)
        return {"seeds": self.n_seeds, "observations": len(h),
                "mean_drift": round(float(h.mean()), 3),
                "p90_drift": round(float(np.percentile(h, 90)), 3),
                "over_threshold": int((h > self.threshold).sum()),
                "threshold": self.threshold}


@dataclass
class SaturationMonitor:
    """Discovery rate over the crawl.

    Sampled every `interval` fetches. When the number of previously unseen
    accounts per interval stays below `floor` for `patience` consecutive
    samples, the reachable community is enumerated and further crawling is
    buying duplicates.
    """
    run_id: int | None = None
    interval: int = 100
    floor: int = 2
    patience: int = 3
    fetches: int = 0
    _last: dict[str, int] = field(default_factory=dict)
    _low_streak: int = 0
    samples: list[dict] = field(default_factory=list)

    def _counts(self) -> dict[str, int]:
        return {t: int(engine.scalar(f"SELECT count(*) FROM {t}") or 0)
                for t in ("accounts", "items", "sounds")}

    def tick(self, n: int = 1) -> dict | None:
        self.fetches += n
        if self.fetches % self.interval and self.samples:
            return None
        counts = self._counts()
        new_accounts = counts["accounts"] - self._last.get("accounts", 0)
        self._last = counts
        sample = {"fetches": self.fetches, "new_accounts": new_accounts, **counts}
        self.samples.append(sample)
        engine.execute(
            """INSERT INTO saturation_samples (run_id, fetches, known_accounts,
                   known_items, known_sounds, new_accounts) VALUES (?,?,?,?,?,?)""",
            (self.run_id, self.fetches, counts["accounts"], counts["items"],
             counts["sounds"], new_accounts))
        if len(self.samples) > 1:
            self._low_streak = self._low_streak + 1 if new_accounts <= self.floor else 0
        return sample

    @property
    def saturated(self) -> bool:
        return self._low_streak >= self.patience

    def curve(self) -> list[dict]:
        return self.samples

    def summary(self) -> dict[str, object]:
        if len(self.samples) < 2:
            return {"fetches": self.fetches, "saturated": False,
                    "note": "not enough samples to judge"}
        recent = [s["new_accounts"] for s in self.samples[-self.patience:]]
        return {"fetches": self.fetches, "samples": len(self.samples),
                "saturated": self.saturated,
                "recent_new_accounts_per_interval": recent,
                "floor": self.floor, "interval": self.interval}


def seed_texts(limit: int = 200) -> list[str]:
    """Text of the items a crawl was seeded from, for fitting the centroid.

    Falls back to the earliest-collected items when nothing is explicitly
    marked as a seed, which is the right default: the oldest rows are what the
    corpus grew outward from.
    """
    rows = engine.query(
        """SELECT i.caption AS t FROM items i
             LEFT JOIN sources s ON s.id = i.source_id
            WHERE coalesce(i.caption,'') != ''
            ORDER BY CASE WHEN s.kind = 'seed_list' THEN 0 ELSE 1 END,
                     i.first_seen_at ASC LIMIT ?""", (limit,))
    return [r["t"] for r in rows if r["t"]]
