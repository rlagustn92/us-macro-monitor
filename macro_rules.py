# -*- coding: utf-8 -*-
"""
물가·금리 신호 판정 규칙 (macro_monitor_spec.md 3~6장)
================================================================
화면/데이터와 무관한 순수 함수만 모아둠 -> test_macro_rules.py 로 검증.

  입력 ind (지표 dict)
    cpi       : CPI 전년비 월간 시계열 [오래된 것 ... 최신]  (BLS 발표처럼 소수 1자리)
    y10       : 미국 10년물 금리 (%)
    y10_high  : 직전 고점 (%)  — 고정값(기본 5.34) 또는 52주 최고치
    unrate    : 실업률 월간 시계열 [오래된 것 ... 최신]
    fed_pause : 연준 인상 중단 시그널 (수동 체크)

  입력 th (임계값 dict)  — DEFAULTS 참고. 전부 대화에서 정한 휴리스틱.

  출력  judge(ind, th) -> (단계 키, [판정 근거 문장, ...])
================================================================
"""

DEFAULTS = {
    "regime_cpi": 3.0,    # CPI 전년비 12개월 평균이 이 이상이면 고물가 시대
    "cpi_red":    3.5,    # 🔴/🟠 물가 기준
    "y10_green":  5.0,    # 🟢 10년물 기준
    "y10_strong": 4.7,    # 🟢🟢 10년물 기준
    "y10_high":   5.34,   # 직전 고점 기본값 (2026-10-01 장중 고가)
    "streak":     2,      # "N개월 연속"
    "unrate_rise": 0.3,   # 저물가 🔴: 12개월 최저 대비 실업률 상승폭
    "gold_cpi":   2.5,    # 저물가 🌟: 물가 낮음(≈2%) 판정 상한
    "unrate_aux": 0.2,    # 고물가 보조신호: 실업률 3개월 변화 기준
    "mom_warn":   0.3,    # 버블 조건2 경고등: CPI 전월비(계절조정) 이 이상이면 재상승 경고
    "stop_loss":  -20.0,  # 레버리지 손절선 (진입가 대비 %)
    "take_profit": 10.0,  # 레버리지 익절 시작 (진입가 대비 %)
}

STAGES = {  # 우선순위 높은 순
    "RED":    dict(icon="🔴", label="매우 위험", color="#d03b3b"),
    "ORANGE": dict(icon="🟠", label="위험",     color="#ec835a"),
    "GREEN2": dict(icon="🟢🟢", label="강한 기회", color="#0ca30c"),
    "GOLD":   dict(icon="🌟", label="황금기",   color="#0ca30c"),
    "GREEN":  dict(icon="🟢", label="기회",     color="#0ca30c"),
    "YELLOW": dict(icon="🟡", label="주의",     color="#fab219"),
}

# 6-3 단계별 액션 표.  (몸통 적립, 커버드콜, 레버리지 10%, 대기 현금)
ACTIONS = {
    "RED":    ("유지", "유지", "0으로 정리",
               "비중 확대, 비싼 성장주·반도체 일부 축소"),
    "ORANGE": ("유지", "유지", "절반으로 축소, 재진입 금지", "유지·확대"),
    "YELLOW": ("유지", "유지", "신규 진입 중단, 보유분은 익절/손절 규칙대로",
               "단기채로 이자 받으며 대기"),
    "GREEN":  ("유지", "유지", "스윙 재개",
               "1/3을 지수 ETF(QQQ, 반도체 일반 ETF)에 3개월 분할 투입"),
    "GREEN2": ("유지", "유지", "스윙 적극", "남은 대기 현금 전부 투입"),
}
ACTIONS["GOLD"] = ACTIONS["GREEN2"]   # 저물가 황금기 = 강한 기회와 같은 행동


def ok(b):
    return "✅" if b else "❌"


# ---------------------------------------------------------------- 시계열 도우미
def rising_streak(xs):
    """끝에서부터 '직전 달보다 높았던' 달이 연속 몇 번인지. 같으면 끊김."""
    n = 0
    for a, b in zip(reversed(xs[:-1]), reversed(xs[1:])):
        if b > a:
            n += 1
        else:
            break
    return n


