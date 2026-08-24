"""
Fill in missing hashtag definitions.

Strategy: Urban Dictionary API for generic/slang tags (top community entry,
cleaned and trimmed). Curated fallbacks for coded/cluster tags where UD has
no entry or where the top entries are written by the in-group itself and
can't be trusted as neutral description.

Run: python -m scraper.fetch_definitions
"""

import sys
import re
import json
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_connection

UD_API = "https://api.urbandictionary.com/v0/define?term={}"
RETRIEVED = "2026-07-09"

# Tags whose UD entries are unreliable (in-group authored) or absent —
# use the curated definition below instead of whatever UD returns.
CURATED = {
    "evropa": {
        "category": "white_nationalist", "severity": "medium",
        "definition": "Latinised spelling of 'Europe' favoured in white-nationalist aesthetics — signals a romanticised pan-European racial identity rather than the geographic continent. Popularised by the US group Identity Evropa (2016–2019).",
        "origin": "Identity Evropa branding; the archaic spelling evokes classical antiquity.",
    },
    "vriltok": {
        "category": "white_nationalist", "severity": "high",
        "definition": "TikTok discovery tag for Vril-themed content — the esoteric-Nazi 'life force' mythology. Connects viewers into the wider #vrilliant network.",
        "origin": "Portmanteau of Vril + TikTok, in use within the Vrilliant cluster since ~2023.",
    },
    "agarthaedit": {
        "category": "conspiracy", "severity": "low",
        "definition": "Video-edit tag for Agartha — the mythical subterranean kingdom of Hollow Earth lore. Aestheticised 'lost world' edits that work as a soft entry point into esoteric conspiracy content.",
        "origin": "Agartha derives from 19th-century occult writing; the edit format is TikTok-native.",
    },
    "faustianspirit": {
        "category": "white_nationalist", "severity": "medium",
        "definition": "References Oswald Spengler's description of Western civilisation as 'Faustian' — endlessly striving and expansionist. Adopted by the online far right to romanticise European conquest and destiny.",
        "origin": "Spengler's 'The Decline of the West' (1918–22); revived by online right subcultures.",
    },
    "ah": {
        "category": "white_nationalist", "severity": "medium",
        "definition": "Two-letter initialism read within this cluster as 'Adolf Hitler'. Deliberately ambiguous — innocuous-looking to outsiders, decoded by the in-group. Classification is contextual; the letters are common in unrelated content.",
        "origin": "Initialism convention alongside numeric codes like 88 (HH).",
    },
    "irynazarutska": {
        "category": "radicalisation", "severity": "medium",
        "definition": "Refers to Iryna Zarutska, a Ukrainian refugee killed on Charlotte's light rail in August 2025. Her death was heavily amplified by far-right accounts as 'crime noticing' content to advance racial narratives.",
        "origin": "News event, August 2025; repurposed as an outrage/martyr meme in right-wing short-form content.",
    },
    "rawmilk": {
        "category": "conspiracy", "severity": "low",
        "definition": "Raw (unpasteurised) milk as a lifestyle signal — distrust of food regulation, 'natural order' masculinity, and trad aesthetics. A recurring marker on the crunchy-to-far-right pipeline.",
        "origin": "Trad/wellness TikTok; overlaps with anti-institutional conspiracy culture.",
    },
    "white": {
        "category": "radicalisation", "severity": "low",
        "definition": "Single-word racial identifier. Innocuous in most contexts; inside this cluster it functions as an identity tag alongside #europe and #culture.",
        "origin": "Generic word — the surrounding cluster supplies the meaning.",
    },
    "nationalist": {
        "category": "radicalisation", "severity": "low",
        "definition": "Political self-identifier one step short of explicit ethnonationalism; commonly used as a gateway tag that reads as mainstream patriotism.",
        "origin": "Generic political vocabulary, repurposed as an adjacency tag.",
    },
    "satire": {
        "category": "uncategorised", "severity": "low",
        "definition": "Disclaimer tag used as an irony shield — framing extreme content as 'just a joke' to keep plausible deniability and dodge moderation.",
        "origin": "Standard moderation-evasion convention across platforms.",
    },
    "fördig": {
        "category": "uncategorised", "severity": "low",
        "definition": "Swedish for 'for you' — the Swedish-language equivalent of #fyp, aimed at the Swedish For You feed.",
        "origin": "Localised TikTok reach tag.",
    },
    "fypシ": {
        "category": "uncategorised", "severity": "low",
        "definition": "#fyp with the katakana 'シ' appended (often read as a smiley). Reach-tag variant believed by users to dodge tag-level suppression.",
        "origin": "TikTok folk convention; the katakana renders as a sideways smile.",
    },
    "henrynowak": {
        "category": "uncategorised", "severity": "low",
        "definition": "Personal-name tag appearing in this cluster; function unverified — possibly a creator, meme figure, or referenced individual.",
        "origin": "Unresolved — flagged for manual review.",
    },
    "crime": {
        "category": "uncategorised", "severity": "low",
        "definition": "Generic topical tag; in this cluster it is attached to selectively framed crime content feeding 'noticing' narratives about race and crime.",
        "origin": "Generic word — cluster context supplies the function.",
    },
    "leftisttiktok": {
        "category": "uncategorised", "severity": "low",
        "definition": "Opposing-community tag — used here either to bait cross-audience engagement or to mock left-leaning TikTok.",
        "origin": "Community-boundary tag; usage is adversarial rather than affiliative.",
    },
}

