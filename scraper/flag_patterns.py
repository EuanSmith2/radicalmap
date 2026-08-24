"""
Shared comment flagging patterns for all ingest/import scripts.
Each entry: (regex, category, severity)
"""

import re

COMMENT_FLAG_PATTERNS = [
    # ── Explicit neo-Nazi codes and slogans ───────────────────────────────────
    (r"\b(1488|14\s*words|heil|fuhrer|reich|ss\s*uniform|waffen)\b",               "white_nationalist", "high"),
    # "Sik Haj" = Swedish phonetic Sieg Heil
    (r"\bsik\s*haj\b",                                                              "white_nationalist", "high"),
    # ⚡⚡ double lightning bolt = SS Blitz runes (high — explicit Nazi symbol)
    (r"⚡\s*⚡|☇\s*☇",                                                              "white_nationalist", "high"),
    # "It's OK to be white" — organised far-right poster campaign
    (r"\bit.?s\s*ok(ay)?\s*to\s*be\s*white\b|iotbw",                              "white_nationalist", "high"),
    # Mussolini's Blackshirts and historic fascist figures
    (r"\b(blackshirts?|mussolini|goebbels|swastika|schutzstaffel)\b",              "white_nationalist", "high"),
    # Esoteric/occult far-right community tags (match word stems to catch variants: Hyperborean, Agarthan)
    (r"\b(vril\w*|vrilliant|hyperborea\w*|aryan|sonnenrad|black\s*sun)\b",         "white_nationalist", "medium"),
    # "Save/Defend/Reclaim Europe" — identitarian slogans
    (r"\b(save\s*europe|defend\s*europe|reclaim\s*europe|europe\s*is\s*ours)\b",   "white_nationalist", "medium"),
    # "anti-white" persecution narrative
    (r"\banti[-\s]?white\b",                                                        "white_nationalist", "medium"),
    # "Urban classic" — "urban" used as coded racial descriptor
    (r"\burban\s+classic\b",                                                        "white_nationalist", "medium"),
    # Replacement / genocide conspiracy
    (r"\b(white\s*genocide|great\s*replacement|kalergi|replacement\s*theory)\b",   "conspiracy",        "high"),

    # ── Antisemitic ───────────────────────────────────────────────────────────
    (r"\b(jew|kike|zog|goy|goyim|jewry|judeo|rothschild|soros)\b",                "antisemitic",       "high"),
    (r"\b(gas\s*(the|them)|oven|six\s*million|holohoax|hollow?caust\s*didn)\b",    "antisemitic",       "high"),
    # "funny hats" = coded antisemitic reference to kippah
    (r"\bfunny\s+hats?\b",                                                          "antisemitic",       "medium"),
    # "noticing" — euphemism for observing alleged Jewish influence
    (r"\bnoticing\b",                                                               "antisemitic",       "medium"),
    # Dutch/Flemish "ze zeggen het" = "they say it" — equivalent of "noticing"
    (r"\bze\s+zeggen\s+het\b",                                                     "antisemitic",       "medium"),

    # ── Dehumanisation / incel ────────────────────────────────────────────────
    (r"\b(foid|femoid|roastie|incel|blackpill|subhuman|degenerate)\b",             "dehumanisation",    "medium"),

    # ── Accelerationist ───────────────────────────────────────────────────────
    (r"\b(race\s*war|rwds|accelerat|boogaloo|day\s*of\s*the\s*rope|civil\s*war\s*2)\b",
                                                                                    "accelerationist",   "high"),

    # ── Esoteric / occult-fascist ─────────────────────────────────────────────
    # "The sun will rise again" + ⚡⚡ = esoteric National Socialist trope
    (r"\bsun\s*will\s*rise\s*again\b",                                             "esoteric",          "high"),
    # "Slavic secret place" / hidden underground civilisation refs (match Agarthan etc.)
    (r"\b(slavic\s*secret|agartha\w*|hollow\s*earth|inner\s*earth|underground\s*civili)\b",
                                                                                    "esoteric",          "medium"),
    # Hans Landa — SS Obersturmbannführer character, worshipping this figure
    (r"\bhans\s*landa\b",                                                           "white_nationalist", "medium"),

    # ── Conspiracy / hidden-meaning framing ───────────────────────────────────
    (r"\b(great\s*reset|nwo|new\s*world\s*order|globalist|deep\s*state)\b",        "conspiracy",        "low"),
    (r"\b(in\s+plain\s+sight|symbolism|they\s+(hid|put\s+it|always\s+knew))\b",   "conspiracy",        "low"),
    (r"\b(kept\s+a\s+secret|always\s+k[ea]pt|took\s+me\s+\d+\s+years?|years?\s+to\s+(realiz|notic|see))\b",
                                                                                    "conspiracy",        "low"),
    (r"\bcartoons?\s+(always|have\s+been|kept)\b",                                 "conspiracy",        "low"),
    (r"\b(they\s+don.t\s+want|they\s+won.t\s+show|wake\s*up\s+people)\b",         "conspiracy",        "low"),

    # ── Radicalisation pipeline / community endorsement slang ─────────────────
    (r"\b(redpill|red\s*pilled|based\s+and|black\s*pilled)\b",                     "radicalisation",    "low"),
    # "bazed" / "absolute based" / "so based" etc. — far-right endorsement slang
    (r"\b(absolute\s+based|so\s+based|dead\s+based|based\s+af|ultra\s+based|the\s+most\s+ba[sz]ed)\b",
                                                                                    "radicalisation",    "medium"),
    (r"\bba[sz]ed\b",                                                              "radicalisation",    "low"),
    # "risky repost" — "I agree but scared how I'll be seen sharing this"
    (r"\brisky\s*repost\b",                                                         "radicalisation",    "medium"),
    # "instantly to my story" / "added to my story" — amplification/endorsement
    # Note: pattern ends before "y" to avoid word-boundary issue on "story"
    (r"\binstantly\s+to\s+my\s+stor",                                              "radicalisation",    "low"),
    (r"\badded\s+to\s+(my\s+)?stor",                                               "radicalisation",    "low"),
    # "saving the sound/video before it gets deleted" — persecution/censorship narrative
    (r"\bsav(ing|e)\s+(the\s+)?(sound|video|this)\s+before\b",                    "radicalisation",    "low"),
    (r"\bbefore\s+(it.?s?\s+)?deleted\b|\bbefore\s+they\s+(delete|remove)\b",      "radicalisation",    "medium"),
    # "I saw this on insta" — cross-platform amplification
    (r"\bi\s+saw\s+this\s+on\s+(insta|instagram|twitter|x\.com|telegram)\b",       "radicalisation",    "low"),
    # "I miss prime far right TikTok" — explicit community nostalgia
    (r"\b(prime\s+far[-\s]?right|far[-\s]?right\s+tiktok|miss\s+(prime\s+)?far[-\s]?right)\b",
                                                                                    "radicalisation",    "medium"),
    # "white monster" — Monster Energy drink co-opted as white pride symbol
    (r"\bwhite\s+monster\b",                                                        "white_nationalist", "medium"),
    # Recruitment signals
    (r"\b(telegram|t\.me\/|link\s+in\s+(bio|comments?)|join\s+(us|the\s+group|the\s+server))\b",
                                                                                    "radicalisation",    "medium"),
    (r"\b(pv\s+message|pm\s+me.*group|wheres?\s+the\s+group|where.*group)\b",      "radicalisation",    "medium"),
    # "fed post" / "glowie" — accelerationist community slang for entrapment risk
    (r"\b(fed\s*post|glowie|glow\s*in\s*the\s*dark)\b",                           "radicalisation",    "low"),
    # Jan 6 sympathy signals
    (r"\b(jan\s*6|january\s*6th?)\b",                                              "radicalisation",    "low"),
    # "I miss prime far right Tiktok" / community longing
    (r"\bi\s+miss\s+prime\b",                                                      "radicalisation",    "medium"),
]


def flag_comment(text: str) -> tuple[bool, str, str, str]:
    """Returns (flagged, flag_reason, categories_csv, severity)."""
    t = text.lower()
    cats, reasons, max_sev = [], [], "low"
    for pattern, cat, sev in COMMENT_FLAG_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            m = re.search(pattern, t, re.IGNORECASE)
            cats.append(cat)
            reasons.append(m.group(0).strip())
            if sev == "high":
                max_sev = "high"
            elif sev == "medium" and max_sev == "low":
                max_sev = "medium"
    if cats:
        return (
            True,
            ", ".join(dict.fromkeys(reasons))[:200],
            ", ".join(dict.fromkeys(cats)),
            max_sev,
        )
    return False, "", "", "low"