def falling_streak(xs):
    return rising_streak([-x for x in xs])


# ---------------------------------------------------------------- 3장 레짐
def judge_regime(ind, th):
    recent = ind["cpi"][-12:]
    avg = sum(recent) / len(recent)
    high = avg >= th["regime_cpi"]
    why = (f"CPI 전년비 최근 {len(recent)}개월 평균 {avg:.2f}% "
           f"{'≥' if high else '<'} {th['regime_cpi']:.1f}%")
    return ("HIGH" if high else "LOW"), why


# ---------------------------------------------------------------- 4장 고물가 규칙
def judge_high(ind, th):
    cpi, y10, hi = ind["cpi"][-1], ind["y10"], ind["y10_high"]
    up, down, n = rising_streak(ind["cpi"]), falling_streak(ind["cpi"]), th["streak"]

    c_cpi = cpi >= th["cpi_red"]
    c_brk = y10 >= hi
    red = c_cpi and c_brk
    orange = up >= n or c_cpi
    green = down >= n and y10 < th["y10_green"]
    green2 = green and y10 < th["y10_strong"] and ind.get("fed_pause", False)

    r = [
        f"🔴 CPI {cpi:.1f}% ≥ {th['cpi_red']:.1f}% {ok(c_cpi)} · "
        f"10년물 {y10:.2f}% ≥ 직전 고점 {hi:.2f}% {ok(c_brk)} → {'충족' if red else '미충족'}",
        f"🟠 CPI {up}개월 연속 상승 (기준 {n}) {ok(up >= n)} 또는 "
        f"CPI ≥ {th['cpi_red']:.1f}% {ok(c_cpi)} → {'충족' if orange else '미충족'}",
        f"🟢 CPI {down}개월 연속 하락 (기준 {n}) {ok(down >= n)} · "
        f"10년물 {y10:.2f}% < {th['y10_green']:.1f}% {ok(y10 < th['y10_green'])} → "
        f"{'충족' if green else '미충족'}",
        f"🟢🟢 🟢 조건 {ok(green)} · 10년물 < {th['y10_strong']:.1f}% "
        f"{ok(y10 < th['y10_strong'])} · 연준 인상 중단 {ok(ind.get('fed_pause', False))} → "
        f"{'충족' if green2 else '미충족'}",
    ]
    stage = ("RED" if red else "ORANGE" if orange else "GREEN2" if green2
             else "GREEN" if green else "YELLOW")
    if stage == "YELLOW":
        r.append("🟡 위 조건 모두 해당 없음 → 관망")
    return stage, r


# ---------------------------------------------------------------- 5장 저물가 규칙
def judge_low(ind, th):
    u = ind["unrate"]
    last, low12 = u[-1], min(u[-12:])
    rise = round(last - low12, 2)          # 3.9-3.6=0.2999.. 같은 오차로 경계값 놓치지 않게
    down = falling_streak(u)
    n = th["streak"]
    cpi = ind["cpi"][-1]

    red = rise >= th["unrate_rise"]
    green = down >= n
    gold = green and cpi <= th["gold_cpi"]
    r = [
        f"🔴 실업률 {last:.1f}% − 12개월 최저 {low12:.1f}% = +{rise:.1f}%p "
        f"≥ {th['unrate_rise']:.1f}%p {ok(red)}",
        f"🟢 실업률 {down}개월 연속 하락 (기준 {n}) {ok(green)}",
        f"🌟 🟢 조건 {ok(green)} · CPI {cpi:.1f}% ≤ {th['gold_cpi']:.1f}% "
        f"{ok(cpi <= th['gold_cpi'])}",
    ]
    stage = "RED" if red else "GOLD" if gold else "GREEN" if green else "YELLOW"
    if stage == "YELLOW":
        r.append("🟡 위 조건 모두 해당 없음 → 관망")
    return stage, r


def judge(ind, th):
    """레짐을 먼저 정하고 그 레짐의 규칙으로 단계 판정."""
    regime, why = judge_regime(ind, th)
    stage, reasons = (judge_high if regime == "HIGH" else judge_low)(ind, th)
    return stage, [f"레짐: {'고물가' if regime == 'HIGH' else '저물가'} 시대 ({why})"] + reasons


