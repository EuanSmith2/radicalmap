"""The label taxonomy: three axes, not one list.

v1 had nine flat categories, all far-right, each doubling as a verdict. That
shape cannot express the corpus the tool is being pointed at now, and trying to
extend it by adding "communism" and "self_harm" to the same list would be a
category error with real consequences.

Three axes instead:

  DOMAIN      what ideological or topical family the content belongs to.
              Descriptive. "maoist" is a domain the way "catholic" or
              "libertarian" would be — a fact about the content, never an
              accusation.

  HARM TYPE   what, if anything, makes it a finding. Incitement, dehumanisation,
              glorification of violence, recruitment, promotion of self-harm.
              A domain with harm_type = none is a description and nothing more.

  STANCE      the speaker's relation to the material. Promoting, glorifying and
              recruiting are finding-eligible; discussing, reporting, countering,
              recovering and help-seeking are not.

A finding requires all three to line up. This is the difference between a
research instrument and a political surveillance tool, and it is why communism
and Maoism appear here as domains rather than as harms: advocating a Marxist
economics is lawful political speech across the EU, and only the harm axis —
glorifying insurgent violence, calling for killings — makes a piece of content a
finding. The same rule protects the right-wing case: "conservative" and
"nationalist" are domains too.

The framing matches how the field already works. Europol's TE-SAT and the Global
Terrorism Database both separate ideological affiliation from terrorist conduct;
the DSA's Art. 34 systemic-risk list likewise names conduct, not politics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ─── stance ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Stance:
    slug: str
    name: str
    finding_eligible: bool
    note: str


STANCES: list[Stance] = [
    Stance("promoting", "Promoting", True,
           "Advances the position as the speaker's own."),
    Stance("glorifying", "Glorifying", True,
           "Celebrates violence, a perpetrator, or an atrocity."),
    Stance("recruiting", "Recruiting", True,
           "Solicits others to join a group, channel or action."),
    Stance("depicting", "Depicting", False,
           "Shows the material without a clear stance. Ambiguous by nature — "
           "adjudicated case by case, never auto-confirmed."),
    Stance("discussing", "Discussing", False,
           "Analytical or conversational reference, including political argument."),
    Stance("reporting", "Reporting", False,
           "Journalistic or documentary account."),
    Stance("countering", "Countering", False,
           "Opposes, debunks or mocks the position. Counter-speech shares almost "
           "all of its vocabulary with the thing it opposes, which is why stance "
           "has to be modelled rather than inferred from keywords."),
    Stance("recovery", "Recovery", False,
           "Recovery-oriented content in the self-harm and eating-disorder "
           "domains. Uses the same coded vocabulary as pro-content and must "
           "never be recorded as a harm finding."),
    Stance("help_seeking", "Help-seeking", False,
           "A person describing their own distress or asking for support. Not a "
           "finding under any circumstances. Recording someone's disclosure of "
           "their own suffering as a harm finding is both wrong and harmful."),
]

STANCE_BY_SLUG = {s.slug: s for s in STANCES}
FINDING_STANCES = {s.slug for s in STANCES if s.finding_eligible}


# ─── harm types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HarmType:
    slug: str
    name: str
    severity_ceiling: str
    note: str
    requires_dpia: bool = False


HARM_TYPES: list[HarmType] = [
    HarmType("incitement_to_violence", "Incitement to violence", "high",
             "Calls for or encourages physical harm against a person or group. "
             "The core offence under the Criminal Justice (Incitement to Violence "
             "or Hatred and Hate Offences) Act 2024, ss.7 and 10."),
    HarmType("glorification_of_violence", "Glorification of violence", "high",
             "Celebrates an attack, atrocity or perpetrator without necessarily "
             "calling for more."),
    HarmType("dehumanisation", "Dehumanisation", "high",
             "Describes a group as vermin, disease, subhuman or otherwise outside "
             "moral consideration. The most reliable single precursor signal in "
             "the genocide-prevention literature."),
    HarmType("terrorist_org_support", "Support for a proscribed organisation", "high",
             "Material support, branding or claimed affiliation."),
    HarmType("recruitment", "Recruitment", "medium",
             "Directs an audience to an off-platform group, channel or action."),
    HarmType("harassment_coordination", "Coordinated harassment", "medium",
             "Organises or directs a pile-on against a named target."),
    HarmType("conspiracy_targeting_group", "Group-targeting conspiracy", "medium",
             "Attributes hidden coordinated malice to an ethnic, religious or "
             "national group. Capped at medium: the claim is the harm, and "
             "escalation to high needs an incitement or dehumanisation element."),
    HarmType("identity_denigration", "Identity denigration", "medium",
             "Slurs or degradation targeting a protected characteristic, short of "
             "dehumanisation."),
    HarmType("self_harm_promotion", "Promotion of self-harm", "high",
             "Encourages, instructs or normalises self-injury or suicide. "
             "Named as a systemic risk in DSA Art. 34(1)(d).", requires_dpia=True),
    HarmType("disordered_eating_promotion", "Promotion of disordered eating", "high",
             "Pro-ana/pro-mia content: goal weights, restriction targets, purging "
             "instruction, thinspiration.", requires_dpia=True),
    HarmType("minor_targeting", "Targeting of minors", "high",
             "Grooming, coercion or sextortion aimed at children — the defining "
             "conduct of the nihilistic violent extremist networks.",
             requires_dpia=True),
    HarmType("none", "No harm identified", "low",
             "The content belongs to the domain but carries no harm. This is the "
             "expected value for most political content and is NOT a finding."),
]

HARM_BY_SLUG = {h.slug: h for h in HARM_TYPES}


# ─── domains ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Domain:
    slug: str
    name: str
    family: str
    note: str
    requires_dpia: bool = False
    typical_harms: tuple[str, ...] = field(default_factory=tuple)


DOMAINS: list[Domain] = [
    # ── far right ───────────────────────────────────────────────────────────
    Domain("ethnonationalist", "Ethnonationalist", "far_right",
           "Nation defined by descent; immigration framed as demographic threat.",
           typical_harms=("conspiracy_targeting_group", "dehumanisation")),
    Domain("neo_nazi", "Neo-Nazi", "far_right",
           "Explicit National Socialist identification, symbols and codes.",
           typical_harms=("dehumanisation", "glorification_of_violence")),
    Domain("esoteric_fascist", "Esoteric fascist", "far_right",
           "Occult-fascist mythology: Hyperborea, the Black Sun, Vril.",
           typical_harms=("glorification_of_violence",)),
    Domain("identitarian", "Identitarian", "far_right",
           "Generation Identity and successors; 'remigration', Defend Europe.",
           typical_harms=("conspiracy_targeting_group",)),
    Domain("accelerationist_rw", "Right-wing accelerationist", "far_right",
           "Siege culture, Turner Diaries, collapse-as-strategy.",
           typical_harms=("incitement_to_violence", "terrorist_org_support")),
    Domain("antisemitic_conspiracy", "Antisemitic conspiracy", "far_right",
           "ZOG, Kalergi, 'noticing', the Early Life check.",
           typical_harms=("conspiracy_targeting_group", "dehumanisation")),
    Domain("islamophobic", "Anti-Muslim", "far_right",
           "Framings presenting Muslim populations as a coordinated threat.",
           typical_harms=("conspiracy_targeting_group", "identity_denigration")),

    # ── far left ────────────────────────────────────────────────────────────
    # These are DOMAINS, not harms. A Marxist reading group and an
    # insurrectionary cell share a domain and differ entirely on the harm axis.
    Domain("marxist_leninist", "Marxist-Leninist", "far_left",
           "Communist political tradition. Lawful political speech across the EU "
           "with sitting parliamentary parties; a finding requires a harm axis."),
    Domain("maoist", "Maoist", "far_left",
           "Maoist political tradition. Studied in universities and organised "
           "lawfully in many states. Only glorification of insurgent violence "
           "(People's War, the Naxalite and Shining Path campaigns) is a harm."),
    Domain("insurrectionary_anarchist", "Insurrectionary anarchist", "far_left",
           "Anarchist currents advocating direct action. Europol TE-SAT tracks "
           "left-wing and anarchist terrorism as a distinct category.",
           typical_harms=("incitement_to_violence",)),
    Domain("accelerationist_lw", "Left-wing accelerationist", "far_left",
           "Collapse-as-strategy from the left; guillotine and 'eat the rich' "
           "violence imagery where it passes from rhetoric into advocacy.",
           typical_harms=("glorification_of_violence",)),

    # ── religious ───────────────────────────────────────────────────────────
    Domain("jihadist", "Jihadist", "religious",
           "Salafi-jihadist movements and proscribed organisation propaganda.",
           typical_harms=("terrorist_org_support", "incitement_to_violence")),
    Domain("christian_identity", "Christian Identity", "religious",
           "Racialised theology underpinning parts of the US far right.",
           typical_harms=("dehumanisation",)),

    # ── gendered ────────────────────────────────────────────────────────────
    Domain("manosphere_incel", "Manosphere / incel", "gendered",
           "Blackpill fatalism, foid/femoid vocabulary, looksmaxxing.",
           typical_harms=("dehumanisation", "identity_denigration")),
    Domain("misogynist_harassment", "Misogynist harassment", "gendered",
           "Coordinated targeting of women.",
           typical_harms=("harassment_coordination",)),

    # ── conspiracy ──────────────────────────────────────────────────────────
    Domain("qanon", "QAnon and successors", "conspiracy",
           "Cabal narratives, 'the storm', save-the-children framings."),
    Domain("new_world_order", "New World Order", "conspiracy",
           "Great Reset, globalist elite control narratives."),

    # ── self-directed harm — GATED ──────────────────────────────────────────
    # Art. 9 special-category health data. Detection and storage stay off until
    # a DPIA reference is configured. See governance() below.
    Domain("self_harm", "Self-harm", "self_directed",
           "Self-injury and suicide-related content. Health data under GDPR "
           "Art. 9; processing relies on Art. 9(2)(j) plus DPA 2018 s.42 "
           "suitable and specific measures.",
           requires_dpia=True, typical_harms=("self_harm_promotion",)),
    Domain("eating_disorder", "Eating disorder", "self_directed",
           "Pro-ana and pro-mia communities: goal weights, restriction, "
           "thinspiration, purging. Health data under GDPR Art. 9.",
           requires_dpia=True, typical_harms=("disordered_eating_promotion",)),
    Domain("nihilistic_violent_extremism", "Nihilistic violent extremism",
           "self_directed",
           "The 764 / CVLT network cluster: violent nihilist groups that coerce "
           "minors into self-harm and produce material from it. Bridges violent "
           "extremism and self-directed harm, which is why it is gated with them.",
           requires_dpia=True,
           typical_harms=("minor_targeting", "self_harm_promotion")),

    # ── baseline ────────────────────────────────────────────────────────────
    Domain("mainstream_political", "Mainstream political", "baseline",
           "Ordinary political speech across the spectrum. Present so the "
           "classifier has somewhere correct to put it, instead of forcing "
           "everything political into an extremist domain."),
    Domain("none", "No domain", "baseline",
           "Not political or harm-related content at all."),
]

DOMAIN_BY_SLUG = {d.slug: d for d in DOMAINS}
FAMILIES = sorted({d.family for d in DOMAINS})
GATED_DOMAINS = {d.slug for d in DOMAINS if d.requires_dpia}
GATED_HARMS = {h.slug for h in HARM_TYPES if h.requires_dpia}


# ─── governance gate ─────────────────────────────────────────────────────────

DPIA_ENV = "RM_DPIA_REF"


def dpia_reference() -> str | None:
    ref = os.environ.get(DPIA_ENV, "").strip()
    return ref or None


def gate_open() -> bool:
    return dpia_reference() is not None


def governance() -> dict[str, object]:
    """Why the gate exists, in the run record rather than only in a comment."""
    return {
        "gated_domains": sorted(GATED_DOMAINS),
        "gated_harm_types": sorted(GATED_HARMS),
        "dpia_reference": dpia_reference(),
        "gate_open": gate_open(),
        "basis": ("Self-harm and eating-disorder content is health data and a "
                  "special category under GDPR Art. 9(1). Research processing "
                  "relies on Art. 9(2)(j) with Art. 89(1) safeguards and DPA 2018 "
                  "s.42 suitable and specific measures — which require a "
                  "documented assessment this project does not yet hold. "
                  f"Set {DPIA_ENV} to its reference to enable these domains."),
    }


def check_gate(domain: str | None = None, harm_type: str | None = None) -> None:
    """Raise unless the gate is open for a restricted domain or harm type."""
    restricted = (domain in GATED_DOMAINS) or (harm_type in GATED_HARMS)
    if restricted and not gate_open():
        raise PermissionError(
            f"domain={domain!r} harm_type={harm_type!r} is gated: it processes "
            f"special-category health data under GDPR Art. 9. Set {DPIA_ENV} to a "
            f"documented assessment reference to enable it.")


# ─── labels ──────────────────────────────────────────────────────────────────

def is_finding(domain: str | None, harm_type: str | None, stance: str | None) -> bool:
    """All three axes must line up.

    This single function is where the design decision lives: a domain alone is
    never a finding, a harm type in a non-promoting stance is never a finding,
    and content someone is countering or recovering from is never a finding.
    """
    if not harm_type or harm_type == "none":
        return False
    if domain in (None, "none", "mainstream_political"):
        return False
    return stance in FINDING_STANCES


def label(domain: str | None, harm_type: str | None) -> str:
    """Canonical composite label used as `classifications.label`."""
    return f"{domain or 'none'}:{harm_type or 'none'}"


def severity_ceiling(harm_type: str | None) -> str:
    h = HARM_BY_SLUG.get(harm_type or "none")
    return h.severity_ceiling if h else "low"


def cap_severity(harm_type: str | None, severity: str | None) -> str | None:
    """Never let an adjudicator exceed a harm type's ceiling."""
    if severity is None:
        return None
    order = {"low": 0, "medium": 1, "high": 2}
    ceiling = severity_ceiling(harm_type)
    return severity if order.get(severity, 0) <= order[ceiling] else ceiling


