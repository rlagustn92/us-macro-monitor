# 물가·금리 신호 모니터

미국 물가(CPI)·10년물 금리·실업률로 **레짐(고물가/저물가)과 신호 단계(🔴🟠🟡🟢🟢🟢)** 를 판정하고,
국채 금리·반도체·WTI 유가·나스닥 트라이팟을 함께 보는 Streamlit 대시보드. API 키 불필요.

데이터는 1시간마다 새로 받고, 화면의 **🔄 데이터 갱신** 버튼으로 즉시 받을 수 있습니다.

| 탭 | 내용 | 출처 |
|---|---|---|
| 🧭 신호 판정 | 레짐·단계 판정, 단계별 액션, 버블 붕괴 조건, CPI 시나리오 계산기, 레버리지 손절/익절 점검 | BLS · 미 재무부 |
| 📊 시차 분석 | CPI·10년물 정점 이후 주가 고점/바닥까지 개월 수, 시차별 상관계수 | BLS/FRED · Yahoo Finance (월봉) |
| 🎯 CPI 서프라이즈 | 발표 직전 나우캐스트 vs 실제치, 발표 당일 S&P500·나스닥·SOX·10년물 반응 (2013~) | 클리블랜드 연준 · Yahoo Finance |
| 국채 금리 | 장중 시세, 전 만기 수익률 곡선, 10년−2년 금리차 | Yahoo Finance (약 10~15분 지연) · 미 재무부 |
| 물가·고용 | CPI/근원 전년비·전월비, 실업률, 5년·10년 기대인플레(BEI) | BLS (실패 시 FRED) · 미 재무부 TIPS |
| 반도체 | SOX·SOXL·SOXX·SMH와 주요 반도체주 수익률, 반도체 vs 10년물 | Yahoo Finance |
| WTI 유가 | 근월물 선물 장중 · 1년 | Yahoo Finance `CL=F` |
| 나스닥 트라이팟 | 250일선 / VIX 10일 평균 / 52주 낙폭 규칙 판정 | Yahoo Finance `^NDX` `^VIX` |

```bash
pip install -r requirements.txt
streamlit run app.py
python -m pytest                      # 신호 판정 · 시차 분석 테스트
```

(선택) `.streamlit/secrets.toml` 또는 Streamlit Cloud Secrets에 `FRED_API_KEY = "..."` 를 넣으면 CPI·고용보고서 발표일이 자동으로 채워지고 장기 CPI도 FRED에서 받습니다.

신호 규칙은 `macro_rules.py`(순수 함수)에 있고, 임계값 기본값은 그 파일의 `DEFAULTS`에서 바꿉니다.

이 대시보드는 특정 인터뷰 영상의 프레임워크와 개인적으로 정한 휴리스틱 규칙을 시각화한 것이며, 투자 자문이 아닙니다.