# ---------------------------------------------------------------- 보조 판정
def unemployment_aux(unrate, th):
    """4-2: 고물가 시대 실업률 보조 신호. 단계는 바꾸지 않음."""
    d = round(unrate[-1] - unrate[-4], 2)
    if d >= th["unrate_aux"]:
        return "GREEN", f"실업률 3개월간 {d:+.1f}%p 상승 → 수개월 뒤 물가 하락 기대 (🟢 쪽)"
    if d <= -th["unrate_aux"]:
        return "ORANGE", f"실업률 3개월간 {d:+.1f}%p 하락(고용 호황) → 수개월 뒤 물가 상승 우려 (🟠 쪽)"
    return None, f"실업률 3개월간 {d:+.1f}%p → 뚜렷한 방향 없음"


def bubble_checklist(ind, th, recent_high=None):
    """1-2 버블 붕괴 조건 2개. 상태: ✅ 충족 / ⚠️ 경고 / ❌ 미충족.
    recent_high: 최근 1개월 10년물 최고치 (돌파 직후 반락한 경우 경고로 표시)"""
    y10, hi = ind["y10"], ind["y10_high"]
    if y10 >= hi:
        s1 = ("✅", f"10년물 {y10:.2f}% ≥ 직전 고점 {hi:.2f}% — 신고가 돌파 중")
    elif recent_high is not None and recent_high >= hi:
        s1 = ("⚠️", f"최근 1개월 내 장중 {recent_high:.3f}%로 고점 {hi:.2f}% 터치 후 "
                    f"현재 {y10:.2f}%로 반락")
    elif hi - y10 <= 0.15:
        s1 = ("⚠️", f"10년물 {y10:.2f}% — 고점 {hi:.2f}%까지 {hi - y10:.2f}%p, 근접")
    else:
        s1 = ("❌", f"10년물 {y10:.2f}% < 고점 {hi:.2f}%")

    cpi, up = ind["cpi"][-1], rising_streak(ind["cpi"])
    nowcast, mom = ind.get("nowcast"), ind.get("cpi_mom")
    warn = []
    if up >= 1:
        warn.append(f"CPI {up}개월 연속 상승")
    if mom is not None and mom >= th["mom_warn"]:
        warn.append(f"전월비 {mom:+.2f}% ≥ {th['mom_warn']:.1f}%")
    if nowcast is not None and nowcast >= th["cpi_red"]:
        warn.append(f"나우캐스트 {nowcast:.2f}% ≥ {th['cpi_red']:.1f}%")
    if cpi >= th["cpi_red"]:
        s2 = ("✅", f"CPI {cpi:.1f}% ≥ {th['cpi_red']:.1f}% — 물가 재상승")
    elif warn:
        s2 = ("⚠️", f"CPI {cpi:.1f}% — 미충족이나 경고등 ({', '.join(warn)})")
    else:
        s2 = ("❌", f"CPI {cpi:.1f}% < {th['cpi_red']:.1f}%, 상승 흐름 아님")
    return [("조건 1 · Breaking to New Highs (금리 신고가)",) + s1,
            ("조건 2 · No Way Back (물가 재상승)",) + s2]


def exit_check(ind, th):
    """6-4: 🟢에서 산 물량 청산 조건 (하나라도 나오면 청산)."""
    up = rising_streak(ind["cpi"])
    return [
        (up >= th["streak"], f"CPI 전년비 바닥 찍고 {up}개월 연속 상승 (기준 {th['streak']}개월)"),
        (ind["y10"] >= ind["y10_high"],
         f"10년물 {ind['y10']:.2f}% 직전 고점 {ind['y10_high']:.2f}% 재돌파"),
    ]


def scenario(ind, th, next_cpi):
    """4-3 / 8-1⑥: 다음 CPI가 next_cpi(소수 1자리로 반올림)로 나오면 단계는?"""
    ind2 = dict(ind, cpi=list(ind["cpi"]) + [round(next_cpi, 1)])
    return judge(ind2, th)


