# -*- coding: utf-8 -*-
"""macro_rules 판정 검증.   실행:  py -3.13 -m pytest test_macro_rules.py -q"""

from macro_rules import (DEFAULTS as TH, bubble_checklist, exit_check, falling_streak,
                         judge, judge_regime, leverage_check, rising_streak, scenario,
                         unemployment_aux)

# 스펙 7-1 (2025.09 ~ 2026.08, 2025.10은 셧다운으로 미발표라 제외)
CPI_2026 = [3.0, 2.7, 2.7, 2.4, 2.4, 3.3, 3.8, 4.2, 3.5, 3.4, 3.4]
UNRATE = [4.1, 4.2, 4.2, 4.3, 4.3, 4.2, 4.1, 4.1, 4.2]


def ind(**kw):
    base = dict(cpi=CPI_2026, y10=5.28, y10_high=5.34, unrate=UNRATE, fed_pause=False)
    return {**base, **kw}


def test_streaks():
    assert rising_streak([1, 2, 3]) == 2
    assert rising_streak([3, 2, 3]) == 1
    assert rising_streak([1, 2, 2]) == 0          # 횡보면 끊김
    assert falling_streak([4.2, 3.5, 3.4]) == 2
    assert falling_streak([3.5, 3.4, 3.4]) == 0


def test_spec_snapshot_is_high_regime_yellow():
    """스펙 7-3: 고물가 시대, 🟡 주의"""
    assert judge_regime(ind(), TH)[0] == "HIGH"
    stage, why = judge(ind(), TH)
    assert stage == "YELLOW"
    assert any("관망" in w for w in why)


def test_red_needs_both_conditions():
    assert judge(ind(cpi=CPI_2026 + [3.6], y10=5.40), TH)[0] == "RED"
    assert judge(ind(cpi=CPI_2026 + [3.6], y10=5.20), TH)[0] == "ORANGE"   # 금리는 고점 아래
    assert judge(ind(cpi=CPI_2026 + [3.3], y10=5.40), TH)[0] != "RED"      # 물가가 기준 아래


def test_orange_two_month_rise():
    assert judge(ind(cpi=CPI_2026 + [3.42, 3.45]), TH)[0] == "ORANGE"


def test_green_and_strong_green():
    falling = CPI_2026 + [3.3, 3.2]
    assert judge(ind(cpi=falling, y10=5.10), TH)[0] == "YELLOW"           # 금리가 5.0 이상
    assert judge(ind(cpi=falling, y10=4.90), TH)[0] == "GREEN"
    assert judge(ind(cpi=falling, y10=4.60), TH)[0] == "GREEN"            # 연준 체크 없음
    assert judge(ind(cpi=falling, y10=4.60, fed_pause=True), TH)[0] == "GREEN2"


def test_low_regime_rules():
    low = [2.1] * 12
    assert judge_regime(ind(cpi=low), TH)[0] == "LOW"
    assert judge(ind(cpi=low, unrate=[3.6] * 10 + [3.8, 3.9]), TH)[0] == "RED"
    assert judge(ind(cpi=low, unrate=[4.0] * 10 + [3.9, 3.8]), TH)[0] == "GOLD"
    assert judge(ind(cpi=[2.6] * 12, unrate=[4.0] * 10 + [3.9, 3.8]), TH)[0] == "GREEN"


def test_scenario_checkpoints():
    """스펙 4-3: 9월 CPI 3.5 이상 -> 🟠, 3.4 -> 🟡, 3.3 -> 🟢 쪽이지만 1개월이라 미확정"""
    assert scenario(ind(), TH, 3.57)[0] == "ORANGE"
    assert scenario(ind(), TH, 3.4)[0] == "YELLOW"
    assert scenario(ind(y10=4.9), TH, 3.3)[0] == "YELLOW"   # 하락 1개월째(3.4→3.4→3.3)


def test_bubble_checklist_touch_then_pullback():
    (_, s1, _), (_, s2, _) = bubble_checklist(ind(nowcast=3.57), TH, recent_high=5.342)
    assert s1 == "⚠️" and s2 == "⚠️"
    assert bubble_checklist(ind(), TH)[1][1] == "❌"
    assert bubble_checklist(ind(cpi_mom=0.40), TH)[1][1] == "⚠️"     # 스펙 7-1: 8월 전월비 +0.4%


def test_unemployment_aux_and_exit():
    assert unemployment_aux([4.0, 4.0, 4.1, 4.3], TH)[0] == "GREEN"
    assert unemployment_aux([4.3, 4.2, 4.1, 4.0], TH)[0] == "ORANGE"
    assert not any(hit for hit, _ in exit_check(ind(), TH))


def test_leverage():
    assert leverage_check(80, 100, TH)[1] == "STOP"
    assert leverage_check(112, 100, TH)[1] == "TAKE"
    assert leverage_check(100, 100, TH)[1] is None


# ---------------------------------------------------------------- 쉬운 말 해석
from macro_rules import next_cpi_outcomes, plain_summary, stage_triggers  # noqa: E402


def test_plain_summary_snapshot():
    s = plain_summary(ind(nowcast=3.60), TH, "YELLOW", "HIGH")
    assert s["headline"] == "기다리는 구간"
    assert "물가 3.4% — 높음, 제자리, 다음엔 오를 조짐" in s["reasons"][0]
    assert "정점 4.2%" in s["reasons"][0]
    assert "고점 5.34%까지 0.06%p" in s["reasons"][1]


def test_next_cpi_outcomes_snapshot():
    """스펙 4-3: 3.5% 이상이면 🟠, 그 아래는 🟡 유지 (🟢는 2개월 연속 하락 + 금리 5% 아래 필요)"""
    assert next_cpi_outcomes(ind(), TH) == [("3.4% 이하", "YELLOW"), ("3.5% 이상", "ORANGE")]


def test_next_cpi_outcomes_three_groups():
    """금리가 5% 아래이고 이미 1개월 하락했으면, 한 번 더 내리면 🟢"""
    o = next_cpi_outcomes(ind(cpi=CPI_2026 + [3.3], y10=4.9), TH)
    assert o[0] == ("3.2% 이하", "GREEN") and o[-1] == ("3.5% 이상", "ORANGE")


def test_stage_triggers_distance():
    t = {x["stage"]: x for x in stage_triggers(ind(), TH, "HIGH")}
    assert [m for _, m, _ in t["RED"]["conds"]] == [False, False]
    assert "0.06%p 남음" in t["RED"]["conds"][1][2]
    assert "0.28%p 내려와야" in t["GREEN"]["conds"][1][2]
