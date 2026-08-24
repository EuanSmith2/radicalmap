"""Builds vis.js-compatible graph data from the database."""

from db.database import get_graph_data
from analysis.dictionary import load_categories

# Muted archival palette on dark ground — one hue per category
CATEGORY_SHADES = {
    "antisemitic":       {"accent": "#c05038"},
    "white_nationalist": {"accent": "#c9a227"},
    "conspiracy":        {"accent": "#7a94ab"},
    "radicalisation":    {"accent": "#7f9c6a"},
    "dehumanisation":    {"accent": "#a97a99"},
    "esoteric":          {"accent": "#9a8bb8"},
    "incel":             {"accent": "#b3766a"},
    "islamophobic":      {"accent": "#ab8a5f"},
    "accelerationist":   {"accent": "#c07272"},
    "uncategorised":     {"accent": "#6e7069"},
}

NODE_DARK = "#191b17"
INK = "#d8d4c6"


def build_graph() -> dict:
    raw = get_graph_data()
    categories = load_categories()
    nodes = []
    edges = []
    seen_nodes = set()

    # Hashtag nodes — high severity gets a solid fill, the rest outlined
    for h in raw["hashtags"]:
        accent = CATEGORY_SHADES.get(h["category"], CATEGORY_SHADES["uncategorised"])["accent"]
        if h["severity"] == "high":
            color = {"background": accent, "border": accent,
                     "highlight": {"background": accent, "border": INK}}
            font = {"color": "#12130f"}
        else:
            color = {"background": NODE_DARK, "border": accent,
                     "highlight": {"background": "#242620", "border": INK}}
            font = {"color": accent if h["severity"] == "medium" else "#8d8f85"}
        nodes.append({
            "id": f"h_{h['id']}",
            "label": h["display_name"] or f"#{h['tag']}",
            "group": h["category"],
            "type": "hashtag",
            "title": h["definition"],
            "origin": h.get("origin", ""),
            "severity": h["severity"],
            "usage_count": h["usage_count"],
            "color": color,
            "font": font,
            "shape": "diamond",
            "size": 18 + min(h["usage_count"] * 5, 40),
        })
        seen_nodes.add(f"h_{h['id']}")

    # Video nodes
    for v in raw["videos"]:
        flagged = v.get("flagged_comment_count", 0)
        border_color = "#c05038" if flagged > 0 else "#4b4d45"
        border_width = 2.5 if flagged > 0 else 1.5
        nodes.append({
            "id": f"v_{v['id']}",
            "label": f"@{v['username']}",
            "type": "video",
            "db_id": v["id"],
            "tiktok_id": v.get("tiktok_id", ""),
            "caption": v["caption"][:120] + "..." if v["caption"] and len(v["caption"]) > 120 else v["caption"],
            "view_count":    v.get("view_count", 0) or 0,
            "like_count":    v.get("like_count", 0) or 0,
            "comment_count": v.get("comment_count", 0) or 0,
            "share_count":   v.get("share_count", 0) or 0,
            "save_count":    v.get("save_count", 0) or 0,
            "url": v["url"],
            "flagged_comment_count": flagged,
            "has_transcript": v.get("has_transcript", False),
            "color": {"background": "#22241e", "border": border_color,
                      "highlight": {"background": "#2c2e27", "border": INK}},
            "font": {"color": INK},
            "shape": "box",
            "size": 12,
            "borderWidth": border_width,
        })
        seen_nodes.add(f"v_{v['id']}")

    # Video → hashtag edges
    for vh in raw["video_hashtags"]:
        edges.append({
            "from": f"v_{vh['video_id']}",
            "to": f"h_{vh['hashtag_id']}",
            "color": {"color": "#33352e", "highlight": "#8d8f85"},
            "width": 1,
            "type": "uses_hashtag",
        })

    # Hashtag co-occurrence edges (thicker = more co-occurrence)
    for co in raw["cooccurrences"]:
        edges.append({
            "from": f"h_{co['hashtag_a_id']}",
            "to": f"h_{co['hashtag_b_id']}",
            "color": {"color": "#454740", "highlight": "#8d8f85"},
            "width": min(co["count"] * 2, 8),
            "dashes": True,
            "type": "co_occurs",
            "title": f"Co-occur in {co['count']} video(s)",
        })

    # External link nodes + edges
    for link in raw["external_links"]:
        node_id = f"ext_{link['id']}"
        nodes.append({
            "id": node_id,
            "label": link["url"][:30],
            "type": "external_link",
            "platform": link["platform"],
            "full_url": link["url"],
            "color": {"background": INK, "border": INK,
                      "highlight": {"background": "#ffffff", "border": "#c05038"}},
            "font": {"color": INK},
            "shape": "diamond",
            "size": 14,
        })
        source_id = f"v_{link['source_id']}" if link["source_type"] == "video" else f"a_{link['source_id']}"
        if source_id in seen_nodes:
            edges.append({
                "from": source_id,
                "to": node_id,
                "color": {"color": "#c05038", "highlight": "#d8d4c6"},
                "width": 2,
                "type": "links_to",
                "title": f"Links to {link['platform']}",
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": {k: v for k, v in categories.items()},
        "stats": {
            "total_videos": len(raw["videos"]),
            "total_hashtags": len(raw["hashtags"]),
            "total_links": len(raw["external_links"]),
            "total_comments": raw.get("comment_total", 0),
        }
    }