# Fallback stubs for generic tags if Urban Dictionary returns nothing usable.
GENERIC_FALLBACK = {
    "fyp":       "Reach tag aimed at TikTok's For You feed. Extremist posters stack reach tags to push coded content into general audiences.",
    "foryou":    "Reach tag aimed at TikTok's For You feed — same function as #fyp.",
    "fypp":      "Misspelt/variant reach tag for the For You feed; variants are believed to dodge tag-level suppression.",
    "edit":      "Fan-edit format tag — rapid cuts synced to music. The aesthetic vehicle of choice for this cluster's recruitment content.",
    "viral":     "Generic reach tag requesting algorithmic distribution.",
    "energy":    "Vibes tag attached to mood/aesthetic edits.",
    "brutal":    "Edit-culture superlative — signals a 'hard-hitting' edit.",
    "anotherclassic": "Recurring in-joke framing ('another classic') signalling in-group familiarity with a repeated format.",
    "culture":   "Generic topical tag; in-cluster it frames content as innocuous heritage commentary.",
    "history":   "Generic topical tag; in-cluster it frames content as innocuous history commentary.",
    "europe":    "Geographic tag; in-cluster shorthand for a romanticised European identity.",
    "america":   "Geographic tag; used for US-focused variants of the same content.",
    "florida":   "US state tag, usually attached to crime or 'Florida man' style content.",
    "conservative": "Mainstream political self-identifier; adjacency tag broadening reach into right-leaning audiences.",
    "totallyjoyfulday": "Ironic feel-good tag; in-cluster usage appears to be tone-masking for darker content. Unverified.",
}

BLOCKLIST = re.compile(
    r"\b(kike|nigg|faggot|tranny|spic|chink|raghead|holohoax)\w*", re.IGNORECASE
)


def fetch_ud(term: str) -> str | None:
    """Top Urban Dictionary definition, cleaned, or None if unusable."""
    url = UD_API.format(urllib.parse.quote(term))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
    except Exception:
        return None
    entries = data.get("list") or []
    if not entries:
        return None
    d = entries[0].get("definition", "")
    d = d.replace("[", "").replace("]", "")            # UD cross-link syntax
    d = re.sub(r"\s+", " ", d).strip()
    if len(d) < 30 or BLOCKLIST.search(d):
        return None
    if len(d) > 300:                                    # trim at sentence boundary
        cut = d[:300]
        if "." in cut:
            cut = cut[: cut.rfind(".") + 1]
        d = cut
    return d


def run():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, tag FROM hashtags WHERE definition IS NULL OR definition=''"
    ).fetchall()
    print(f"{len(rows)} hashtags missing definitions")

    for row in rows:
        tag = row["tag"]

        if tag.isdigit():   # ingest artifact — placeholder captions parsed as tags
            conn.execute("DELETE FROM video_hashtags WHERE hashtag_id=?", (row["id"],))
            conn.execute("DELETE FROM hashtag_cooccurrence WHERE hashtag_a_id=? OR hashtag_b_id=?", (row["id"], row["id"]))
            conn.execute("DELETE FROM hashtags WHERE id=?", (row["id"],))
            print(f"  [x] #{tag} — deleted (numeric ingest artifact)")
            continue

        if tag in CURATED:
            c = CURATED[tag]
            conn.execute(
                "UPDATE hashtags SET definition=?, origin=?, category=?, severity=? WHERE id=?",
                (c["definition"], c["origin"], c["category"], c["severity"], row["id"]),
            )
            print(f"  [c] #{tag} — curated ({c['category']}/{c['severity']})")
            continue

        ud = fetch_ud(tag)
        if ud:
            conn.execute(
                "UPDATE hashtags SET definition=?, origin=? WHERE id=?",
                (ud, f"Urban Dictionary — top community entry (retrieved {RETRIEVED}).", row["id"]),
            )
            print(f"  [ud] #{tag}")
        elif tag in GENERIC_FALLBACK:
            conn.execute(
                "UPDATE hashtags SET definition=?, origin=? WHERE id=?",
                (GENERIC_FALLBACK[tag], "Curated stub — no usable Urban Dictionary entry.", row["id"]),
            )
            print(f"  [f] #{tag} — fallback stub")
        else:
            print(f"  [!] #{tag} — nothing usable, left empty")
        time.sleep(0.4)

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    run()
