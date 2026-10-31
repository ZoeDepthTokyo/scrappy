import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import json
import db
from taste import TasteProfile, TAGS, NEGATIVE_TAGS, POSITIVE_TAGS


_seed_counter = 0


def _seed(conn, signal, tags):
    global _seed_counter
    _seed_counter += 1
    db.save_feedback(conn, f"id:{_seed_counter}:{signal}:{','.join(tags)}", signal, "test", tags=tags)


def test_tags_are_disjoint():
    assert NEGATIVE_TAGS.isdisjoint(POSITIVE_TAGS)
    assert TAGS == NEGATIVE_TAGS | POSITIVE_TAGS


def test_has_signal_false_when_empty(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    profile = TasteProfile.from_db(conn)
    assert profile.has_signal() is False


def test_has_signal_true_at_three(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for i in range(3):
        db.save_feedback(conn, f"id:{i}", "up", "ok")
    profile = TasteProfile.from_db(conn)
    assert profile.has_signal() is True


def test_system_prompt_empty_without_signal(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    profile = TasteProfile.from_db(conn)
    assert profile.system_prompt_addendum() == ""


def test_system_prompt_contains_top_negative_tags(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for _ in range(4):
        _seed(conn, "down", ["dark"])
    for _ in range(2):
        _seed(conn, "down", ["no_parking"])
    _seed(conn, "up", ["great_light"])
    profile = TasteProfile.from_db(conn)
    addendum = profile.system_prompt_addendum()
    assert "dark" in addendum
    assert "4x" in addendum


def test_system_prompt_contains_positive_tags(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for _ in range(3):
        _seed(conn, "up", ["great_light"])
    profile = TasteProfile.from_db(conn)
    addendum = profile.system_prompt_addendum()
    assert "great_light" in addendum


def test_ranker_weights_negative_tags_are_negative(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for _ in range(4):
        _seed(conn, "down", ["dark"])
    profile = TasteProfile.from_db(conn)
    weights = profile.ranker_weights()
    assert "dark" in weights
    assert weights["dark"] < 0


def test_ranker_weights_positive_tags_are_positive(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for _ in range(4):
        _seed(conn, "up", ["great_light"])
    profile = TasteProfile.from_db(conn)
    weights = profile.ranker_weights()
    assert "great_light" in weights
    assert weights["great_light"] > 0


def test_ranker_weights_capped_at_1_5(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for _ in range(20):
        _seed(conn, "down", ["dark"])
    profile = TasteProfile.from_db(conn)
    weights = profile.ranker_weights()
    assert weights["dark"] == -1.5


def test_hard_filter_candidates_five_neg_no_pos(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    for _ in range(5):
        _seed(conn, "down", ["old_building"])
    for _ in range(3):
        _seed(conn, "up", ["great_light"])
    profile = TasteProfile.from_db(conn)
    candidates = profile.hard_filter_candidates()
    assert "old_building" in candidates
    assert "great_light" not in candidates


def test_unknown_tags_ignored(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.save_feedback(conn, "x", "down", "meh", tags=["made_up_tag", "dark"])
    db.save_feedback(conn, "y", "up", "nice")
    db.save_feedback(conn, "z", "up", "yes")
    profile = TasteProfile.from_db(conn)
    weights = profile.ranker_weights()
    assert "made_up_tag" not in weights
    assert "dark" in weights
