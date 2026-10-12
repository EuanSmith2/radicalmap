"""The detection lexicon — stage one of two.

This is a CANDIDATE GENERATOR, not a classifier. It is tuned for recall and it
never issues a verdict or a severity. That division is the whole point of the
redesign.

v1 conflated the two. flag_patterns.py carried (regex, category, severity)
triples and flag_comment() returned a finished judgement, so a pattern matching
the word "jew" wrote "antisemitic / high" straight onto the row. Over 105
comments that is survivable. Over a million it produces a corpus in which
Jewish people discussing their own heritage, Holocaust remembrance, and news
reporting are all recorded as high-severity antisemitism — and nothing in the
data distinguishes those from real findings.

Three tiers, and the tier is a claim about AMBIGUITY, not about severity:

  A  The term is essentially never innocent in this register. 1488, sieg heil,
     holohoax, day of the rope. Still adjudicated, but a rejection here is rare
     and worth inspecting.

  B  Coded or contextual. Strongly associated with the movement AND in ordinary
     use elsewhere: the demonym "jew", "noticing", "globalist", "remigration".
     These are exactly the terms that need a second stage. Most of the lexicon
     lives here.

  C  Weak community signal. "based", "link in bio", "jan 6". Individually near
     worthless — "based" is mainstream internet slang — but meaningful in
     combination. A single tier-C hit is NOT a candidate; that gate is what
     stops the false-positive flood.

Every entry carries a research note explaining what the term means and why it
is here, because the dictionary is itself a research output and an unexplained
pattern cannot be reviewed, defended, or retired on evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator

VERSION = "v3.0"

# The v1/v2 single-axis categories, mapped onto the domain axis. Note how badly
# the old list mixed the two: "dehumanisation" was never a domain, it is a harm
# type, and "radicalisation" was neither — it described a process.
CATEGORY_TO_DOMAIN = {
    "white_nationalist": "ethnonationalist",
    "antisemitic": "antisemitic_conspiracy",
    "conspiracy": "new_world_order",
    "esoteric": "esoteric_fascist",
    "incel": "manosphere_incel",
    "islamophobic": "islamophobic",
    "accelerationist": "accelerationist_rw",
    "dehumanisation": "ethnonationalist",
    "radicalisation": "none",
    "political": "none",
    "jihadist": "jihadist",
    "self_harm": "self_harm",
    "eating_disorder": "eating_disorder",
    "nihilistic_violent_extremism": "nihilistic_violent_extremism",
}

# The legacy categories mapped onto the HARM axis. The old list had to serve
# both jobs at once, which is why it contained "dehumanisation" (a harm) beside
# "esoteric" (a domain) beside "radicalisation" (neither — a process).
CATEGORY_TO_HARM = {
    "white_nationalist": "identity_denigration",
    "antisemitic": "conspiracy_targeting_group",
    "conspiracy": "conspiracy_targeting_group",
    "esoteric": "none",
    "incel": "identity_denigration",
    "islamophobic": "conspiracy_targeting_group",
    "accelerationist": "incitement_to_violence",
    "dehumanisation": "dehumanisation",
    "radicalisation": "none",
    "political": "none",
}


@dataclass(frozen=True)
class Entry:
    slug: str
    pattern: str
    tier: str
    category: str            # legacy v1 axis, kept so the v1 comparison still runs
    note: str
    lang: str = "en"
    origin: str = ""
    flags: int = re.IGNORECASE
    # The three-axis model. `domain` says what the content IS; `harm` says what
    # would make it a finding, and is "none" for entries that only identify a
    # community. Most political entries are domain markers with no harm at all —
    # that is the point of separating the axes.
    domain: str = ""
    harm: str = ""          # "" = not yet mapped; "none" = deliberately harmless
    stance_hint: str = ""

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)

    @property
    def resolved_domain(self) -> str:
        return self.domain or CATEGORY_TO_DOMAIN.get(self.category, "none")

    @property
    def resolved_harm(self) -> str:
        """An explicit "none" is a claim — this entry identifies a community and
        nothing more. An empty string just means the legacy category has not been
        mapped by hand yet, so fall back to the category's default harm."""
        return self.harm or CATEGORY_TO_HARM.get(self.category, "none")

    @property
    def gated(self) -> bool:
        from .. import taxonomy as T
        return (self.resolved_domain in T.GATED_DOMAINS
                or self.resolved_harm in T.GATED_HARMS)


# ─── Tier A — essentially never innocent ─────────────────────────────────────