def leverage_check(price, entry, th):
    """6-2 손절 / 6-1 익절 규칙 대비 위치."""
    ret = round((price / entry - 1) * 100, 4)
    if ret <= th["stop_loss"]:
        return ret, "STOP", f"손절선 {th['stop_loss']:.0f}% 도달 → 기계적 정리"
    if ret >= th["take_profit"]:
        return ret, "TAKE", f"익절 구간(+{th['take_profit']:.0f}% 이상) 도달"
    return ret, None, (f"손절선까지 {ret - th['stop_loss']:.1f}%p · "
                       f"익절까지 {th['take_profit'] - ret:.1f}%p")


# ================================================================
# 초보용 쉬운 말 해석 (화면 맨 위 '한눈에 보기')
# ================================================================
LADDER = ["RED", "ORANGE", "YELLOW", "GREEN", "GREEN2"]   # 나쁨 -> 좋음 순서

PLAIN = {   # 단계 -> (한 줄 결론, 레버리지, 현금)
    "RED":    ("피해야 할 구간", "레버리지 전부 정리", "현금 늘리기, 비싼 성장주·반도체 일부 축소"),
    "ORANGE": ("줄여야 할 구간", "레버리지 절반으로, 다시 사지 않기", "현금 유지·확대"),
    "YELLOW": ("기다리는 구간", "레버리지 새로 사지 않기 (보유분은 익절/손절 규칙대로)",
               "현금은 단기채로 이자 받으며 대기"),
    "GREEN":  ("나눠 사기 시작하는 구간", "스윙 다시 시작",
               "대기 현금 1/3을 지수 ETF에 3개월 나눠 투입"),
    "GREEN2": ("적극적으로 사는 구간", "스윙 적극", "남은 대기 현금 전부 투입"),
    "GOLD":   ("강한 상승장 구간", "스윙 적극", "남은 대기 현금 전부 투입"),
}


def cpi_zone(v, th):
    """물가 수준 구간: (키, 쉬운 말)"""
    if v < th["gold_cpi"]:
        return "good", "안정"
    if v < th["cpi_red"]:
        return "warning", "높음"
    return "critical", "위험"


def y10_zone(v, th):
    if v < th["y10_strong"]:
        return "good", "숨통 트임"
    if v < th["y10_green"]:
        return "warning", "보통"
    if v < th["y10_high"]:
        return "serious", "부담"
    return "critical", "신고가"


def plain_summary(ind, th, stage, regime):
    """화면 맨 위 큰 카드용. 반환: dict(headline, reasons[...], lev, cash)"""
    cpi, y10, hi = ind["cpi"][-1], ind["y10"], ind["y10_high"]
    up, down = rising_streak(ind["cpi"]), falling_streak(ind["cpi"])
    nowcast = ind.get("nowcast")
    peak12 = max(ind["cpi"][-12:])

    trend = ("다시 오르는 중" if up >= 1 else "내려오는 중" if down >= 1 else "제자리")
    if nowcast is not None and up == 0 and nowcast >= cpi + 0.15:
        trend += ", 다음엔 오를 조짐"
    elif nowcast is not None and down == 0 and nowcast <= cpi - 0.15:
        trend += ", 다음엔 내릴 조짐"
    r_cpi = f"물가 {cpi:.1f}% — {cpi_zone(cpi, th)[1]}, {trend}"
    if peak12 >= cpi + 0.5:
        r_cpi += f" (1년 내 정점 {peak12:.1f}%에서 내려옴)"

    _, yz = y10_zone(y10, th)
    if y10 >= hi:
        r_y10 = f"금리 {y10:.2f}% — 직전 고점 {hi:.2f}%를 뚫음 (자산시장에 가장 큰 부담)"
    elif hi - y10 <= 0.15:
        r_y10 = f"금리 {y10:.2f}% — {yz}, 고점 {hi:.2f}%까지 {hi - y10:.2f}%p 남음"
    else:
        r_y10 = f"금리 {y10:.2f}% — {yz}"

    reasons = [r_cpi, r_y10]
    if regime == "LOW":
        u = ind["unrate"]
        reasons.append(f"실업률 {u[-1]:.1f}% — 저물가 시대엔 실업률이 핵심 "
                       f"({'오르는 중' if u[-1] > u[-2] else '내려오는 중' if u[-1] < u[-2] else '제자리'})")
    head, lev, cash = PLAIN[stage]
    return dict(headline=head, reasons=reasons, lev=lev, cash=cash)


