"""
Comprehensive text analysis for captions, bios, and usernames.
Detects:
  - Pattern matches (flag_patterns.py)
  - Hidden capitalisation codes (StreSS → SS, proHH → HH)
  - Username numeric/keyword codes (1488, 88, 911, waffen, goy, etc.)
  - Missing hashtag dict entries in captions

Run: python -m scraper.analyse_text
Re-analyses everything already in the DB without re-scraping.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import get_connection
from scraper.flag_patterns import flag_comment as flag_text

# ── Hidden capitalisation detector ────────────────────────────────────────────

# Codes to look for embedded in mixed-case words
HIDDEN_CODES = {
    "SS": ("white_nationalist", "high",
           "SS hidden in mixed-case word — deliberate encoding of Schutzstaffel (Nazi paramilitary)"),
    "HH": ("white_nationalist", "high",
           "HH hidden in mixed-case word — 'Heil Hitler' (H=8th letter; HH=88)"),
    "AH": ("white_nationalist", "high",
           "AH hidden in mixed-case word — Adolf Hitler initials"),
}


def detect_hidden_codes(text: str) -> list[tuple[str, str, str, str]]:
    """
    Returns list of (matched_word, code, category, severity) for each
    hidden code found in a mixed-case word.
    Skips all-caps and all-lowercase words to avoid false positives.
    """
    results = []
    for word in re.findall(r"\b[A-Za-z]{3,}\b", text):
        if word.isupper() or word.islower():
            continue  # all-caps abbreviations or normal words — skip
        for code, (cat, sev, _) in HIDDEN_CODES.items():
            if code in word:  # code appears in uppercase within mixed-case word
                results.append((word, code, cat, sev))
    return results


# ── Username signal detector ──────────────────────────────────────────────────

USERNAME_PATTERNS = [
    # Numeric codes embedded in username
    (r"1488",       "white_nationalist", "high",   "1488 in username — explicit neo-Nazi code"),
    (r"(?<!\d)88(?!\d)", "white_nationalist", "high", "88 in username — Heil Hitler (H=8th letter)"),
    (r"(?<!\d)14(?!\d)", "white_nationalist", "medium", "14 in username — '14 Words' neo-Nazi slogan"),
    (r"(?<!\d)18(?!\d)", "white_nationalist", "high", "18 in username — AH (A=1, H=8) = Adolf Hitler"),
    (r"(?<!\d)28(?!\d)", "white_nationalist", "high", "28 in username — BH = Blood & Honour far-right group"),
    (r"(?<!\d)9(?:1{2}|eleven)(?!\d)", "radicalisation", "medium", "911 in username — 9/11 reference, often ironic or anti-establishment"),
    # Keyword fragments
    (r"waffen",     "white_nationalist", "high",   "'waffen' in username — SS Waffen Division reference"),
    (r"vril",       "white_nationalist", "medium", "'vril' in username — esoteric Nazi community identifier"),
    (r"goy",        "antisemitic",       "high",   "'goy/goyim' in username — antisemitic self-identification as non-Jewish to signal in-group membership"),
    (r"aryan",      "white_nationalist", "high",   "'aryan' in username — explicit white nationalist identifier"),
    (r"reich",      "white_nationalist", "high",   "'reich' in username — German for 'empire', neo-Nazi reference"),
    (r"nationalist","white_nationalist", "medium", "'nationalist' in username — self-applied far-right label"),
    (r"foid",       "dehumanisation",    "high",   "'foid' in username — incel dehumanisation term for women"),
    (r"evropa",     "white_nationalist", "high",   "'evropa' in username — Identity Evropa reference"),
    (r"sun(?:shine)?nationalist", "white_nationalist", "high",
     "'sunshinenationalist' — explicit nationalist self-identification"),
]


def analyse_username(username: str) -> tuple[bool, str, str]:
    """Returns (flagged, flag_reason, categories_csv)."""
    u = username.lower()
    reasons, cats = [], []
    for pattern, cat, sev, desc in USERNAME_PATTERNS:
        if re.search(pattern, u, re.IGNORECASE):
            reasons.append(desc)
            cats.append(cat)
    if reasons:
        return True, "; ".join(dict.fromkeys(reasons))[:300], ", ".join(dict.fromkeys(cats))
    return False, "", ""


# ── Caption / bio full-text analysis ─────────────────────────────────────────

def analyse_caption(caption: str) -> tuple[bool, str, str]:
    """
    Combines flag_text patterns + hidden code detection on caption/bio.
    Returns (flagged, flag_reason, categories_csv).
    """
    reasons, cats = [], []

    # Standard pattern matching
    is_flagged, reason, categories, _ = flag_text(caption)
    if is_flagged:
        reasons.append(reason)
        for c in categories.split(", "):
            if c: cats.append(c)

    # Hidden capitalisation codes
    for word, code, cat, sev in detect_hidden_codes(caption):
        reasons.append(f'hidden {code} in "{word}"')
        cats.append(cat)

    # Specific caption-level signals not in comment patterns
    cap_lower = caption.lower()

    # #AH / standalone AH tag = Adolf Hitler
    if re.search(r'(?:^|[\s#])ah(?:[\s#$]|$)', cap_lower):
        reasons.append("#AH — Adolf Hitler initials")
        cats.append("white_nationalist")

    # JBW = Just Be White
    if re.search(r'\bjbw\b', cap_lower):
        reasons.append("JBW — Just Be White (incel/white nationalist)")
        cats.append("dehumanisation")

    # THEY in caps = coded out-group reference
    if re.search(r'\bTHEY\b', caption):  # case-sensitive: only flag all-caps
        reasons.append('THEY in caps — coded reference to perceived elite/Jewish out-group')
        cats.append("conspiracy")

    # "I support Israel / love pride month" = ironic plausible deniability disclaimer
    if re.search(r'i support israel|love pride month|happy black history', cap_lower):
        reasons.append("ironic disclaimer — plausible deniability pattern used to evade moderation")
        cats.append("radicalisation")

    # Beer belly's in revolt — Beer Hall Putsch aesthetic reference
    if re.search(r"beer belly.?s\s+in\s+revolt", cap_lower):
        reasons.append("'Beer belly's in revolt' — aestheticised reference to Beer Hall Putsch / SA")
        cats.append("white_nationalist")

    # Glad midsommar + Swedish flag = Nordic nationalist signal
    if re.search(r'midsommar|midsummer', cap_lower) and '🇸🇪' in caption:
        reasons.append("Midsommar + Swedish flag — Nordic nationalist identity signal")
        cats.append("white_nationalist")

    # ── Multilingual bio/caption signals ──────────────────────────────────────

    # Swedish: Great Replacement theory / forced repatriation
    if re.search(r'folkutbyte[t]?|folkutbytning', cap_lower):
        reasons.append("'Folkutbyte' (Swedish: 'population replacement') — Great Replacement theory")
        cats.append("conspiracy")
    if re.search(r'återvandring', cap_lower):
        reasons.append("'Återvandring' (Swedish: 'repatriation/remigration') — far-right forced deportation policy slogan")
        cats.append("white_nationalist")
    if re.search(r'alternativ\s+f[öo]r\s+sverige', cap_lower):
        reasons.append("'Alternativ för Sverige' — Swedish far-right nationalist party (AfS)")
        cats.append("white_nationalist")
    if re.search(r'stoppa\s+folkutbyte', cap_lower):
        reasons.append("'Stoppa Folkutbytet' (Swedish: 'Stop the population replacement') — Great Replacement slogan")
        cats.append("conspiracy")

    # Spanish fascist slogans
    if re.search(r'patria[,\s]+sangre', cap_lower):
        reasons.append("'Patria, Sangre, Victoria' — Spanish fascist slogan (Fatherland, Blood, Victory)")
        cats.append("white_nationalist")
    if re.search(r'\bnacionalista\b', cap_lower):
        reasons.append("'Nacionalista' (Spanish: 'Nationalist') — self-applied far-right label")
        cats.append("white_nationalist")

    # "88" as standalone number in bio (Heil Hitler) — distinct from "88%" used as cover
    if re.search(r'(?:^|[\s☦️🌲🏴⚡✝])88(?:[\s☦️🌲🏴⚡✝%]|$)', caption):
        reasons.append("88 in bio — HH (Heil Hitler), H=8th letter")
        cats.append("white_nationalist")
    # 14 standalone in bio = 14 Words neo-Nazi slogan
    if re.search(r'(?:^|[\s☦️🌲🏴⚡✝])14(?:[\s☦️🌲🏴⚡✝]|$)', caption):
        reasons.append("14 in bio — '14 Words' neo-Nazi slogan")
        cats.append("white_nationalist")

    # "Vitalist" — biological vitalism in far-right context
    if re.search(r'\bvitalist\b', cap_lower):
        reasons.append("'Vitalist' — far-right biological vitalism; rejection of modernity, embrace of racial/physical purity")
        cats.append("white_nationalist")

    # "Ministry of Propaganda" — ironic self-naming as propagandist
    if re.search(r'ministry\s+of\s+propaganda', cap_lower):
        reasons.append("'Ministry of Propaganda' — ironic self-identification as propagandist")
        cats.append("radicalisation")

    # Gaddafi admiration — anti-Western/anti-Zionist strongman cult
    if re.search(r'\bgaddafi\b', cap_lower):
        reasons.append("Gaddafi admiration — anti-Western/anti-Zionist strongman cult common in far-right and anti-globalist circles")
        cats.append("radicalisation")

    # "All satire" / "(satire)" cover in bio = troll persona
    if re.search(r'\(all\s+satire\)|\ball\s+satire\b', cap_lower):
        reasons.append("'All satire' cover disclaimer — troll persona providing plausible deniability for extremist content")
        cats.append("radicalisation")

    # "3rd account" / "Xth account" = banned multiple times
    if re.search(r'\b(\d+(?:rd|th|nd|st)|third|fourth|fifth)\s+account\b', cap_lower):
        reasons.append("Multiple-account marker — indicates prior bans for policy violations")
        cats.append("radicalisation")

    # "Banned at Xk" = explicitly banned for extremist content
    if re.search(r'banned\s+at\s+\d+k', cap_lower):
        reasons.append("Previous ban noted in bio — account has been removed for content violations")
        cats.append("radicalisation")

    # "Nationalist" in bio
    if re.search(r'\bnationalist\b', cap_lower):
        reasons.append("'Nationalist' self-label in bio")
        cats.append("white_nationalist")

    if reasons:
        return True, "; ".join(dict.fromkeys(reasons))[:600], ", ".join(dict.fromkeys(cats))
    return False, "", ""


# ── DB migration: add new columns if missing ─────────────────────────────────

def migrate_db():
    conn = get_connection()
    cur = conn.cursor()
    for col, defn in [
        ("caption_flagged",      "INTEGER DEFAULT 0"),
        ("caption_flag_reason",  "TEXT DEFAULT ''"),
        ("caption_flag_categories", "TEXT DEFAULT ''"),
        ("username_flagged",     "INTEGER DEFAULT 0"),
        ("username_flag_reason", "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute(f"ALTER TABLE videos ADD COLUMN {col} {defn}")
        except Exception:
            pass  # column already exists

    for col, defn in [
        ("bio_flagged",          "INTEGER DEFAULT 0"),
        ("bio_flag_reason",      "TEXT DEFAULT ''"),
        ("bio_flag_categories",  "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute(f"ALTER TABLE accounts ADD COLUMN {col} {defn}")
        except Exception:
            pass

    conn.commit()
    conn.close()


# ── Main re-analysis pass ─────────────────────────────────────────────────────

def run():
    migrate_db()
    conn = get_connection()

    # ── Videos: caption + username ─────────────────────────────
    videos = conn.execute("SELECT id, username, caption FROM videos").fetchall()
    cap_flagged = user_flagged = 0

    for v in videos:
        vid_id   = v["id"]
        username = v["username"] or ""
        caption  = v["caption"] or ""

        c_flag, c_reason, c_cats = analyse_caption(caption)
        u_flag, u_reason, _      = analyse_username(username)

        conn.execute(
            """UPDATE videos
               SET caption_flagged=?, caption_flag_reason=?, caption_flag_categories=?,
                   username_flagged=?, username_flag_reason=?
               WHERE id=?""",
            (int(c_flag), c_reason, c_cats, int(u_flag), u_reason, vid_id),
        )
        if c_flag: cap_flagged += 1
        if u_flag: user_flagged += 1

    # ── Accounts: bio ──────────────────────────────────────────
    accounts = conn.execute("SELECT username, bio FROM accounts").fetchall()
    bio_flagged = 0

    for a in accounts:
        bio = a["bio"] or ""
        if not bio:
            continue
        b_flag, b_reason, b_cats = analyse_caption(bio)  # same logic as caption
        conn.execute(
            """UPDATE accounts
               SET bio_flagged=?, bio_flag_reason=?, bio_flag_categories=?
               WHERE username=?""",
            (int(b_flag), b_reason, b_cats, a["username"]),
        )
        if b_flag: bio_flagged += 1

    conn.commit()
    conn.close()

    print(f"Caption analysis: {cap_flagged}/{len(videos)} videos flagged")
    print(f"Username analysis: {user_flagged}/{len(videos)} usernames flagged")
    print(f"Bio analysis: {bio_flagged} bios flagged")

    # Print findings
    conn = get_connection()
    print("\n── Caption flags ──")
    for r in conn.execute("SELECT username, caption_flag_reason, caption_flag_categories FROM videos WHERE caption_flagged=1"):
        print(f"  @{r['username']}: {r['caption_flag_categories']}")
        print(f"    {r['caption_flag_reason'][:120]}")
    print("\n── Username flags ──")
    for r in conn.execute("SELECT username, username_flag_reason FROM videos WHERE username_flagged=1 GROUP BY username"):
        print(f"  @{r['username']}: {r['username_flag_reason'][:120]}")
    conn.close()


if __name__ == "__main__":
    run()