TIER_A: list[Entry] = [
    Entry("num-1488", r"\b1488\b", "A", "white_nationalist",
          "Concatenation of the '14 Words' slogan and 88 (HH = Heil Hitler). The "
          "single most recognisable neo-Nazi numeric code.",
          origin="Coined by David Lane, 1980s"),
    Entry("fourteen-words", r"\b(?:14|fourteen)\s*words\b", "A", "white_nationalist",
          "The 14-word white-survivalist slogan written by David Lane. Referring to "
          "it by number is itself the in-group signal."),
    Entry("sieg-heil", r"\b(?:sieg|zieg)\s*heil\b|\bsik\s*haj\b", "A", "white_nationalist",
          "Nazi salute. 'Sik haj' is the Swedish phonetic spelling used to evade "
          "keyword moderation.", lang="mul"),
    Entry("heil-hitler", r"\bheil\s*hitler\b|\bhh\s*88\b", "A", "white_nationalist",
          "Explicit Nazi salutation, written out or as the HH88 letter-number "
          "compound. No documented innocent usage in this register."),
    Entry("holocaust-denial", r"\bholohoax\b|\bsix\s*million\s*(?:lies|is\s*a\s*lie)\b"
                              r"|\bholoc?aust\s*(?:didn.?t|never)\s*happen",
          "A", "antisemitic",
          "Holocaust denial. A criminal offence in several EU states and a core "
          "antisemitic conspiracy claim."),
    Entry("slur-kike", r"\bkikes?\b", "A", "antisemitic",
          "Ethnic slur for Jewish people. No non-pejorative use."),
    Entry("zog", r"\bzog\b|\bzionist\s*occupied\s*government\b", "A", "antisemitic",
          "'Zionist Occupied Government' — the claim that Western states are "
          "covertly controlled by Jewish interests."),
    Entry("day-of-the-rope", r"\bday\s*of\s*the\s*rope\b", "A", "accelerationist",
          "Mass-execution episode from The Turner Diaries, invoked as an aspiration "
          "for killing political enemies."),
    Entry("rwds", r"\brwds\b|\bright[\s-]*wing\s*death\s*squads?\b", "A", "accelerationist",
          "'Right Wing Death Squads' — endorsement of extrajudicial political killing."),
    Entry("gas-them", r"\bgas\s*(?:the\s*)?(?:jews|them|kikes)\b|\bback\s*to\s*the\s*ovens?\b",
          "A", "antisemitic",
          "Direct incitement referencing the Holocaust."),
    Entry("total-death", r"\btotal\s+\w+\s+death\b|\bt[nkj]d\b(?!\w)", "A", "dehumanisation",
          "The 'Total ___ Death' formula and its acronyms — a genocidal slogan "
          "template targeting a named group."),
    Entry("sonnenrad", r"\bsonnenrad\b|\bschwarze\s*sonne\b", "A", "esoteric",
          "The Black Sun wheel, an SS occult symbol adopted by esoteric "
          "neo-Nazism and by several recent attackers.", lang="mul"),
    Entry("ss-units", r"\bschutzstaffel\b|\bwaffen[\s-]*ss\b|\btotenkopf\b", "A",
          "white_nationalist", "Named Nazi paramilitary formations."),
    Entry("blood-and-soil", r"\bblood\s*and\s*soil\b|\bblut\s*und\s*boden\b", "A",
          "white_nationalist",
          "Nazi agrarian-racial slogan, revived at Charlottesville 2017.", lang="mul"),
    Entry("iotbw", r"\biotbw\b|\bit.?s\s*ok(?:ay)?\s*to\s*be\s*white\b", "A",
          "white_nationalist",
          "An organised 4chan poster campaign (2017) engineered so that objecting "
          "to it appears to be objecting to white people."),
    Entry("remove-kebab", r"\bremove\s*kebab\b", "A", "dehumanisation",
          "Meme celebrating the Srebrenica genocide; cited approvingly in the "
          "Christchurch attacker's material."),
    Entry("sig-runes", r"⚡\ufe0f?\s*⚡|☇\s*☇|ᛋᛋ", "A", "white_nationalist",
          "Doubled lightning bolts standing in for the SS sig runes (ᛋᛋ). The "
          "single bolt is an ordinary emoji; the DOUBLED form is the signal.",
          flags=re.IGNORECASE | re.UNICODE),
    Entry("turner-diaries", r"\bturner\s*diaries\b|\bsiege\s*culture\b|\bjames\s*mason\b",
          "A", "accelerationist",
          "Foundational accelerationist texts advocating terrorism to precipitate "
          "racial civil war."),
]

# Far-right and runic symbols used to frame numeric codes. Written as an
# alternation rather than a character class because several are multi-codepoint
# sequences that a class would split into their components.
_SYM = r"(?:☦\ufe0f?|🌲|🏴|⚡\ufe0f?|✝\ufe0f?|🗡\ufe0f?|⚔\ufe0f?|卐|卍|ᛋ|᛭|🌞|☀\ufe0f?)"

# ─── Tier B — coded or contextual, needs adjudication ────────────────────────

