"""Scrappy taste profile — learns user preferences from feedback tags.

TasteProfile.from_db() reads all tagged feedback and computes:
  - system_prompt_addendum(): injected into VisionScorer system prompt
  - ranker_weights(): tag-based score adjustments for ranker
  - hard_filter_candidates(): tags with >= 5 negative occurrences (informational only)

parse_reason_to_tags(reason) extracts structured TAGS from free-text rejection reasons.
"""

import json
import re
import sqlite3

NEGATIVE_TAGS: frozenset[str] = frozenset(
    {
        "dark",
        "no_view",
        "ground_floor_vibe",
        "awkward_layout",
        "no_balcony",
        "rooms_too_small",
        "old_finishes",
        "ugly_kitchen",
        "bad_bathrooms",
        "old_building",
        "no_doorman",
        "no_amenities",
        "bad_street",
        "far_metro",
        "noisy_area",
        "no_parking",
        "too_small",
        "no_storage",
        "overpriced",
        "bad_vibes",
        "wrong_location",
    }
)

POSITIVE_TAGS: frozenset[str] = frozenset(
    {
        "great_light",
        "open_view",
        "good_flow",
        "nice_finishes",
        "new_build",
        "rooftop",
        "great_location",
        "good_value",
        "love_it",
    }
)

TAGS: frozenset[str] = NEGATIVE_TAGS | POSITIVE_TAGS

# Comma-separated string used in Haiku tag-parse prompts
TAGS_CSV: str = ", ".join(sorted(TAGS))

# ── Keyword → tag map (order matters: more specific phrases first) ────────────
# Each entry: (list_of_trigger_phrases, tag_name)
# Phrases are matched case-insensitively as substrings of the reason text.
_KEYWORD_MAP: list[tuple[list[str], str]] = [
    # Wrong location
    (["wrong city", "wrong location", "address mismatch", "location mismatch",
      "outside of preferred", "too far", "not cdmx", "edge of map",
      "preferred area", "didnt like location", "didn't like location",
      "wrong state", "city mismatch", "wrong address", "mismatched address",
      "wrong_location", "not polanco", "bosque real", "not in cdmx",
      "different city", "outside cdmx", "outside mexico city"], "wrong_location"),
    # Dark / lighting
    (["too dark", "bad lighting", "dark", "dim", "no light", "no natural light",
      "poor light", "low light"], "dark"),
    # Great light (positive)
    (["great light", "bright", "lots of light", "beautiful light",
      "amazing light", "plenty of light"], "great_light"),
    # Old building
    (["old building", "old block", "old tower", "dated building"], "old_building"),
    # Old finishes
    (["old kitchen", "old rug", "old carpet", "old floor", "ugly floor",
      "old style", "out of style", "old finish", "old and", "looks old",
      "too old", "old,"], "old_finishes"),
    # Bad kitchen
    (["ugly kitchen", "bad kitchen", "small kitchen", "old kitchen"], "ugly_kitchen"),
    # Bad bathrooms
    (["bad bathroom", "ugly bathroom", "old bathroom", "old bath"], "bad_bathrooms"),
    # No view
    (["no view", "bad view", "no windows", "no natural"], "no_view"),
    # Too small
    (["too small", "very small", "too tiny", "small and",
      "outside of preferred area, too small", "small apartment"], "too_small"),
    # Rooms too small
    (["rooms too small", "bedrooms small", "small rooms"], "rooms_too_small"),
    # Overpriced
    (["expensive", "overpriced", "too much", "bad value",
      "not worth", "small and expensive"], "overpriced"),
    # Bad vibes / style
    (["bad vibes", "ugly", "depressing", "not right style",
      "corporate management", "institutional", "cold feel"], "bad_vibes"),
    # No parking
    (["no parking", "no garage", "no car"], "no_parking"),
    # No balcony
    (["no balcony", "no terrace", "no outdoor"], "no_balcony"),
    # Noisy
    (["noisy", "loud", "busy street", "too much traffic"], "noisy_area"),
    # Bad street
    (["bad street", "bad area", "bad neighborhood", "bad neighbourhood",
      "bad block"], "bad_street"),
    # No doorman
    (["no doorman", "no security", "no concierge"], "no_doorman"),
    # No amenities
    (["no amenities", "no gym", "no pool"], "no_amenities"),
    # No storage
    (["no storage", "no closet", "no wardrobe"], "no_storage"),
    # Far from metro
    (["far metro", "far from metro", "no metro nearby", "no subway"], "far_metro"),
    # Awkward layout
    (["awkward layout", "weird layout", "bad flow", "bad layout",
      "strange layout"], "awkward_layout"),
    # Positive — great location
    (["great location", "perfect location", "amazing location",
      "love the location", "excellent location"], "great_location"),
    # Positive — good value
    (["good value", "great price", "affordable", "good deal",
      "fair price"], "good_value"),
    # Positive — love it
    (["love it", "perfect", "this is it", "amazing apartment",
      "worth seeing", "great candidate", "strong candidate",
      "excellent choice", "will visit", "good find", "nice find",
      "great find", "i liked", "i like this"], "love_it"),
    # Positive — great view
    (["open view", "great view", "amazing view", "nice view",
      "beautiful view"], "open_view"),
    # Positive — new build
    (["new build", "new building", "newly built", "modern building",
      "brand new"], "new_build"),
    # Positive — nice finishes
    (["nice finishes", "beautiful finishes", "high-end",
      "luxury finishes", "premium finishes"], "nice_finishes"),
    # Positive — good flow
    (["good flow", "nice flow", "nice layout", "great layout",
      "well designed"], "good_flow"),
    # Positive — rooftop
    (["rooftop", "roof terrace", "roof access"], "rooftop"),
]


