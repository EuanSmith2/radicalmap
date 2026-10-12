"""Link and tag extraction from free text.

This module exists because of a specific v1 defect. The README advertises
cross-platform network mapping, `external_links` is a node type in the graph,
the legend has an entry for it — and the table has 0 rows. The cause: the
Telegram regex in manual_ingest.py scanned bios and captions only, never comment
text, while video 1's own caption reads "telegram group in comments!".

So extraction here runs over every text surface — caption, bio, comment,
transcript, description — and records which surface each link came from, so the
coverage gap is visible in the data rather than invisible in the code.

It also handles deliberate obfuscation. Accounts that post recruitment links
know the platform scans for them, so they write "t dot me slash x" or "t(.)me/x".
A plain URL regex sees none of that, and the links it misses are exactly the
ones that matter most — the ones someone took trouble to hide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

# ── real URLs ────────────────────────────────────────────────────────────────

URL_RE = re.compile(
    r"""(?xi)
    \b (?: https?://  | www\. )
    [^\s<>"'\]\)},;]+
    """)

# Bare domain mentions for the platforms we care about, without a scheme.
BARE_RE = re.compile(
    r"""(?xi)\b(
        t\.me | telegram\.(?:me|dog) | discord\.(?:gg|com/invite)
      | gab\.com | odysee\.com | rumble\.com | bitchute\.com | minds\.com
      | truthsocial\.com | gettr\.com | vk\.com | substack\.com | 4chan\.org
      | kiwifarms\.\w+ | poal\.co | bitchute\.\w+
    )/\S+""")

# ── obfuscated forms ─────────────────────────────────────────────────────────
# Written the way people actually write them to dodge automated scanning.
# Each yields (platform, handle_or_path).

_SEP = r"(?:\s*(?:\.|\(\s*\.\s*\)|\[\s*\.\s*\]|\{\s*\.\s*\}|,|\s+dot\s+)\s*)"
_SLASH = r"(?:\s*(?:/|\\|\s+slash\s+|\s+\|\s*)\s*)"

OBFUSCATED = [
    ("telegram", re.compile(
        rf"(?xi)\bt{_SEP}me{_SLASH}(?:joinchat/)?([A-Za-z0-9_+\-]{{3,64}})")),
    ("telegram", re.compile(
        rf"(?xi)\btelegram{_SEP}(?:me|dog){_SLASH}([A-Za-z0-9_+\-]{{3,64}})")),
    # "telegram: @handle" / "tg @handle" — a channel pointer with no URL at all.
    ("telegram", re.compile(
        r"(?xi)\b(?:telegram|tele|tg)\s*[:\-–]?\s*@\s*([A-Za-z0-9_]{5,32})\b")),
    ("discord", re.compile(
        rf"(?xi)\bdiscord{_SEP}gg{_SLASH}([A-Za-z0-9\-]{{3,32}})")),
    ("session", re.compile(
        r"(?xi)\bsession\s*(?:id)?\s*[:\-–]?\s*\b(05[0-9a-f]{64})\b")),
]

PLATFORM_BY_HOST = {
    "t.me": "telegram", "telegram.me": "telegram", "telegram.dog": "telegram",
    "discord.gg": "discord", "discord.com": "discord", "discordapp.com": "discord",
    "gab.com": "gab", "odysee.com": "odysee", "rumble.com": "rumble",
    "bitchute.com": "bitchute", "minds.com": "minds", "gettr.com": "gettr",
    "truthsocial.com": "truthsocial", "vk.com": "vk", "4chan.org": "4chan",
    "youtube.com": "youtube", "youtu.be": "youtube", "instagram.com": "instagram",
    "x.com": "twitter", "twitter.com": "twitter", "substack.com": "substack",
    "tiktok.com": "tiktok",
}

TAG_RE = re.compile(r"#([\wÀ-ɏЀ-ӿ]{1,64})", re.UNICODE)


@dataclass(frozen=True)
class ExtractedLink:
    url: str
    normalised_url: str
    domain: str
    platform: str
    context: str
    obfuscated: bool = False


def _domain(url: str) -> str:
    netloc = urlsplit(url).netloc.lower()
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    netloc = netloc.split(":", 1)[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def platform_of(url: str) -> str:
    d = _domain(url)
    if d in PLATFORM_BY_HOST:
        return PLATFORM_BY_HOST[d]
    # Match a registrable suffix so link.t.me and discord.com/invite both land.
    parts = d.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in PLATFORM_BY_HOST:
            return PLATFORM_BY_HOST[cand]
    return "other"


def normalise_url(url: str) -> str:
    """Strip tracking noise so the same destination collapses to one row."""
    if url.startswith("www."):
        url = "https://" + url
    parts = urlsplit(url)
    query = "&".join(
        kv for kv in parts.query.split("&")
        if kv and not kv.split("=")[0].lower().startswith(("utm_", "fbclid", "igshid", "_ga")))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(),
                       path, query, ""))


def _context(text: str, start: int, end: int, width: int = 60) -> str:
    return " ".join(text[max(0, start - width):min(len(text), end + width)].split())


def extract_links(text: str | None) -> list[ExtractedLink]:
    """Every link-shaped thing in one piece of text, obfuscated forms included."""
    if not text:
        return []
    found: dict[str, ExtractedLink] = {}

    for m in URL_RE.finditer(text):
        raw = m.group(0).rstrip(".,;:!?")
        norm = normalise_url(raw)
        found.setdefault(norm, ExtractedLink(
            raw, norm, _domain(norm), platform_of(norm), _context(text, *m.span())))

    for m in BARE_RE.finditer(text):
        raw = m.group(0).rstrip(".,;:!?")
        norm = normalise_url("https://" + raw)
        found.setdefault(norm, ExtractedLink(
            raw, norm, _domain(norm), platform_of(norm), _context(text, *m.span())))

    for platform, pattern in OBFUSCATED:
        for m in pattern.finditer(text):
            handle = m.group(1)
            if platform == "telegram":
                norm = normalise_url(f"https://t.me/{handle}")
            elif platform == "discord":
                norm = normalise_url(f"https://discord.gg/{handle}")
            else:
                norm = f"{platform}:{handle}"
            # An obfuscated hit never overwrites a clean one, but it does fill a
            # gap the URL regex left — which is the whole point.
            if norm not in found:
                found[norm] = ExtractedLink(
                    m.group(0).strip(), norm,
                    _domain(norm) if norm.startswith("http") else platform,
                    platform, _context(text, *m.span()), obfuscated=True)
    return list(found.values())


def extract_tags(text: str | None) -> list[str]:
    if not text:
        return []
    seen, out = set(), []
    for m in TAG_RE.finditer(text):
        tag = m.group(1)
        # Numeric tags are deliberately KEPT. The obvious instinct is to drop
        # them as noise (#2026, #2024), but the numeric dog whistles are the
        # single most diagnostic tag class in this corpus — 1488, 14, 88, 18,
        # 28 are all in the project's own dictionary. Discarding every numeric
        # tag to avoid a few year strings would throw away exactly what the
        # tool exists to find. Distinguishing a year from a code is a
        # classification question, not an extraction one.
        if tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out