TIER_B: list[Entry] = [
    # The headline case. This is a NEUTRAL DEMONYM. It belongs in the lexicon
    # because plainly-worded antisemitism uses it, and it must never by itself
    # produce a verdict — v1 wrote "antisemitic / high" on every occurrence.
    Entry("demonym-jewish", r"\bjews?\b|\bjewish\b|\bjewry\b", "B", "antisemitic",
          "Neutral demonym. Present for recall only: explicit antisemitism often "
          "uses the plain word. Innocent use is the COMMON case — Jewish people "
          "discussing themselves, Holocaust remembrance, ordinary news. Never a "
          "finding on its own."),
    Entry("goyim", r"\bgoyim?\b|\bgoyslop\b", "B", "antisemitic",
          "Hebrew for non-Jews, reappropriated ironically in antisemitic circles "
          "to imply the speaker is among a duped majority."),
    Entry("noticing", r"\bnotic(?:ing|ed)\s*(?:things|a\s*pattern)?\b|\bze\s*zeggen\s*het\b"
                      r"|\bhave\s*you\s*notic",
          "B", "antisemitic",
          "Euphemism for perceiving alleged Jewish control of institutions, chosen "
          "precisely because the bare word is unremarkable. Dutch 'ze zeggen het' "
          "is the equivalent.", lang="mul"),
    Entry("early-life", r"\bearly\s*life\s*check\b|\bcheck\s*(?:the\s*)?early\s*life\b",
          "B", "antisemitic",
          "Instruction to check the 'Early life' section of a Wikipedia article to "
          "infer Jewish ancestry — an antisemitic dog whistle disguised as research."),
    Entry("great-replacement", r"\bgreat\s*replacement\b|\bwhite\s*genocide\b|\bkalergi\b"
                               r"|\breplacement\s*(?:theory|migration)\b",
          "B", "conspiracy",
          "The claim that white populations are being deliberately displaced. Named "
          "in the Christchurch and Buffalo manifestos. Also discussed by name in "
          "journalism and scholarship, hence tier B."),
    Entry("remigration", r"\bremigrations?\b|\båtervandring\b|\bfolkutbyte", "B",
          "white_nationalist",
          "Euphemism for mass deportation of ethnic minorities, mainstreamed by the "
          "Identitarian movement. Swedish 'återvandring'/'folkutbyte' are the "
          "Scandinavian equivalents.", lang="mul"),
    Entry("globalist", r"\bglobalists?\b|\bnew\s*world\s*order\b|\bnwo\b"
                       r"|\bgreat\s*reset\b|\bdeep\s*state\b",
          "B", "conspiracy",
          "Conspiratorial framing of a hidden controlling elite; 'globalist' often "
          "functions as a coded substitute for 'Jewish'. Also used non-coded in "
          "ordinary political argument."),
    Entry("named-financiers", r"\bsoros\b|\brothschilds?\b", "B", "antisemitic",
          "Individuals and families used as stand-ins for 'Jewish control' in "
          "conspiracy narratives. Legitimate reference exists, hence tier B."),
    Entry("funny-hats", r"\bfunny\s*hats?\b|\bthe\s*chosen\s*ones?\b", "B", "antisemitic",
          "Coded references to Jewish identity (the kippah; 'chosen people') used to "
          "gesture at antisemitic claims without naming them."),
    Entry("esoteric-fascism", r"\bhyperborea\w*\b|\bagartha\w*\b|\bvril\w*\b"
                              r"|\bhollow\s*earth\b|\bblack\s*sun\b|\bthule\b",
          "B", "esoteric",
          "Occult-fascist mythology: a lost Aryan homeland and hidden civilisations. "
          "Overlaps with non-political occult and fiction communities."),
    Entry("aryan", r"\baryan\b|\bnordicist\b|\bphenotype\s*check\b", "B",
          "white_nationalist",
          "Racial-hierarchy vocabulary. Has a distinct legitimate use in "
          "Indo-Iranian linguistics and history."),
    Entry("anti-white", r"\banti[\s-]*white\b|\bwhite\s*erasure\b", "B", "white_nationalist",
          "Persecution framing positioning white people as the victimised group."),
    Entry("defend-europe", r"\b(?:defend|save|reclaim|take\s*back)\s*europe\b"
                           r"|\beurope\s*is\s*ours\b|\bidentitarian\b",
          "B", "white_nationalist",
          "Identitarian movement slogans (Generation Identity / Defend Europe)."),
    Entry("accelerationism", r"\bboogaloo\b|\bciviI?\s*war\s*2\b|\baccelerat(?:e|ion|ionist)\b"
                             r"|\bhelicopter\s*rides?\b",
          "B", "accelerationist",
          "Advocacy of collapse or civil war as a route to political change. "
          "'Helicopter rides' references Pinochet-era extrajudicial killings."),
    Entry("incel-lexicon", r"\bfoids?\b|\bfemoids?\b|\broasties?\b|\bblackpill(?:ed)?\b"
                           r"|\bhit\s*the\s*wall\b|\blooksmax",
          "B", "incel",
          "Manosphere vocabulary that dehumanises women. Note 'blackpill' also "
          "appears in unrelated fatalistic slang."),
    Entry("islamophobic", r"\bgreat\s*jihad\b|\bislamification\b|\bislamisation\b"
                          r"|\bno[\s-]*go\s*zones?\b|\brapefugees?\b",
          "B", "islamophobic",
          "Framings presenting Muslim populations as a coordinated demographic threat."),
    # The number must be ADJACENT TO A SYMBOL, or be the entire text.
    #
    # Ported from v1, where the character class admitted a plain space on each
    # side and included "%" as a terminator. The behavioural tests showed what
    # that actually matched: "88% off today", "born in 88", "the 14 was great".
    # A pattern whose note claims it requires symbol context, and which in fact
    # fires on a percentage and a birth year, is the exact v1 failure this
    # redesign is about — so it is written here to match its own description.
    Entry("numeric-codes-symbol",
          r"(?:" + _SYM + r"\s*(?:88|14|18|28)\b"
          r"|\b(?:88|14|18|28)\s*" + _SYM +
          r"|^\s*(?:88|14|18|28)\s*$)",
          "B", "white_nationalist",
          "Numeric codes (88=HH, 14=14 Words, 18=AH, 28=Blood & Honour) set off by "
          "far-right symbol emoji, or standing entirely alone. Bare digits in "
          "running text are overwhelmingly birth years, prices and jersey numbers, "
          "so proximity to a symbol is what carries the signal.",
          flags=re.IGNORECASE | re.UNICODE),
    Entry("white-monster", r"\bwhite\s*monster\b", "B", "white_nationalist",
          "Monster Energy's white can, adopted as a deniable white-pride signal."),
    Entry("clown-world", r"\bclown\s*world\b|\bhonk\s*honk\b|🤡\s*🌍", "B", "radicalisation",
          "Framing society as absurd and irredeemable — a common on-ramp motif.",
          flags=re.IGNORECASE | re.UNICODE),
    Entry("nordic-nationalist", r"\balternativ\s*f[öo]r\s*sverige\b|\bstoppa\s*folkutbyte"
                                r"|\bnordisk\s*motstånd", "B", "white_nationalist",
          "Swedish far-right party and movement names, and the 'stop the population "
          "replacement' slogan.", lang="sv"),
    Entry("hispanic-nationalist", r"\bpatria[,\s]+sangre\b|\bsangre\s*y\s*suelo\b", "B",
          "white_nationalist",
          "'Homeland, blood' / 'blood and soil' in Spanish — Falangist vocabulary.",
          lang="es"),
    Entry("ban-evasion", r"\b(?:\d+(?:st|nd|rd|th)|second|third|fourth|fifth)\s*account\b"
                         r"|\bbanned\s*at\s*\d+\s*k\b|\bback\s*again\s*lads\b",
          "B", "radicalisation",
          "Evasion signalling: announcing a replacement account after moderation. "
          "Evidence of a persistent actor, and of the platform having already acted."),
    Entry("satire-shield", r"\(\s*all\s*satire\s*\)|\ball\s*satire\b|\bjust\s*a\s*joke\s*bro\b",
          "B", "radicalisation",
          "Pre-emptive deniability disclaimer. Notable because it marks awareness "
          "that the content breaches the rules."),
    Entry("farright-nostalgia", r"\b(?:prime\s*)?far[\s-]*right\s*tiktok\b"
                                r"|\bmiss\s*(?:prime\s*)?far[\s-]*right\b",
          "B", "radicalisation",
          "Explicit nostalgia for a pre-moderation era of the platform's far-right "
          "community — an unusually direct statement of affiliation."),
    Entry("jbw", r"\bjbw\b", "B", "white_nationalist",
          "'Just Be White' — racial-hierarchy shorthand from manosphere/WN overlap."),
    # The five entries below were recovered by comparing v2 against v1 on the
    # real corpus: v1 caught them, the first draft of v2 did not. That is the
    # lexicon-maintenance loop working as intended — a miss found by
    # measurement rather than by recollection.
    Entry("slavic-secret", r"\bslavic\s*secret\b|\bunderground\s*civili[sz]"
                           r"|\binner\s*earth\b", "B", "esoteric",
          "References to a hidden Slavic or subterranean civilisation — the "
          "Hyperborean myth cluster in its Eastern European form."),
    Entry("sun-will-rise", r"\bsun\s*will\s*rise\s*again\b|\bthe\s*sun\s*returns\b",
          "B", "esoteric",
          "Esoteric National Socialist trope figuring fascism's return as a "
          "sunrise; usually paired with sonnenrad or sig-rune imagery."),
    Entry("blackshirts", r"\bblackshirts?\b|\bsquadristi\b|\bmussolini\b"
                         r"|\bfascisti\b", "B", "white_nationalist",
          "Italian Fascist paramilitary and its leader. Tier B because "
          "historical and educational discussion is common.", lang="mul"),
    Entry("intensified-endorsement",
          r"\b(?:absolute|so|dead|ultra|most|fully)\s+ba[sz]ed\b"
          r"|\bbased\s+and\s+\w*pilled\b|\bbased\s+department\b",
          "B", "radicalisation",
          "Intensified forms of the endorsement slang. Unlike bare 'based', the "
          "intensifier marks deliberate in-group signalling rather than passing "
          "slang, which is why this sits a tier above it."),
    Entry("group-seeking", r"\bwhere.?s\s*the\s*(?:group|chat|server)\b"
                           r"|\bpv\s*message\b|\bp\.?m\.?\s*me\s*(?:for|the)\b"
                           r"|\binvite\s*me\b|\bhow\s*do\s*i\s*join\b",
          "C", "radicalisation",
          "Requests to be admitted to an off-platform group — the demand side of "
          "recruitment, where recruitment-link is the supply side."),
    Entry("caps-they", r"\bTHEY\b", "B", "conspiracy",
          "Capitalised THEY, marking an unnamed controlling group — frequently a "
          "placeholder for an antisemitic referent.", flags=0),
]

