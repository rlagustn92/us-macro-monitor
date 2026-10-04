# 미국 금리 · 물가 · WTI 유가 모니터

Streamlit 대시보드. API 키 없이 무료 공개 데이터만 사용합니다.

| 탭 | 내용 | 출처 / 갱신 |
|---|---|---|
| 국채 금리 | 3개월·5년·10년·30년 장중 시세, 전 만기 수익률 곡선, 10년−2년 금리차 | Yahoo Finance (약 10~15분 지연, 1분마다 자동 갱신) · 미 재무부 (매 영업일) |
| 물가 | CPI / 근원 CPI 전년比, 5년·10년 기대인플레(BEI) | BLS (월 1회) · 미 재무부 TIPS (매 영업일) |
| WTI 유가 | 근월물 선물 장중 · 1년 일봉 | Yahoo Finance `CL=F` |
| 나스닥 트라이팟 | 250일선 / VIX 10일 평균 / 52주 낙폭 규칙 판정 | Yahoo Finance `^NDX` `^VIX` (종가 기준) |

```bash
pip install -r requirements.txt
streamlit run app.py
```

공개된 자료를 기계적으로 계산해 보여줄 뿐이며 투자 권유가 아닙니다.