def parse_reason_to_tags(reason: str, signal: str = "") -> list[str]:
    """Extract structured TAGS from a free-text feedback reason.

    Uses keyword substring matching — no LLM required. Returns only tags
    from the known TAGS vocabulary. Deduplicates results.

    Args:
        reason: Free-text rejection/approval note from the user.
        signal: "up" or "down" — filters to only semantically matching tags
                (down → NEGATIVE_TAGS only, up → POSITIVE_TAGS only).
                Pass "" to return all matched tags regardless of signal.

    Returns:
        List of tag strings (may be empty if no keywords matched).
    """
    if not reason:
        return []
    lower = reason.lower()
    found: list[str] = []
    seen: set[str] = set()
    for phrases, tag in _KEYWORD_MAP:
        if tag in seen:
            continue
        for phrase in phrases:
            if phrase in lower:
                found.append(tag)
                seen.add(tag)
                break
    # Filter tags to only those semantically valid for this signal
    if signal == "down":
        found = [t for t in found if t in NEGATIVE_TAGS]
    elif signal == "up":
        found = [t for t in found if t in POSITIVE_TAGS]
    return found


def backfill_tags(conn: sqlite3.Connection, *, commit: bool = True) -> int:
    """Parse tags from reason text for all feedback rows that have no structured tags.

    Safe to run multiple times — only touches rows where tags_json is NULL or [].

    Returns:
        Number of rows updated.
    """
    rows = conn.execute(
        "SELECT id, signal, reason FROM feedback "
        "WHERE reason IS NOT NULL AND reason != '' "
        "AND (tags_json IS NULL OR tags_json = '[]')"
    ).fetchall()
    updated = 0
    for row_id, signal, reason in rows:
        tags = parse_reason_to_tags(reason, signal=signal)
        if tags:
            conn.execute(
                "UPDATE feedback SET tags_json=? WHERE id=?",
                (json.dumps(tags), row_id),
            )
            updated += 1
    if commit and updated:
        conn.commit()
    return updated


class TasteProfile:
    def __init__(
        self,
        pos_counts: dict[str, int],
        neg_counts: dict[str, int],
        total_feedback: int,
    ) -> None:
        self._pos = pos_counts
        self._neg = neg_counts
        self._total = total_feedback

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> "TasteProfile":
        """Build TasteProfile from all tagged feedback rows in DB."""
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        rows = conn.execute(
            "SELECT signal, tags_json FROM feedback WHERE tags_json IS NOT NULL"
        ).fetchall()
        pos_counts: dict[str, int] = {}
        neg_counts: dict[str, int] = {}
        for signal, tags_json in rows:
            try:
                tags = json.loads(tags_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for tag in tags:
                if signal == "up" and tag in POSITIVE_TAGS:
                    pos_counts[tag] = pos_counts.get(tag, 0) + 1
                elif signal == "down" and tag in NEGATIVE_TAGS:
                    neg_counts[tag] = neg_counts.get(tag, 0) + 1
        return cls(pos_counts, neg_counts, total)

    def has_signal(self) -> bool:
        """True if at least 3 feedback rows exist — minimum for useful personalization."""
        return self._total >= 3

    def system_prompt_addendum(self) -> str:
        """Paragraph injected into VisionScorer SYSTEM_PROMPT when has_signal() is True."""
        if not self.has_signal():
            return ""
        lines = []
        top_neg = sorted(self._neg.items(), key=lambda x: x[1], reverse=True)[:5]
        top_pos = sorted(self._pos.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_neg:
            neg_str = ", ".join(f"{t} ({n}x)" for t, n in top_neg)
            lines.append(f"This user strongly dislikes: {neg_str}.")
        if top_pos:
            pos_str = ", ".join(f"{t} ({n}x)" for t, n in top_pos)
            lines.append(f"They prefer: {pos_str}.")
        if lines:
            lines.append(
                "Penalize listings matching negative traits. "
                "Reward listings matching positive traits."
            )
        return "\n".join(lines)

    def ranker_weights(self) -> dict[str, float]:
        """Tag -> score adjustment for ranker. Negative = penalize, positive = boost.
        Each occurrence = +/-0.15, capped at +/-1.5 per tag."""
        weights: dict[str, float] = {}
        for tag, count in self._neg.items():
            weights[tag] = -min(count * 0.15, 1.5)
        for tag, count in self._pos.items():
            weights[tag] = min(count * 0.15, 1.5)
        return weights

    def hard_filter_candidates(self) -> list[str]:
        """Tags with >= 5 negative occurrences and 0 positive — candidates for hard filter.
        Informational only; not auto-applied."""
        return [
            tag
            for tag, count in self._neg.items()
            if count >= 5 and self._pos.get(tag, 0) == 0
        ]
