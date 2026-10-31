import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from unittest.mock import patch, MagicMock
import db
from taste import TasteProfile
from vision import VisionScorer, SYSTEM_PROMPT

CONFIG = {"anthropic_api_key": "test-key", "criteria": {"min_vision_score": 5}}


def _make_profile_with_signal(tmp_path, neg_tags=None, pos_tags=None):
    conn = db.connect(str(tmp_path / "t.db"))
    for i in range(3):
        db.save_feedback(conn, f"id:{i}", "down", "x", tags=neg_tags or [])
    if pos_tags:
        for i in range(3):
            db.save_feedback(conn, f"pos:{i}", "up", "y", tags=pos_tags)
    return TasteProfile.from_db(conn)


def test_taste_profile_addendum_injected_into_system_prompt(tmp_path):
    profile = _make_profile_with_signal(tmp_path, neg_tags=["dark"])
    scorer = VisionScorer(CONFIG, taste_profile=profile)
    system_prompt = scorer._build_system_prompt()
    assert "dark" in system_prompt
    assert "dislikes" in system_prompt
    from vision import SYSTEM_PROMPT as _BASE

    addendum = profile.system_prompt_addendum()
    assert system_prompt == _BASE + "\n\n" + addendum
    assert system_prompt.startswith(_BASE)


def test_no_addendum_without_signal(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    profile = TasteProfile.from_db(conn)  # 0 feedback rows
    scorer = VisionScorer(CONFIG, taste_profile=profile)
    system_prompt = scorer._build_system_prompt()
    assert system_prompt == SYSTEM_PROMPT


def test_scorer_works_without_taste_profile():
    scorer = VisionScorer(CONFIG)  # no taste_profile arg
    assert scorer._build_system_prompt() == SYSTEM_PROMPT