# ─── Tier C — weak community signal, only meaningful in combination ──────────

TIER_C: list[Entry] = [
    Entry("based", r"\bbased\b|\bba[sz]ed\b|\bredpill(?:ed)?\b", "C", "radicalisation",
          "Approval slang. Now thoroughly mainstream — on its own this means "
          "nothing, which is why it is tier C and cannot produce a candidate alone."),
    Entry("recruitment-link", r"\btelegram\b|\bt\.me/|\blink\s*in\s*(?:bio|comments?)\b"
                              r"|\bjoin\s*(?:us|the\s*(?:group|server|chat))\b",
          "C", "radicalisation",
          "Off-platform migration signal. Very common in ordinary promotion, so "
          "weak alone; strong alongside anything else."),
    Entry("risky-repost", r"\brisky\s*repost\b|\bdeleting\s*this\s*later\b", "C",
          "radicalisation",
          "Marks awareness that sharing carries social or moderation risk — a "
          "recognised marker of partial endorsement."),
    Entry("amplification", r"\b(?:added|instantly)\s*to\s*(?:my\s*)?stor"
                           r"|\bsav(?:ing|ed)\s*(?:the\s*)?(?:sound|video|this)\s*before\b"
                           r"|\bbefore\s*(?:it.?s\s*)?(?:deleted|taken\s*down)\b",
          "C", "radicalisation",
          "Amplification and archival behaviour, often framed around anticipated "
          "censorship."),
    Entry("jan6", r"\bjan(?:uary)?\s*6(?:th)?\b|\bstop\s*the\s*steal\b", "C",
          "radicalisation",
          "Reference point that ranges from neutral news discussion to celebration."),
    Entry("glowie", r"\bfed\s*post(?:ing)?\b|\bglowie\b|\bglow(?:ing)?\s*in\s*the\s*dark\b",
          "C", "radicalisation",
          "Accusations of undercover law enforcement — indicates a community that "
          "expects to be investigated."),
    Entry("hidden-meaning", r"\bin\s*plain\s*sight\b|\bsymbolism\b|\bthey\s*always\s*knew\b"
                            r"|\bthey\s*(?:don.?t|won.?t)\s*want\s*you\s*to\b|\bwake\s*up\b",
          "C", "conspiracy",
          "Hidden-knowledge framing. Extremely common across all conspiracy content "
          "and much ordinary rhetoric."),
    Entry("iykyk", r"\biykyk\b|\bif\s*you\s*know\s*you\s*know\b|\bwe\s*know\b", "C",
          "radicalisation",
          "In-group gatekeeping phrase signalling deliberately unstated meaning."),
]