def summary() -> dict[str, object]:
    by_family: dict[str, int] = {}
    for d in DOMAINS:
        by_family[d.family] = by_family.get(d.family, 0) + 1
    return {
        "domains": len(DOMAINS), "by_family": by_family,
        "harm_types": len(HARM_TYPES), "stances": len(STANCES),
        "finding_eligible_stances": sorted(FINDING_STANCES),
        "gated_domains": sorted(GATED_DOMAINS),
        "gate_open": gate_open(),
    }


# ─── database sync ───────────────────────────────────────────────────────────

def sync() -> dict[str, int]:
    """Mirror the taxonomy into its reference tables.

    The definitions above are the source of truth; the tables exist so that
    classifications can carry real foreign keys and so the taxonomy a stored
    verdict was made under is recoverable from the database alone. Idempotent,
    and run after every migration so the two cannot drift.
    """
    from .db import engine

    with engine.transaction():
        engine.connect().executemany(
            """INSERT INTO domains (slug, name, family, note, requires_dpia)
               VALUES (?,?,?,?,?)
               ON CONFLICT (slug) DO UPDATE SET
                   name=excluded.name, family=excluded.family, note=excluded.note,
                   requires_dpia=excluded.requires_dpia""",
            [(d.slug, d.name, d.family, d.note, int(d.requires_dpia)) for d in DOMAINS])
        engine.connect().executemany(
            """INSERT INTO harm_types (slug, name, severity_ceiling, note, requires_dpia)
               VALUES (?,?,?,?,?)
               ON CONFLICT (slug) DO UPDATE SET
                   name=excluded.name, severity_ceiling=excluded.severity_ceiling,
                   note=excluded.note, requires_dpia=excluded.requires_dpia""",
            [(h.slug, h.name, h.severity_ceiling, h.note, int(h.requires_dpia))
             for h in HARM_TYPES])
        engine.connect().executemany(
            """INSERT INTO stances (slug, name, finding_eligible, note)
               VALUES (?,?,?,?)
               ON CONFLICT (slug) DO UPDATE SET
                   name=excluded.name, finding_eligible=excluded.finding_eligible,
                   note=excluded.note""",
            [(s.slug, s.name, int(s.finding_eligible), s.note) for s in STANCES])

        # Record every session in which restricted processing was enabled, and
        # under which assessment. The gate is only meaningful if its use leaves
        # a trail.
        if gate_open():
            engine.execute(
                "INSERT INTO governance_events (event, dpia_ref, detail) VALUES (?,?,?)",
                ("art9_gate_open", dpia_reference(),
                 f"gated domains enabled: {sorted(GATED_DOMAINS)}"))
    return {"domains": len(DOMAINS), "harm_types": len(HARM_TYPES),
            "stances": len(STANCES)}
