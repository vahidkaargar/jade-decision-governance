import pytest
from jade import improve  # noqa: F401


def test_candidate_cannot_target_protected():
    with pytest.raises(improve.ProtectedTarget):
        improve.propose(target_component="promotion_rule", change_diff="x", hypothesis="y" * 10)


def test_promotion_requires_protected_pass(tmp_path):
    c = improve.propose(target_component="question_template", change_diff="x", hypothesis="y" * 10)
    with pytest.raises(improve.PromotionBlocked):
        improve.promote(tmp_path, c.candidate_id)   # no protected-set result attached


def test_kill_switch_freezes(tmp_path):
    (tmp_path / "FREEZE").write_text("")
    assert improve.frozen(tmp_path)


def test_shadow_adds_zero_hot_path_latency():
    assert improve.shadow_mode_is_async()