# ─── Retired from v1, with the reason recorded ───────────────────────────────
#
# Kept as data rather than deleted. A pattern that was dropped silently is
# indistinguishable from one that was never considered, and reviewers of the
# published dictionary are entitled to see what was removed and why.

RETIRED: list[dict[str, str]] = [
    {"pattern": r"beer belly.?s\s+in\s+revolt", "reason":
     "Written to match one specific video in the 18-item development sample. A "
     "rule that matches exactly one document is a bookmark, not a lexicon entry."},
    {"pattern": r"i support israel|love pride month|happy black history", "reason":
     "Intended to catch sarcastic inversion, but the pattern cannot see sarcasm. "
     "As written it flags anyone sincerely expressing support for Israel, Pride, "
     "or Black History Month — the most dangerous false-positive class in the "
     "whole v1 lexicon."},
    {"pattern": r"\bgaddafi\b", "reason":
     "Any mention of a head of state. Historical and news discussion dominates."},
    {"pattern": r"\bnationalist\b", "reason":
     "Far too broad: captures every civic-nationalist, sporting and historical use. "
     "Replaced by qualified forms (white nationalist, ethnonationalist, identitarian)."},
    {"pattern": r"midsommar|midsummer", "reason":
     "Folk-nationalist aesthetics do appropriate midsummer imagery, but the base "
     "rate of ordinary Scandinavian seasonal content is overwhelming."},
    {"pattern": r"\bthey\s+(hid|put it|always knew)\b", "reason":
     "Folded into the tier-C hidden-meaning entry, where a lone match no longer "
     "produces a candidate."},
    {"pattern": r"\burban\s+classic\b", "reason":
     "Derived from a single observed usage; no corroborating evidence that it is "
     "an established coded term."},
    {"pattern": r"\bhans\s*landa\b", "reason":
     "A Tarantino character. Admiration is not reliably distinguishable from film "
     "discussion by a keyword."},
]

ALL_ENTRIES: list[Entry] = TIER_A + TIER_B + TIER_C
BY_SLUG: dict[str, Entry] = {e.slug: e for e in ALL_ENTRIES}


# ─── Non-regex detectors ─────────────────────────────────────────────────────

HIDDEN_CODES = {
    "SS": ("white_nationalist", "Schutzstaffel encoded in mixed case"),
    "HH": ("white_nationalist", "Heil Hitler encoded in mixed case"),
    "AH": ("white_nationalist", "Adolf Hitler's initials encoded in mixed case"),
}

_WORD_RE = re.compile(r"\b[A-Za-z]{3,}\b")
# Ordinary English words whose natural capitalisation collides with the codes.
_HIDDEN_ALLOW = {"MySpace", "YouTube", "SoundCloud", "PhD", "IPhone", "MacBook"}


def detect_hidden_codes(text: str) -> Iterator[tuple[str, str, str, int, int]]:
    """Codes hidden by capitalisation inside an otherwise ordinary word.

    'StreSS' carries SS; 'proHHibited' carries HH. Ported from v1, which had it
    inline. Skips all-upper and all-lower words, since the signal IS the
    deliberate mixed casing.
    """
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        if word.isupper() or word.islower() or word in _HIDDEN_ALLOW:
            continue
        for code, (category, note) in HIDDEN_CODES.items():
            idx = word.find(code)
            # Require the code to be genuinely embedded, not just a normal
            # capitalised first letter followed by caps.
            if idx > 0 and word[idx:idx + 2] == code:
                yield code, category, f"{note}: {word!r}", m.start(), m.end()
                break


USERNAME_CODES: list[tuple[str, str, str, str]] = [
    (r"1488", "A", "white_nationalist", "1488 in handle — explicit neo-Nazi code"),
    (r"(?<!\d)88(?!\d)", "B", "white_nationalist", "88 in handle — HH / Heil Hitler"),
    (r"(?<!\d)14(?!\d)", "B", "white_nationalist", "14 in handle — the 14 Words"),
    (r"(?<!\d)18(?!\d)", "B", "white_nationalist", "18 in handle — AH / Adolf Hitler"),
    (r"(?<!\d)28(?!\d)", "B", "white_nationalist", "28 in handle — BH / Blood & Honour"),
    (r"\b(?:wotan|odinist|vinland|kalki)\b", "B", "esoteric",
     "Norse-revivalist naming used by esoteric far-right currents"),
]

# A bare two-digit number in a handle is usually a birth year. Recording that
# openly matters: these entries are tier B precisely because their base rate in
# ordinary usernames is enormous.
USERNAME_NOTE = ("Two-digit codes in handles have a very high innocent base rate "
                 "(birth years, jersey numbers). Tier B, never a finding alone.")


# ─── Candidates ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Match:
    slug: str
    tier: str
    category: str
    span: str
    start: int
    end: int
    note: str
    domain: str = "none"
    harm: str = ""          # "" = not yet mapped; "none" = deliberately harmless
    stance_hint: str = ""


@dataclass
class Candidate:
    """What the lexicon proposes for adjudication."""
    matches: list[Match] = field(default_factory=list)

    @property
    def tiers(self) -> set[str]:
        return {m.tier for m in self.matches}

    @property
    def categories(self) -> list[str]:
        seen: dict[str, None] = {}
        for m in sorted(self.matches, key=lambda x: ("ABC".index(x.tier), x.slug)):
            seen.setdefault(m.category, None)
        return list(seen)

    @property
    def top_tier(self) -> str:
        return min(self.tiers, key=lambda t: "ABC".index(t)) if self.matches else "C"

    @property
    def is_candidate(self) -> bool:
        """The recall gate.

        Any A or B match qualifies. Tier C needs two DISTINCT entries — a lone
        'based' or a lone 'link in bio' does not start an adjudication, and that
        single rule removes the largest false-positive class v1 had.
        """
        if not self.matches:
            return False
        if self.tiers & {"A", "B"}:
            return True
        return len({m.slug for m in self.matches if m.tier == "C"}) >= 2