def next_cpi_outcomes(ind, th, lo=2.5, hi=4.5):
    """다음 CPI가 x%로 나오면 단계가 뭐가 되나 -> 같은 단계끼리 묶은 구간 목록.
    반환: [(구간 문구, 단계키), ...]  예) [('3.4% 이하', 'YELLOW'), ('3.5% 이상', 'ORANGE')]"""
    vals = [round(lo + i / 10, 1) for i in range(int(round((hi - lo) * 10)) + 1)]
    groups = []
    for v in vals:
        k, _ = scenario(ind, th, v)
        if groups and groups[-1][2] == k:
            groups[-1][1] = v
        else:
            groups.append([v, v, k])
    out = []
    for i, (a, b, k) in enumerate(groups):
        if len(groups) == 1:
            txt = "어떤 값이 나와도"
        elif i == 0:
            txt = f"{b:.1f}% 이하"
        elif i == len(groups) - 1:
            txt = f"{a:.1f}% 이상"
        else:
            txt = f"{a:.1f}%" if a == b else f"{a:.1f} ~ {b:.1f}%"
        out.append((txt, k))
    return out


def stage_triggers(ind, th, regime):
    """현재 말고 다른 단계로 가려면 무엇이 필요한가.
    반환: [dict(stage, logic('그리고'/'또는'), conds=[(조건, 충족여부, 지금 상태)]), ...]"""
    cpi, y10, hi = ind["cpi"][-1], ind["y10"], ind["y10_high"]
    up, down, n = rising_streak(ind["cpi"]), falling_streak(ind["cpi"]), th["streak"]
    if regime == "HIGH":
        return [
            dict(stage="RED", logic="그리고", conds=[
                (f"물가 {th['cpi_red']:.1f}% 이상", cpi >= th["cpi_red"], f"지금 {cpi:.1f}%"),
                (f"금리가 고점 {hi:.2f}% 돌파", y10 >= hi,
                 f"지금 {y10:.2f}%" + ("" if y10 >= hi else f" ({hi - y10:.2f}%p 남음)"))]),
            dict(stage="ORANGE", logic="또는", conds=[
                (f"물가 {th['cpi_red']:.1f}% 이상", cpi >= th["cpi_red"], f"지금 {cpi:.1f}%"),
                (f"물가 {n}개월 연속 상승", up >= n, f"지금 {up}개월째")]),
            dict(stage="GREEN", logic="그리고", conds=[
                (f"물가 {n}개월 연속 하락", down >= n, f"지금 {down}개월째"),
                (f"금리 {th['y10_green']:.1f}% 아래", y10 < th["y10_green"],
                 f"지금 {y10:.2f}%" + ("" if y10 < th["y10_green"]
                                      else f" ({y10 - th['y10_green']:.2f}%p 내려와야)"))]),
            dict(stage="GREEN2", logic="그리고", conds=[
                ("🟢 조건 충족", down >= n and y10 < th["y10_green"], ""),
                (f"금리 {th['y10_strong']:.1f}% 아래", y10 < th["y10_strong"], f"지금 {y10:.2f}%"),
                ("연준 금리 인상 중단", bool(ind.get("fed_pause")), "")]),
        ]
    u = ind["unrate"]
    low12, udown = min(u[-12:]), falling_streak(u)
    return [
        dict(stage="RED", logic="", conds=[
            (f"실업률이 1년 최저({low12:.1f}%)보다 {th['unrate_rise']:.1f}%p 이상 상승",
             u[-1] - low12 >= th["unrate_rise"], f"지금 {u[-1]:.1f}%")]),
        dict(stage="GREEN", logic="", conds=[
            (f"실업률 {n}개월 연속 하락", udown >= n, f"지금 {udown}개월째")]),
        dict(stage="GOLD", logic="그리고", conds=[
            (f"실업률 {n}개월 연속 하락", udown >= n, f"지금 {udown}개월째"),
            (f"물가 {th['gold_cpi']:.1f}% 이하", cpi <= th["gold_cpi"], f"지금 {cpi:.1f}%")]),
    ]