def scan(text: str | None, *, is_username: bool = False) -> Candidate:
    """Find every lexicon hit in one piece of text."""
    cand = Candidate()
    if not text:
        return cand

    for entry in active_entries():
        m = entry.compiled().search(text)
        if m:
            cand.matches.append(Match(entry.slug, entry.tier, entry.category,
                                      m.group(0)[:120], m.start(), m.end(), entry.note,
                                      entry.resolved_domain, entry.resolved_harm, entry.stance_hint))

    for code, category, note, start, end in detect_hidden_codes(text):
        cand.matches.append(Match(f"hidden-{code.lower()}", "B", category,
                                  text[start:end][:120], start, end, note,
                                  "neo_nazi", "none", ""))

    if is_username:
        for pattern, tier, category, note in USERNAME_CODES:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                cand.matches.append(Match(f"handle-{pattern[:12]}", tier, category,
                                          m.group(0), m.start(), m.end(),
                                          f"{note}. {USERNAME_NOTE}",
                                          "ethnonationalist", "none", ""))
    return cand


# ─── Versioning ──────────────────────────────────────────────────────────────

def fingerprint() -> str:
    """Content hash of the lexicon. Any edit changes the version, so a stored
    classification can always be traced to the exact rules that produced it."""
    payload = json.dumps(
        [[e.slug, e.pattern, e.tier, e.category, e.lang, e.resolved_domain, e.resolved_harm]
         for e in ALL_ENTRIES]
        + [[c[0], c[1], c[2]] for c in USERNAME_CODES]
        + sorted(HIDDEN_CODES),
        sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def summary() -> dict[str, object]:
    by_tier: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for e in ALL_ENTRIES:
        by_tier[e.tier] = by_tier.get(e.tier, 0) + 1
        by_category[e.category] = by_category.get(e.category, 0) + 1
    from .. import taxonomy as T
    by_domain: dict[str, int] = {}
    by_harm: dict[str, int] = {}
    for e in ALL_ENTRIES:
        by_domain[e.resolved_domain] = by_domain.get(e.resolved_domain, 0) + 1
        by_harm[e.resolved_harm] = by_harm.get(e.resolved_harm, 0) + 1
    return {"version": VERSION, "fingerprint": fingerprint()[:16],
            "entries": len(ALL_ENTRIES), "active_entries": len(active_entries()),
            "gated_entries": len([e for e in ALL_ENTRIES if e.gated]),
            "gate_open": T.gate_open(),
            "by_domain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])),
            "by_harm": dict(sorted(by_harm.items(), key=lambda kv: -kv[1])),
            "domain_markers_no_harm": by_harm.get("none", 0),
            "by_tier": by_tier,
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "languages": sorted({e.lang for e in ALL_ENTRIES}),
            "retired_from_v1": len(RETIRED)}


# ═════════════════════════════════════════════════════════════════════════════
# v3 — domains beyond the far right
# ═════════════════════════════════════════════════════════════════════════════
#
# Everything above was built for one ideological family. These blocks extend the
# lexicon across the rest of the taxonomy, and the three-axis model does most of
# the work: the majority of what follows are DOMAIN MARKERS with harm="none" —
# they say what a piece of content is, and say nothing about whether it is a
# finding. Only the entries that carry an explicit harm axis can contribute to
# one, and even then only in a promoting, glorifying or recruiting stance.

# ─── far left: domain markers (harm = none) ──────────────────────────────────
#
# None of these is a finding. Marxism, Maoism and anarchism are lawful political
# traditions with sitting parliamentary parties across the EU and standing
# literatures in every university. They are here so that a cluster can be
# DESCRIBED accurately — and so the model has a correct place to put ordinary
# left-wing content instead of being forced to call it extremism or nothing.

FAR_LEFT_DOMAIN: list[Entry] = [
    Entry("ml-vocabulary",
          r"\b(?:marxist[\s-]?leninist|dialectical\s*materialism|vanguard\s*party"
          r"|dictatorship\s*of\s*the\s*proletariat|democratic\s*centralism)\b",
          "C", "political", "Marxist-Leninist theoretical vocabulary. A domain "
          "marker and nothing more — this is the language of a political "
          "tradition, not of a harm.",
          domain="marxist_leninist", harm="none"),
    Entry("maoist-vocabulary",
          r"\b(?:maois[tm]|mao\s*zedong|protracted\s*people.?s\s*war"
          r"|mass\s*line|cultural\s*revolution|gonzalo\s*thought)\b",
          "C", "political", "Maoist theoretical vocabulary. Domain marker only. "
          "Maoism is taught, published and organised lawfully; a finding needs "
          "the glorification-of-violence axis, not this entry.",
          domain="maoist", harm="none"),
    Entry("anarchist-vocabulary",
          r"\b(?:anarcho[\s-]?(?:communis|syndicalis|primitivis)\w*|mutual\s*aid"
          r"|autonomen|antifa|black\s*bloc|acab|a\.c\.a\.b)\b",
          "C", "political", "Anarchist and antifascist vocabulary. Domain marker. "
          "ACAB and black bloc are protest vocabulary in extremely wide ordinary "
          "use and carry no harm axis on their own.",
          domain="insurrectionary_anarchist", harm="none"),
    Entry("left-slogans",
          r"\b(?:eat\s*the\s*rich|no\s*war\s*but\s*class\s*war|seize\s*the\s*means"
          r"|abolish\s*(?:the\s*)?(?:police|prisons?)|land\s*back)\b",
          "C", "political", "Left political slogans, most of them mainstream "
          "internet register. Domain markers; 'eat the rich' in particular is a "
          "pop-culture phrase, not advocacy.",
          domain="marxist_leninist", harm="none"),
]

# ─── far left: the harm axis ─────────────────────────────────────────────────
# These DO carry a harm. The distinction is advocacy of violence, and it is the
# same test applied to the right-wing entries.

FAR_LEFT_HARM: list[Entry] = [
    Entry("armed-group-glorification",
          r"\b(?:shining\s*path|sendero\s*luminoso|naxalite|red\s*army\s*faction"
          r"|baader[\s-]?meinhof|red\s*brigades?|brigate\s*rosse|tupamaros)\b",
          "B", "political",
          "Named left-wing armed organisations. Tier B because historical and "
          "scholarly reference is common — the harm axis applies only where the "
          "stance is glorifying, exactly as with the right-wing group entries.",
          domain="insurrectionary_anarchist", harm="glorification_of_violence",
          lang="mul"),
    Entry("revolutionary-violence",
          r"\b(?:first\s*against\s*the\s*wall|up\s*against\s*the\s*wall"
          r"|when\s*the\s*guillotines?|guillotine\s*the\s*\w+|bring\s*out\s*the\s*guillotine"
          r"|kill\s*(?:all\s*)?(?:landlords|the\s*rich|billionaires))\b",
          "B", "political",
          "Revolutionary violence imagery. Tier B and genuinely contested: much "
          "of this is hyperbolic online register rather than advocacy, which is "
          "precisely why it needs adjudication rather than a keyword verdict.",
          domain="accelerationist_lw", harm="glorification_of_violence"),
    Entry("peoples-war-advocacy",
          r"\b(?:initiate\s*(?:the\s*)?people.?s\s*war|armed\s*struggle\s*is"
          r"|only\s*(?:through\s*)?armed\s*(?:struggle|revolution))\b",
          "B", "political",
          "Advocacy of armed insurgency, as distinct from describing Maoist "
          "doctrine. The verb is what moves this onto the harm axis.",
          domain="maoist", harm="incitement_to_violence"),
]

# ─── jihadist ────────────────────────────────────────────────────────────────
#
# CARE REQUIRED, for the same reason the \bjew\b entry needs care and then some.
# Kuffar, jihad, khilafah, shahid, ummah and dawlah are ORDINARY ARABIC AND
# ISLAMIC VOCABULARY used daily by roughly two billion people. A lexicon that
# flags them builds anti-Muslim bias directly into the instrument and would
# produce a corpus in which ordinary religious speech is recorded as extremism.
#
# So the entries below are restricted to ORGANISATIONAL markers — specific
# proscribed-group slogans, media brands and claimed affiliation — and not to
# religious vocabulary. That is the same discipline applied throughout: name the
# conduct, never the community.

JIHADIST: list[Entry] = [
    Entry("is-slogans",
          r"\bbaqiyah(?:\s*wa\s*tatamadad)?\b|\bremaining\s*and\s*expanding\b"
          r"|\bamaq\s*(?:agency|news)\b|\bal[\s-]?hayat\s*media\b|\brumiyah\b",
          "A", "jihadist",
          "Islamic State organisational slogans and media brands. These identify "
          "the organisation's own output, not a religion.",
          domain="jihadist", harm="terrorist_org_support", lang="mul"),
    Entry("proscribed-orgs",
          r"\b(?:al[\s-]?qaeda|aqap|aqim|boko\s*haram|al[\s-]?shabaab"
          r"|hayat\s*tahrir|jabhat\s*al[\s-]?nusra|tehrik[\s-]?i[\s-]?taliban)\b",
          "B", "jihadist",
          "Named proscribed organisations. Tier B because news reporting and "
          "scholarship name them constantly — support requires the stance axis.",
          domain="jihadist", harm="terrorist_org_support", lang="mul"),
    Entry("attack-glorification",
          r"\b(?:lone\s*wolf\s*(?:attack|op)|inghimasi|istishhadi)\b"
          r"|\bmay\s*allah\s*accept\s*(?:him|her|them)\s*(?:as\s*a\s*)?shahid\b",
          "B", "jihadist",
          "Operational vocabulary and martyrdom praise applied to an attacker. "
          "Note the narrowness: 'shahid' alone is ordinary religious language and "
          "is deliberately NOT matched.",
          domain="jihadist", harm="glorification_of_violence", lang="mul"),
]

# ─── manosphere, beyond the existing incel entry ─────────────────────────────

MANOSPHERE: list[Entry] = [
    Entry("manosphere-vocabulary",
          r"\b(?:hypergamy|alpha\s*widow|briffault|awalt|smv\s*(?:crash|peak)"
          r"|female\s*nature|plate\s*spinning|red\s*pill\s*truth)\b",
          "C", "incel", "Manosphere theoretical vocabulary. Domain marker.",
          domain="manosphere_incel", harm="none"),
    Entry("misogynist-degradation",
          r"\b(?:all\s*women\s*are\s*(?:like\s*that|whores)|women\s*are\s*(?:property|objects)"
          r"|she\s*deserved\s*it|rape\s*is\s*(?:not\s*)?(?:a\s*)?compliment)\b",
          "B", "incel", "Degradation and denial of women's personhood.",
          domain="manosphere_incel", harm="identity_denigration"),
]

# ═════════════════════════════════════════════════════════════════════════════
# GATED — GDPR Art. 9 special-category health data
# ═════════════════════════════════════════════════════════════════════════════
#
# These entries are excluded from every scan unless RM_DPIA_REF is set (see
# radicalmap/taxonomy.py). Three design rules apply to this block specifically:
#
#   1. Detect COMMUNITY MEMBERSHIP and PROMOTION, never methods. Nothing here
#      describes how to do anything, because a research lexicon that doubles as
#      an instruction set is not a research lexicon.
#
#   2. Recovery and help-seeking share almost all of this vocabulary. "Days
#      clean", "ED recovery" and "I'm struggling" are the SAME words as the
#      pro-content. Every entry here is tier B or C and none can be a finding
#      without the stance axis saying promoting or glorifying.
#
#   3. A person disclosing their own distress is never a finding. The
#      help_seeking stance exists for exactly this, and treating a disclosure as
#      a harm record would be both wrong and harmful.

SELF_HARM_GATED: list[Entry] = [
    Entry("sh-community",
          r"\b(?:sh\s*(?:twt|community|blog)|selfharm(?:blog|community)?"
          r"|vent\s*account|#?sh_?tw(?:t|itter))\b",
          "B", "self_harm",
          "Self-harm community identifiers. Membership marker; stance decides.",
          domain="self_harm", harm="none"),
    Entry("moderation-evasion-suicide",
          r"\b(?:sewerslide|unalive|self[\s-]?delete|kts|catch\s*the\s*bus)\b",
          "B", "self_harm",
          "Deliberate misspellings used to evade platform detection of "
          "suicide-related terms. The evasion itself is the signal; the stance "
          "may equally be help-seeking or recovery.",
          domain="self_harm", harm="none"),
    Entry("sh-encouragement",
          r"\b(?:do\s*it\s*coward|nobody\s*(?:would\s*)?miss\s*you"
          r"|you\s*should\s*(?:just\s*)?(?:kys|end\s*it)|kys\b)"
          r"|\b(?:go\s*)?(?:kill\s*yourself)\b",
          "B", "self_harm",
          "Encouragement directed at another person. This is the actual harm in "
          "this domain and is distinct from a disclosure. Tier B because 'kys' is "
          "also widespread hyperbole between friends.",
          domain="self_harm", harm="self_harm_promotion"),
    Entry("sh-recovery",
          r"\b(?:days?\s*clean|clean\s*streak|recovery\s*(?:account|blog|era)"
          r"|in\s*recovery|healing\s*journey)\b",
          "C", "self_harm",
          "RECOVERY marker, present specifically so recovery content is "
          "identified as such rather than swept up with the thing it is "
          "recovering from.",
          domain="self_harm", harm="none", stance_hint="recovery"),
]

EATING_DISORDER_GATED: list[Entry] = [
    Entry("ed-community",
          r"\b(?:ed\s*twt|edtwt|ana\s*buddy|mia\s*buddy|pro[\s-]?ana|pro[\s-]?mia"
          r"|thinspo|bonespo|meanspo|skinnytok)\b",
          "B", "eating_disorder",
          "Pro-eating-disorder community identifiers. 'Ana' and 'Mia' are the "
          "community's personifications of anorexia and bulimia.",
          domain="eating_disorder", harm="disordered_eating_promotion"),
    Entry("ed-metrics",
          r"\b(?:u?gw\s*[:=]?\s*\d{2,3}|cw\s*[:=]?\s*\d{2,3}|hw\s*[:=]?\s*\d{2,3}"
          r"|lw\s*[:=]?\s*\d{2,3}|goal\s*weight|body\s*check)\b",
          "B", "eating_disorder",
          "Goal/current/highest/lowest weight notation and body-checking. The "
          "numeric shorthand is the community's signature register.",
          domain="eating_disorder", harm="disordered_eating_promotion"),
    Entry("ed-restriction",
          r"\b(?:safe\s*foods?|fasting\s*day|restricting|intake\s*today"
          r"|water\s*fast|dry\s*fast)\b",
          "C", "eating_disorder",
          "Restriction vocabulary. Deliberately tier C: every one of these terms "
          "has a large ordinary-dieting and medical usage.",
          domain="eating_disorder", harm="none"),
    Entry("ed-recovery",
          r"\b(?:ed\s*recovery|recovering\s*from\s*(?:ana|an\s*ed|anorexia|bulimia)"
          r"|meal\s*support|anti[\s-]?restriction|fearfood\s*challenge)\b",
          "C", "eating_disorder",
          "RECOVERY marker. Same vocabulary, opposite stance — present so "
          "recovery content is recognisable rather than misfiled.",
          domain="eating_disorder", harm="none", stance_hint="recovery"),
]

NVE_GATED: list[Entry] = [
    Entry("nve-networks",
          r"\b(?:764|cvlt|kaskar|harm\s*nation|no\s*lives\s*matter|nlm\b"
          r"|order\s*of\s*nine\s*angles|o9a)\b",
          "B", "nihilistic_violent_extremism",
          "Named nihilistic violent extremist networks. These groups coerce "
          "minors into self-harm and produce material from it; they sit across "
          "the extremism and self-directed-harm boundary, which is why this "
          "domain is gated with the health-data ones. '764' is also a plain "
          "number, hence tier B.",
          domain="nihilistic_violent_extremism", harm="minor_targeting"),
]

V3_ENTRIES: list[Entry] = (FAR_LEFT_DOMAIN + FAR_LEFT_HARM + JIHADIST + MANOSPHERE
                           + SELF_HARM_GATED + EATING_DISORDER_GATED + NVE_GATED)

ALL_ENTRIES = ALL_ENTRIES + V3_ENTRIES
BY_SLUG = {e.slug: e for e in ALL_ENTRIES}
TIER_A = [e for e in ALL_ENTRIES if e.tier == "A"]
TIER_B = [e for e in ALL_ENTRIES if e.tier == "B"]
TIER_C = [e for e in ALL_ENTRIES if e.tier == "C"]


def active_entries() -> list[Entry]:
    """Entries the current governance state permits scanning with.

    The gated block is not merely unreported when the gate is shut — it is never
    executed, so no special-category text is matched, spanned or stored.
    """
    from .. import taxonomy as T
    if T.gate_open():
        return ALL_ENTRIES
    return [e for e in ALL_ENTRIES if not e.gated]
