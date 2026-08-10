# 03. 시나리오 모델링 설계

> **단계**: 3단계 — 시나리오 모델링 (LLM 사용 — 옵션 C: narrative + 확률 + 트리거)
> **상위 문서**: [`CHARTER.md`](../CHARTER.md), [`CLAUDE.md`](../CLAUDE.md)
> **선행 문서**: [`docs/02-bull-bear.md`](02-bull-bear.md) — 본 단계 입력원, M2 운영 중
> **후행 문서**: `docs/04-optimizer.md` — 본 단계 출력(`ExpectedReturn`)을 입력으로 받음
> **버전**: v0.17 (2026-08-04)
> **상태**: 구현 #1~#12 완료 + #13 로컬 PoC (잔여: 자동화·#14 DeepEval) · 4주 운영 회고 완료 · 자동 운영 중 (유효 regime 2026-07-14~)
>
> **v0.17 변경 (`bear_price_cap_pct=0.0` primary 승격 — §12.3 bear semantics 결정)**:
> ① 증거 4주 일관 (retro §0.5): 시뮬 07-20 위반 7→1 / 실운영 07-27 7→1 / 08-03 6→0. cap 은 정확히 bear>current 유형만 제거, 다른 종목 ER Δ=0. 잔여 base<bear 유형(ADM/TKO)은 주당 ≤1건 — §12.3 별도 관찰 항목으로 유지.
> ② 기본값 None→**0.0** ("bear 시나리오는 최소한 상승이 아니다"). LLM bear narrative 와 가격 방향 정합 회복 — §7.2 `realized_scenario` calibration 오염 차단 (#13 가동 전 선결).
> ③ §4.4 대안 "bear_capped" → **"bear_uncapped"**(None) 교체 — 구 동작을 counterfactual 로 계속 관찰 (승격 결정의 사후 검증).
> ④ **4단계 선행 조건 해소** — `docs/04-optimizer.md` 착수 가능. primary ER 은 08-03 기준 또 한 번 regime 경계 (이전 주차와 분포 비교 시 주의).
>
> **v0.16 변경 (bear 가격 역전 대응 — `bear_price_cap_pct` 신설)**:
> ① **배경**: epsDiluted 수정(retro §0.7) 후 peer P/E 갈래 부활 → 딥밸류 종목에서 peer 함의 적정가 ≫ 현재가 + bear `conservative=max` 상호작용으로 **bear > bull 가격 역전 7/20** 2주 연속 (retro §0.5 07-14/07-20). 양수 ER 상위가 역전 종목에 집중 (07-20: 6/7) — 4단계 optimizer 오염 + §7.2 `realized_scenario` calibration 오염 위험.
> ② **§4.2 `bear_price_cap_pct` 신설** (base cap 과 대칭, 기본 **None = primary 불변**). §4.4 대안에 **`bear_capped`**(=0.0, bear ≤ 현재가) 추가 — A/B 관찰 후 §12.3 에서 primary 승격 판단.
> ③ **07-20 오프라인 시뮬레이션** (같은 opinion/context, config 만 변경): 역전 7→1 (잔여 ADM 은 base<bear 인 별개 유형), 영향은 정확히 역전 6종목에 국한 (EIX ER +40.9%→+0.1% 등), 나머지 14종목 Δ=0. 음수 skew 13/20 불변.
>
> **v0.15 변경 (4주 운영 회고 + #12 sensitivity 구현)**:
> ① **4주 회고** (`docs/03-scenario-retro.md §0.6`): 운영 health 전 기준 합격 (성공 80/80, 재시도 0%, $1.45/월, flags 0/80). 산출물 음수 skew 82.5%(4주 일관, 원인 base_cap=0.0) 확인.
> ② **#12 sensitivity 구현** (§4.4) — `ExpectedReturnsBundle` (primary + alternatives) + `alternative_configs`(balanced/base_cap_10/aggressive). lambda_core 가 같은 ScenarioOpinion 에 대안 config 병렬 산출(추가 LLM 비용 0) → S3 `expected_returns/` 에 Bundle 저장. 음수 skew config 결정을 4단계 설계 시 데이터(A/B)로.
> ③ **§12 결정**: config 기본값 변경은 #12 A/B 누적 후 4단계 시 (지금 미변경) / `_validate_price_order` 유지(config 완화 안전망) / §5.2 재시도 추정→**실측 0%**(4주).
> ④ §1.4.2 최종 성공 판정(Brier/적중률/portfolio)은 변동 없이 이월 (12주 + #13 + 4단계).
>
> **v0.14 변경 (M3 첫 운영 피드백 — narrative 길이 완화)**:
> ① 첫 20종목 운영에서 `narrative` 300자 한계로 **3종목(CRL/WTW/EIX) 검증 실패** (Sonnet 2회+Haiku 폴백 전부 narrative >300자). evidence 인용(hard rule #3) narrative 가 구조적으로 300자 초과 빈번 — 골든 4종목은 우연히 통과한 케이스.
> ② §2.2 `Scenario.narrative` max_length **300 → 500** (hard limit), 프롬프트 타깃 **350자** (타깃 < 상한 = overshoot 흡수). 골든 스냅샷(≤300)은 완화된 스키마에서도 유효 — 재생성 불필요.
> ③ 부수 관찰 (M3 회고 입력, §12.3): 실운영 재시도율이 M2(0%)보다 높음 (§5.2 "+20% ceiling" 가정 검증) / expected_return 음수 skew (보수 config — 4주 누적 후 §12.3 config 조정 판단).
>
> **v0.13 변경 (전체 정독 — 누락 갭 3건 교정)**:
> ① **bull/bear 가격 양쪽 결측 fallback** (§4.1/§9) — `compute_bull/bear_price` 가 `combine()` 결과를 그대로 반환해 *historical·peer 둘 다 결측 시 None → crash*. `current_price` fallback 추가 (base 선례). §9 에 "52w 결측"·"양쪽 결측" 행 추가.
> ② **시나리오 캐시 키/결정성 정책** (§6.2) — `lambda_core` 의 캐시 키가 미정의였음. M2 `context_input_hash` 패턴 재사용 — `scenario_input_hash = hash(ScenarioContext − lineage)`. 재실행 폭주 방지 + 재현성 (CHARTER 2순위). §5.2 cache 주장의 실제 메커니즘.
> ③ **stale 정정** — §9 "MaxConcurrency=1" → **2** (v0.8 G3 반영 누락).
>
> **v0.12 변경 (§12 재구조화 — stale 해소 + 4그룹화)**:
> ① **stale 해소** — 이미 다른 섹션에서 결정된 2건을 `[x]` 로 정정: *확률 calibration 평가 방법* (v0.10 §7.2 realized bin+Brier 로 정의 완료, 측정은 성공 기준 (1) 로 중복 병합), *variance floor 책임 경계* (v0.6 §부록 B 4단계 확정).
> ② **4그룹 재배치** — 22개 flat → 12.1 ✅이미 해결(3) / 12.2 🟢즉시 결정 가능(4) / 12.3 🔴데이터·운영 게이트(12) / 12.4 🔵v2 연기(3). 중복(calibration 방법↔측정) 병합.
> ③ 새 *결정* 없음 — 순수 정리. 12.2 의 P1-E/P2-H/D/guidance_change 는 *결정 가능* 으로 분류만 (확정은 구현 티켓).
>
> **v0.11 변경 (§10.3 golden 의미 정정 — CLAUDE.md 정합)**:
> ① **생성 vs replay 분리** — CLAUDE.md `golden` 마커 = *실제 LLM 호출 없이* 스냅샷 검증인데 §10.3 이 "실제 호출"과 혼동. *생성* (`run_scenario_golden.py` 스크립트, 1회 $0.072) ↔ *테스트* (스냅샷 replay, LLM 호출 0) 분리.
> ② **assert 명세 추가** — M2 골든 패턴 따라 구조·정책 검증 열거. *scenario 는 M2 보다 강함* — 저장 ScenarioOpinion → `compute_expected_return` 재실행 시 `ExpectedReturn` 이 결정적으로 정확 재현 (exact assert). + narrative 가격숫자 미출현(hard rule #1)·prob 합·라벨/enum.
> ③ **마커 기존재 정정** — `golden` 마커는 이미 `src/pytest.ini` 에 등록됨 (범용) → §10.3·§11 #8 의 "추가/등록" 을 "재사용" 으로 정정.
> ④ (A context_builder / C 신규 트리거 로직 / D ExpectedReturnsBundle 테스트 갭은 검토했으나 이번 박제 범위 밖 — 필요 시 후속.)
>
> **v0.10 변경 (§7 트리거 자동 검증 — 스키마·calibration 정의)**:
> ① **A `TriggerEvaluation` 스키마 + S3 저장** — §7.1 이 정의 없이 쓰던 `TriggerEvaluation` 을 Pydantic 모델로 박제 (`actual`/`threshold`/`met`/`requires_human_review`/lineage) + S3 레이아웃 `trigger_evaluations/dt=*/symbol=*.json`.
> ② **C realized 시나리오 = 가격 bin** — §1.4.2 #1 Brier score 의 `1_{realized}` 를 *scenario_prices 중점 bin* 으로 정의 (다음 분기 발표일 가격, tripwire 동일 앵커). 성공 기준 측정 가능화.
> ③ **E observe-only 경계** — 트리거 평가는 *회고 calibration 전용*, M3 매매·재분석에 라이브 피드백 없음 (CHARTER §6 매매는 룰 기반 정합) 명시.
> ④ **B/D/F 는 #13 위임** — 시간 조인(B)·배치 메커니즘(D)·FMP 신선도(F)는 운영·메커니즘 의존이라 §12 박제 후 #13 구현 시 결정.
>
> **v0.9 변경 (§11 구현 순서 의존성 교정 — 2건)**:
> ① **#2/#3 순서 교정** — `pricing.py` 의 `compute_*_price(cfg)` 가 `ScenarioPricingConfig` 를 import 하므로 `pricing_config.py` 가 먼저여야 함. 순서: schemas → **pricing_config** → pricing → … 모델은 pricing_config.py 확정 (§3.1·부록A·§10.1·M2 선례 정합), §11 #1 의 ScenarioPricingConfig 표기 제거.
> ② **percentile = numpy 미사용 결정** — §4.1 P2-#4 의 "numpy 기본" 을 *신규 Lambda 의존성 가시성* 하에 재조정. numpy 사용처는 percentile 1곳뿐 (variance 는 순수 Python) → **손수 linear interp ~10줄** (numpy 'linear' 정확 일치, dep 0, 콜드스타트 최소). §4.1 에 `percentile()` 정의 추가, §10.1 테스트 표기 갱신. requirements.txt 변경 없음.
> ③ P2 노트 — #7 trigger_evaluator 는 #1 에만 의존 (활성화는 #13), #8 골든에 `pytest.ini golden 마커` 명시, DeepEval baseline(§10.5)을 #14 로 추가.
>
> **v0.8 변경 (§6.1 ASL — 실파일 교차검증 교정 3건)**:
> ① **G1 체이닝 교정** — 현재 커밋된 ASL 의 `BullBearMap` 은 `End:true` + `ResultPath` 없음 → Map 출력이 `$.result` 를 덮어써 ScenarioMap 의 `ItemsPath:$.result.selected` 불가. `Next:ScenarioMap` + `ResultPath:"$.bullbear_results"` 보존 필수. §6.1 에 교정 ASL 박제.
> ② **G2 Catch·Retry 위치** — ScenarioMap 은 Parallel 없는 단일 Task → Catch[States.ALL]→RecordItemFailure 를 *Task 직착*, M2 와 동일한 Lambda throttle Retry 블록 (4종 에러) 적용. §9 교정.
> ③ **G3 MaxConcurrency 1→2** — single-stance 라 `2×2048=4,096 < 8,000` = M2 BullBearMap(=1, 2 stance) 과 동일 부하, M2 4주 안정 운영으로 검증된 수준. 시나리오 단계 wall-clock 2x 단축.
>
> **v0.7 변경 (§5 비용 — M2 실측 교차검증 주석)**:
> ① §5.1 — 입력 토큰 추정이 *golden(#8) 전 미검증* 임을 명시. M2 실측 (추정 ~3,250 → 실제 1,551, 2x 과대) 근거로 시나리오 실측은 ~2,400~2,700 예상 — 추정이 보수적(상향)이라 예산 안전.
> ② §5.2 — 재시도 +20% 가 *보수 ceiling* 임을 M2 실측 (160 invoke retry/fallback 0건) 으로 박제. S3 result cache 는 *재실행 폭주 방어용* (주간 정규 호출은 80 calls 풀 카운트) 명확화. v2 헤드룸 정량화 (Self-Consistency 5샘플 = $7.25/월 = cap 3.6%).
> ③ 결론: §5 추정 모두 보수적, 과소추정·누락 위험 없음 — 주석만 추가, 수치 변경 없음.
>
> **v0.6 변경 (§4.2 ScenarioPricingConfig 충분성 검토 — 3건)**:
> ① **잉여 제거** — `historical_window_days` 삭제. §4.1 산식이 `ctx.return_52w_*` (사전 계산값) 만 쓰고 이 파라미터를 읽는 함수가 없는 *phantom knob* 이었음. 9→8 필드. multi-window 실제 구현 시 재추가 (§12 park).
> ② **검증 하드닝 2건** — (a) `model_validator` 로 `peer_pe_bear ≤ base ≤ bull percentile` 강제 (config 레벨 가격 역전 사전 차단, §4.1 `_validate_price_order` 의 근본 원인). (b) `base_price_cap_pct` 에 `ge=-0.5, le=1.0` bound (무경계 latent bug 제거).
> ③ **누락 검토 — 확률 floor 거부** — `min_scenario_probability` 는 옵션 C 의 "LLM 확률 신뢰" 철학과 충돌 + cap 이 이미 임시 가드라 불신 이중화. §12 에 calibration 결과 tail 과소평가 확인 시 도입 후보로 park. variance floor 는 4단계 책임으로 §부록 B 경계 메모.
>
> **v0.5 변경 (§2.4 검토 마무리 — P1-G 확정 + 잔여 위임)**:
> ① **P1-G 확정** — `invalidation_trigger` 의 평가 윈도우를 *tripwire* (다음 분기 발표 1회 고정) 로 결정. schema 무변경 (`evaluation_window` 필드 미도입). §7.1 에 윈도우 의미 박제, §12 에 다중 윈도우 v2 후보 추가.
> ② **잔여 4건 구현 티켓 위임** — schema 구조를 바꾸지 않는 항목 (P1-E unit validator → #1 schemas.py / P2-H label↔direction 의미 → #5 prompts / P2-D guidance_change 자동화 → #7 trigger_evaluator / P2-C peer_announcement 정책 → 운영 5주차 회고) 은 §11 구현 순서 해당 티켓에서 결정. §12 에 결정 시점 박제. *이유*: 모두 기존 필드 위에 얹는 validator·프롬프트·evaluator 로직 → 첫 운영(#11) 전에는 무손실 추가 가능.
> ③ §2.4 Metric Enum 검토 종료 — P1-A(enum 확장, v0.4) + P1-E/P1-G(v0.5) + P2-C/D/H(위임) 처리 완료.
>
> **v0.4 변경 (§2.4 Metric Enum 확장 P1-A — 2건)**:
> ① §2.2 `InvalidationTrigger.metric` Literal 8 → 10 확장 — `earnings_surprise` (vs 컨센서스, FMP `earnings-surprises` 신규) + `net_debt_yoy` (BS 캐시 재활용, 인프라 0 추가).
> ② §2.4 자동 측정 비율 6/8 = 75% → 8/10 = 80% 향상. §3.2 시스템 프롬프트에 `net_debt_yoy` 의 Financials/Utilities sub_sector 사용 자제 명시. §12 미해결 항목에 *거부 후보 5개* (valuation_multiple / analyst_estimate_revision / dividend·buyback / sector_specific / macroeconomic) 거부 근거 박제. P1-E (metric↔unit validator) 와 P1-G (트리거 시간 축) 는 별도 박제 예정.
>
> **v0.3 변경 (§4 가격 산정 공식 정밀화 — 4건)**:
> ① §4.1 `combine()` 의 *bear case 의미 분기* — `bear_conservatism='conservative'` 는 *작은 하락* (= `max(historical, peer)`) 을 의미. `is_bear=True` 플래그로 bull/bear 비대칭 처리. (이전 v0.2 산식은 bear 에서 `conservative=min` 으로 *큰 하락* 을 산출하는 의미 불일치).
> ② §4.1 `_apply_probability_cap` 의 *잉여 확률 재분배* 동작 명시 — 나머지 시나리오의 *원래 비율* 로 비례 분배. `bear_probability_cap` 대칭 추가 (§4.2).
> ③ §4.1 *bull/base/bear 가격 순서 검증* — `_validate_price_order` 가 위반 시 warning 로그 + `ExpectedReturn.data_quality_flags` 기록. 산식 자동 보정 없음 (lineage 보존 우선).
> ④ §2.3 `ExpectedReturn` 에 `data_quality_flags: list[str]` 필드 신설 — 위 #3 + 향후 데이터 품질 신호 누적용. §10.1 단위 테스트 항목에 분기·재분배·순서 검증 추가.
>
> **v0.2 변경 (§1.4 옵션 비교 보강 — 기록 보존)**:
> ① §1.4.1 신설 — 옵션 C 의 *잠재 약점 4건* 명시적 박제 (확률 calibration 사전 검증 불가 / `invalidation_trigger` 생성 불가 케이스 / 가격 산식 보수성 / 5단계 cross-validation 부재). 각 약점에 완화책·측정 시점 명기.
> ② §1.4.2 신설 — *옵션 C 채택의 사전 성공 기준 3건*. 회고 시점 (M3 말, 12주 누적) 정량 측정: (1) 확률 calibration Brier score < 0.25, (2) 트리거 적중률 ≥ 60%, (3) portfolio outcome 옵션 B baseline 대비 동등 또는 우월. 측정 인프라는 `trigger_evaluator` + `ExpectedReturnsBundle.alternatives["option_b_baseline"]`.
> ③ §4.4 ExpectedReturnsBundle 에 옵션 B baseline 슬롯 정의 추가 (§1.4.2 #3 인프라).
> ④ §12 미해결 항목에 *옵션 C 성공 기준 측정* + *§1.4.1 약점별 완화책 활성화 시점* 추가.

---

## 0. TL;DR

Bull/Bear 의견을 입력으로 받아 *3 시나리오 (bull/base/bear) × 확률 + 무효화 트리거*를 LLM 으로 생성, 코드 측 결정적 산식이 가격 범위 + expected return + variance 산출. 4단계 포트폴리오 최적화의 입력.

- 모델: **Sonnet 4.6** (Haiku 4.5 폴백 — 02-bull-bear §4.2 동일 사다리)
- 호출량: 종목당 1회 × 주 15~20 종목 = **주 15~20회 / 월 ~60~80회**
- 예상 비용: **월 ~$1.2~1.6** (CHARTER §3.3 시나리오 단계 추정과 정합)
- 출력: Pydantic 검증 JSON (`ScenarioOpinion`) + 결정적 산식으로 변환된 `ExpectedReturn`
- 핵심 결정: **LLM 은 가격 숫자를 만들지 않음** — 확률 + narrative + 측정 가능 트리거만 생성. 가격 범위는 *historical · peer 데이터 기반 결정적 산식*

---

## 1. 목적과 범위

### 1.1 무엇을 하는가
1. **입력**: 종목 1개의 Bull/Bear 의견 (2 × `BullBearOpinion`) + 가격 컨텍스트 (현재가, TTM EPS, peer P/E, OHLCV 52w high/low)
2. **LLM 처리**: 3개 시나리오 생성 — 각 시나리오는 `(label, probability, narrative, invalidation_trigger)`. 확률 합 = 1.0
3. **코드 처리**: ScenarioPricingConfig + 시장 데이터 → 각 시나리오 가격 → expected return + variance
4. **출력**: `ExpectedReturn` (4단계 최적화의 입력) + `ScenarioOpinion` (원본 LLM 출력, 트리거 자동 검증용)

### 1.2 비범위 (이 단계가 하지 않는 것)
- ❌ **LLM 이 가격 숫자 추정** — Bull/Bear 케이스 가격은 코드 산식이 결정 (옵션 A 거부 근거 §1.4)
- ❌ **포지션 사이징·섹터 가중** (4단계 최적화)
- ❌ **매수/매도 결정** (5단계 룰 기반 리밸런서)
- ❌ **옵션·선물·레버리지·공매도** (CHARTER §5 — bear scenario 도 매수 안 함, 위험 측정용)
- ❌ **백테스트** (별도 퀀트 트랙)
- ❌ **시계열 시뮬레이션** (확률 가중 단일 시점 expected return 만, monte carlo X — v2 후보)

### 1.3 CHARTER 정합성 체크
| 원칙 | 본 설계 적용 |
|---|---|
| 1순위 LLM 학습 | 옵션 C 자체가 *Self-Verification 패턴* (트리거로 자기 시나리오 검증 가능) 학습 포인트 |
| 2순위 산출물 | LLM 출력(`ScenarioOpinion`) + 산식 결과(`ExpectedReturn`) + 사용된 config 모두 S3 보존 → 사후 재현 |
| 3순위 퀀트 리서치 | 가격 산정 공식·config 가 외부화 → sensitivity 실험 가능 |
| 비용 상한 월 $200 | 본 단계 월 ~$1.2~1.6 — 2단계 ($2.76) + 3단계 합쳐도 hard cap 의 2~3% |
| 매매는 룰 기반 (§6) | **LLM 추론 영역 (시나리오·확률) 과 가격 산정 (결정적 코드) 분리** — 할루시네이션이 매매 가격을 직접 결정하지 않음 |

### 1.4 옵션 비교 — 왜 옵션 C 인가

설계 시점 (2026-05-25) 에 3 가지 옵션을 검토:

| 옵션 | LLM 산출 | 할루시네이션 리스크 | 사후 검증 | 4단계 인터페이스 | 결정 |
|---|---|---|---|---|---|
| **A** 가격 타깃 | "12M target = $250" 직접 추정 | 높음 (숫자 자체) | 어려움 | 매끄러움 | ✗ 거부 |
| **B** 코드 점수화만 (LLM 호출 X) | (없음) | 0 | N/A | 단순 | ✗ CHARTER §3.3 LLM 사용 명시와 어긋남 |
| **C** narrative + 확률 + 트리거 | "Bull 시나리오 40%, 트리거: Q2 revenue < $35B" | 낮음 (숫자 X, 검증 가능 트리거 O) | **쉬움** (트리거 객관 측정) | 변환 1단계 (산식) | ✅ **채택** |

옵션 C의 결정적 강점: **LLM 추론 영역(시나리오·확률·narrative) ≠ 가격 산정 영역(결정적 산식)** 의 깔끔한 분리. CHARTER §3.3 LLM 사용 충족 + §6 할루시네이션 리스크 보수 + Self-Verification 패턴 학습.

#### 1.4.1 옵션 C 의 잠재 약점 (명시적 박제)

옵션 C 채택의 정직성을 위해 잠재 약점을 박제. 이는 §12 미해결 항목과 §1.4.2 성공 기준의 *측정 대상*.

1. **확률 calibration 의 사전 검증 불가**
   - LLM 이 "Bull 시나리오 60%" 라고 산출해도 *실제로 그 빈도로 발생하는지*는 회고 시점 (최소 8~12주 운영 데이터 누적) 에야 측정 가능
   - 시작 시점에는 *unverified*. 시간이 지나야 §1.4.2 의 Brier score 등으로 정량 측정
   - 완화책: §11 구현 순서에 트리거 자동 검증 (#13) 포함 — 분기 발표 후 적중률 누적

2. **`invalidation_trigger` 생성 불가 케이스**
   - Enum (§2.4) 은 *정량적·재무 metric* 위주 — M&A, 경영진 교체, 규제·소송, supply chain 차단 같은 *비정량 이벤트* 는 `peer_announcement` 외에 표현 불가
   - 이런 종목 (예: 인수 루머 있는 종목) 의 시나리오는 *invalidation_trigger 의 description 에만 자유 텍스트로* 표현되어 자동 검증이 어려움
   - 완화책: §12 미해결 항목 "peer_announcement 트리거 정책" — M3 5주차 운영 데이터로 enum 확장 결정

3. **가격 산식의 보수성**
   - §4.1 공식은 `peer P/E + historical 52w high/low` 만 사용 → *새로운 모멘텀·acceleration* (예: 갑작스러운 R&D 성과·신규 수주) 을 가격 범위에 직접 반영 못 함
   - Bull 시나리오 narrative 에 그런 신호가 있어도 *가격은 peer 75th P/E × TTM EPS* 같은 정적 산식이 결정
   - 완화책: `bull_aggressiveness=aggressive` config 또는 §12 의 v2 Monte Carlo (현재 단순 단일 시점 → distribution 전환)

4. **5단계 (리밸런서) 와의 cross-validation 부재**
   - 옵션 C 산출이 *4단계 최적화에 좋은가* 는 본 단계 단독으로 평가 어려움 — 5단계까지 가야 portfolio outcome 측정 가능
   - 4주 운영으로는 부족. M3 말 (Phase 1 종료, 12주 누적) 회고에서 판단

#### 1.4.2 성공 기준 (사전 정의)

옵션 C 가 *잘 작동했다* 고 판단할 정량 기준을 회고 시점 (M3 말) 측정 대상으로 박제. 이 기준은 *Charter §4.1 실전 전환 기준* 의 본 단계 구체화.

| # | 측정 항목 | 측정 방법 | 합격 임계 (잠정) |
|---|---|---|---|
| 1 | **확률 calibration (Brier score)** | M3 12주 동안의 시나리오 출력 vs 분기 발표 후 실제 발생 비율. `BS = Σ (probability - 1_{realized})²` | < 0.25 (uniform predictor `0.33` 보다 우수) |
| 2 | **트리거 적중률** | invalidation_trigger 가 발생한 종목 (자동 측정 metric 만): 시나리오의 *경로* 가 실제와 일치했나 (예: bear 트리거 발동 → 가격 하락 동반) | ≥ 60% (단순 우연 50% 보다 유의) |
| 3 | **Portfolio outcome (vs B baseline)** | 옵션 B (LLM 호출 없는 코드 점수화) 를 *parallel* 로 산출해 4단계 최적화에 넣은 가상 portfolio 와 옵션 C portfolio 의 12주 누적 수익률·tracking error 비교 | 옵션 C 가 *동등 또는 우월* (degradation 없음). 우월하면 옵션 C 유지, 동등이면 비용 트레이드오프 재평가 |

**측정 인프라**:
- #1, #2 — `trigger_evaluator.py` (§7) 가 분기 발표 후 자동 누적
- #3 — `ExpectedReturnsBundle` (§4.4) 의 `alternatives` 슬롯에 *옵션 B baseline* 도 동시 산출. 추가 LLM 호출 없음, 추가 비용 0
- 모두 §11 구현 순서의 #12 (sensitivity 로깅) 와 #13 (트리거 자동 평가) 활성화 시 자동 수집

**판단 분기 — M3 말 회고 시**:
- 3 기준 *모두 합격* → 옵션 C 유지 + 기본 config 미세 조정 (§12 미해결 항목)
- 1~2 기준만 합격 → 옵션 C 유지 + 약점 보강 (§1.4.1 의 완화책 도입)
- *모두 미달* → 옵션 A (가격 타깃) 또는 옵션 B (LLM 호출 제거) 로 전환 검토. v0.x 재설계.

이 기준은 *Phase 1 종료 시 실전 전환 판단* (CHARTER §4.1) 의 본 단계 입력.

---

## 2. 입출력 스키마

### 2.1 입력 (`ScenarioContext`)

종목 1개에 대해 호출 직전 조립. **FMP 직접 호출 금지** — 캐싱 계층 (`common/fmp_client.py`, `common/fundamentals.py`) 경유.

```python
class ScenarioContext(BaseModel):
    # 1. Identity (from Bull/Bear)
    symbol: str
    company_name: str | None = None
    sector: str | None = None
    sub_sector: str | None = None
    as_of_date: date

    # 2. Bull/Bear 의견 (LLM 노출)
    bull_opinion: BullBearOpinion        # 02-bull-bear §2.2
    bear_opinion: BullBearOpinion

    # 3. 가격 컨텍스트 (LLM 노출 + 코드 산식 입력)
    current_price: float
    ttm_eps: float | None                # 코드 산식 입력. None 이면 P/E 기반 가격 산정 불가 (§4.1 처리)
    peer_pe: list[float]                 # 정렬 무관, 코드가 percentile 계산
    return_52w_high: float | None        # (52w_high - current) / current. 양수
    return_52w_low: float | None         # (52w_low - current) / current. 음수

    # 4. Lineage (audit/재현 — LLM 미노출)
    run_id: str
    scenario_s3_key: str                 # 본 단계 출력 저장 경로
    bullbear_s3_keys: dict[Literal["bull", "bear"], str]  # 입력 의견 원본
    data_quality_flags: list[str] = Field(default_factory=list)
```

**원칙**:
- Bull/Bear `BullBearOpinion` 을 그대로 임베드 — 평탄화 안 함 (Bull/Bear 자체가 이미 평탄화 1-depth)
- `current_price` / `ttm_eps` / `peer_pe` 는 LLM 프롬프트와 코드 산식 *양쪽 모두* 에 사용 — 가격 산정의 raw 데이터
- `peer_pe` 는 정렬되지 않은 리스트로 받음. 코드가 percentile 계산 (config 의 `peer_pe_*_percentile` 적용)

### 2.2 출력 (`ScenarioOpinion`)

LLM 응답을 Pydantic 검증.

```python
class InvalidationTrigger(BaseModel):
    """시나리오를 무효화하는 *측정 가능* 조건. 자유 텍스트 X — enum + threshold 구조."""
    metric: Literal[
        "revenue_yoy", "revenue_qoq", "eps_yoy", "fcf_yoy",
        "gross_margin_yoy", "operating_margin_yoy",
        "earnings_surprise",      # v0.4 — vs 컨센서스, T+0 측정
        "net_debt_yoy",           # v0.4 — BS 기반 레버리지 변화
        "guidance_change", "peer_announcement",
    ]
    direction: Literal["less_than", "greater_than"]
    threshold: float | None              # qualitative metric 이면 None
    threshold_unit: Literal["percent", "absolute_usd", "qualitative"]
    description: str = Field(min_length=10, max_length=200)


class Scenario(BaseModel):
    label: Literal["bull", "base", "bear"]
    probability: float = Field(ge=0.0, le=1.0)
    narrative: str = Field(min_length=20, max_length=500)
    invalidation_trigger: InvalidationTrigger


class ScenarioOpinion(BaseModel):
    symbol: str
    as_of_date: date
    scenarios: list[Scenario] = Field(min_length=3, max_length=3)

    # 호출 메타 (CLAUDE.md 로깅 규칙)
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _validate_scenarios(self) -> Self:
        labels = [s.label for s in self.scenarios]
        if sorted(labels) != ["base", "bear", "bull"]:
            raise ValueError(f"라벨 {labels} — bull/base/bear 각 1개씩 필요")
        total = sum(s.probability for s in self.scenarios)
        if not 0.99 <= total <= 1.01:
            raise ValueError(f"확률 합 {total:.3f} — 1.0±0.01 이어야 함")
        return self
```

**핵심 결정**:
- `invalidation_trigger` 가 자유 텍스트가 아닌 **(metric, direction, threshold) 3-tuple** — 코드 자동 검증 가능 (§7)
- `metric` Enum 8개는 *측정 가능한 것* 만 (§2.4 — 자동 측정 가능 6개 + 인간 검토 2개)
- 확률 합 1.0±0.01 강제 (LLM 부동소수점 오차 허용)
- `narrative` 20~500 자 (hard limit) — 프롬프트 타깃은 350자. v0.14 M3 첫 운영에서 300 한계가 빡빡(evidence 인용 narrative 가 ~310~400자로 자주 초과 → 3/20 종목 검증 실패)해 완화. 타깃 350 < 상한 500 으로 overshoot 흡수

### 2.3 가격 산정 출력 (`ExpectedReturn`)

LLM 출력 + 시장 데이터 + config 로 *결정적 코드* 가 산출.

```python
class ExpectedReturn(BaseModel):
    symbol: str
    as_of_date: date

    # 4단계 최적화의 직접 입력
    expected_price: float
    expected_return: float               # (expected_price - current_price) / current_price
    variance: float                      # 확률 가중 분산 — covariance matrix 입력

    # 시나리오별 가격 (감사·디버깅)
    scenario_prices: dict[Literal["bull", "base", "bear"], float]

    # 사용된 파라미터 (sensitivity 분석 필수)
    pricing_config: ScenarioPricingConfig

    # 데이터 품질 플래그 (v0.3 §4.1 결정 — 가격 순서 위반·peer_pe 결측 등)
    data_quality_flags: list[str] = Field(default_factory=list)

    # Lineage
    scenario_opinion_s3_key: str
    computed_at: datetime
```

**`data_quality_flags` 운영 가치**:
- Athena 조회 가능 — *어떤 종목·주차에 어떤 품질 이슈* 가 누적되는지 회고
- 4단계 최적화가 *이 플래그 기준 종목 제외/가중 감소* 정책 도입 가능 (M3 후반 검토)
- §4.1 가격 순서 위반 + (향후) peer_pe insufficient / ttm_eps_missing 같은 신호 누적용

### 2.4 Metric Enum — 자동 측정 가능 여부

| metric | 데이터 출처 | 자동 측정 |
|---|---|---|
| `revenue_yoy` | FMP `income-statement-quarterly` 최신 Q / 동기 전년 Q | ✅ |
| `revenue_qoq` | 최신 Q / 직전 Q | ✅ |
| `eps_yoy` | income-statement-quarterly `epsdiluted` | ✅ |
| `fcf_yoy` | `cash-flow-statement-quarterly` `freeCashFlow` | ✅ |
| `gross_margin_yoy` | `grossProfit / revenue` 비교 | ✅ |
| `operating_margin_yoy` | `operatingIncome / revenue` 비교 | ✅ |
| `earnings_surprise` *(v0.4)* | FMP `earnings-surprises` — actualEarningResult vs estimatedEarning | ✅ T+0 (분기 발표 직후) |
| `net_debt_yoy` *(v0.4)* | BS quarterly: `totalDebt - cashAndShortTermInvestments`, 전년 동기 대비 | ✅ — Financials/Utilities sub_sector 제외 권장 (§3.2) |
| `guidance_change` | FMP earnings transcript / press release 텍스트 | ⚠ semi-auto (텍스트 분석 필요, M3 후반 또는 인간 검토) |
| `peer_announcement` | 외부 뉴스 | ❌ 인간 검토 only |

자동 측정 비율: **8/10 = 80%** (v0.3 까지 75% — v0.4 에서 +5%p).

시스템 프롬프트가 *자동 측정 가능 metric 선호* 를 명시 (§3.2). LLM 이 `peer_announcement` 를 남발하면 사후 검증 자동화 불가.

**v0.4 추가 metric 의미**:
- `earnings_surprise`: *상대 (vs 컨센서스)* 차원. `eps_yoy` 의 *절대 (vs 전년)* 차원과 보완. 분기 발표 T+0 측정 → 빠른 회고 데이터 누적
- `net_debt_yoy`: BS 기반 *레버리지 변화*. bear "레버리지 위험" / bull "balance sheet 정리" 가설 검증. Financials (부채가 비즈니스) / Utilities (구조적 고부채) 는 의미 다름 → 시스템 프롬프트가 사용 자제 명시

---

## 3. 프롬프트 설계

### 3.1 파일 배치 (CLAUDE.md 규칙)

```
src/agents/
├── scenario/
│   ├── __init__.py             # namespace package — 빈 파일 가능 (deploy_lambda.sh 자동 생성)
│   ├── agent.py                # 호출 진입점 (Bull/Bear agent.py 와 동일 사다리 패턴)
│   ├── context_builder.py      # ScenarioContext 조립 + to_prompt_markdown
│   ├── lambda_core.py          # Lambda 공유 코어 (캐시 hit/miss 분기)
│   ├── pricing.py              # 결정적 가격 산식 (§4) — LLM 호출 없음, 순수 함수
│   ├── pricing_config.py       # ScenarioPricingConfig + 기본값
│   ├── trigger_evaluator.py    # 트리거 자동 검증 (§7) — 별도 모듈
│   └── schemas.py              # ScenarioContext, ScenarioOpinion, ExpectedReturn 등
└── prompts/
    └── scenario_system.md      # 시스템 프롬프트
    └── scenario_user.md        # 사용자 프롬프트 템플릿 (placeholder 4개)
```

`AnthropicSDKCaller` 는 `agents/bull_bear/anthropic_adapter.py` 를 재사용 (모듈 공유).

### 3.2 시스템 프롬프트 핵심

```
당신은 한 종목에 대한 다중 시나리오 분석가다. Bull 의견과 Bear 의견을
입력으로 받아 3개의 시나리오 (bull / base / bear) 를 생성한다.

## Hard rules

1. **No price estimation**. 가격 숫자를 출력하지 않는다. 시나리오 가격은
   downstream 의 결정적 산식이 계산한다. probability 와 narrative,
   invalidation_trigger 만 생성.
2. **Probabilities sum to 1.0**. 3개 시나리오 확률 합은 1.0 (±0.01 허용).
3. **Triggers must be measurable**. invalidation_trigger.metric 은 정해진
   enum 에서만 선택. 가능하면 자동 측정 가능한 metric (revenue_yoy, eps_yoy,
   margin_yoy, earnings_surprise, net_debt_yoy 등) 을 선호. peer_announcement
   같은 qualitative 트리거는 다른 측정 가능한 metric 으로 표현 불가능할 때만
   사용. `net_debt_yoy` 는 Financials/Utilities sub_sector 종목에서는 부채
   자체가 비즈니스 모델이므로 사용 자제 (v0.4).
4. **Narratives reference Bull/Bear evidence**. 각 시나리오의 narrative 는
   입력의 Bull 또는 Bear 의견에서 구체 evidence 를 인용해야 한다 (예:
   "Bull #2 의 FCF margin 확장 가설이 사실이면").
5. **JSON only**. 정해진 schema 만 반환.

## Output schema (strict — do not deviate)

```json
{
  "scenarios": [
    {
      "label": "bull" | "base" | "bear",
      "probability": 0.0~1.0,
      "narrative": "string, 20~350 chars, citing Bull/Bear evidence",
      "invalidation_trigger": {
        "metric": "revenue_yoy" | "revenue_qoq" | "eps_yoy" | "fcf_yoy" |
                  "gross_margin_yoy" | "operating_margin_yoy" |
                  "earnings_surprise" | "net_debt_yoy" |
                  "guidance_change" | "peer_announcement",
        "direction": "less_than" | "greater_than",
        "threshold": number | null,
        "threshold_unit": "percent" | "absolute_usd" | "qualitative",
        "description": "string, 10~200 chars"
      }
    },
    { "label": "...", ... },
    { "label": "...", ... }
  ]
}
```

Critical rules:
- `scenarios` length must be exactly 3, labels = bull/base/bear (one each)
- probabilities sum to 1.0 (±0.01 tolerance)
- `metric` must be from the enum — do not invent new ones
- `threshold` is null ONLY when `threshold_unit == "qualitative"`
- prefer auto-measurable metrics over qualitative when possible
```

### 3.3 사용자 프롬프트 (placeholder)

```
{context}

---

Task: based on Bull/Bear opinions and price context above, generate 3
scenarios for **{symbol}** as of **{as_of_date}**.

Reminders:
- Do NOT estimate prices — only probability + narrative + invalidation_trigger.
- Probabilities must sum to 1.0.
- Each scenario's narrative must cite specific evidence from the Bull or Bear opinion.
- Invalidation triggers must use auto-measurable metrics when possible.

Return only the JSON object matching the schema.
```

`{context}` 는 `to_prompt_markdown(ScenarioContext)` 가 생성:
1. Identity (symbol, sector, as_of_date)
2. 가격 컨텍스트 (현재가, TTM EPS, peer P/E percentile 사전 계산값, 52w high/low return)
3. Bull 의견 (summary + arguments + key_risks)
4. Bear 의견 (summary + arguments + key_risks)

Bull/Bear `to_prompt_markdown` 과 유사한 화이트리스트 직렬화 (lineage 필드 미노출).

---

## 4. 가격 산정 — 결정적 코드

### 4.1 시나리오 가격 공식

각 시나리오의 가격은 *historical 데이터 + peer P/E + TTM EPS* 결합. config (§4.2) 가 결합 방식 결정.

#### Bull case price
```python
def compute_bull_price(
    current_price: float,
    return_52w_high: float | None,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    historical_target = (
        current_price * (1 + return_52w_high)
        if return_52w_high is not None else None
    )
    peer_target = (
        percentile(peer_pe, cfg.peer_pe_bull_percentile) * ttm_eps
        if (ttm_eps and ttm_eps > 0 and peer_pe) else None
    )
    price = combine(historical_target, peer_target, mode=cfg.bull_aggressiveness)
    return price if price is not None else current_price   # v0.13 — 양쪽 결측 시 fallback (base 선례)

def combine(a, b, mode, *, is_bear=False):
    """둘 중 하나만 있으면 그 값. 둘 다 있으면 mode 에 따라 결합.
    둘 다 None 이면 None (호출자가 current_price fallback).

    v0.3 §4.1 결정 — bear case 의미 분기:
    - bull (is_bear=False): conservative=min(a,b) (작은 상승), aggressive=max(a,b)
    - bear (is_bear=True):  conservative=max(a,b) (작은 하락), aggressive=min(a,b)
    balanced 는 두 케이스 모두 산술평균 — 의미 비대칭 없음.
    """
    if a is None and b is None:
        return None
    if a is None: return b
    if b is None: return a
    if mode == "balanced": return (a + b) / 2
    if is_bear:
        return max(a, b) if mode == "conservative" else min(a, b)
    return min(a, b) if mode == "conservative" else max(a, b)


def percentile(xs: list[float], q: float) -> float:
    """순수 linear-interpolation percentile (numpy 'linear' 방식 정확 재현).
    v0.9 — numpy 미사용 (peer_pe percentile 1곳 전용이라 신규 dep 회피,
    CLAUDE.md 콜드스타트 최소). q 는 0~100. 호출자가 빈 리스트 사전 차단."""
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    rank = (q / 100) * (len(s) - 1)
    lo = int(rank)
    if lo + 1 >= len(s):
        return s[-1]
    return s[lo] + (rank - lo) * (s[lo + 1] - s[lo])
```

#### Base case price
```python
def compute_base_price(
    current_price: float,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    peer_target = (
        percentile(peer_pe, cfg.peer_pe_base_percentile) * ttm_eps
        if (ttm_eps and ttm_eps > 0 and peer_pe) else None
    )
    if peer_target is None:
        return current_price       # fallback — fair value 산정 불가
    if cfg.base_price_cap_pct is None:
        return peer_target
    cap = current_price * (1 + cfg.base_price_cap_pct)
    return min(peer_target, cap)
```

#### Bear case price
```python
def compute_bear_price(
    current_price: float,
    return_52w_low: float | None,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    historical_target = (
        current_price * (1 + return_52w_low)
        if return_52w_low is not None else None
    )
    peer_target = (
        percentile(peer_pe, cfg.peer_pe_bear_percentile) * ttm_eps
        if (ttm_eps and ttm_eps > 0 and peer_pe) else None
    )
    price = combine(historical_target, peer_target, mode=cfg.bear_conservatism, is_bear=True)
    return price if price is not None else current_price   # v0.13 — 양쪽 결측 시 fallback
```

#### Expected return + variance
```python
def compute_expected_return(
    opinion: ScenarioOpinion,
    ctx: ScenarioContext,
    cfg: ScenarioPricingConfig,
) -> ExpectedReturn:
    prices = {
        "bull": compute_bull_price(ctx.current_price, ctx.return_52w_high, ctx.ttm_eps, ctx.peer_pe, cfg),
        "base": compute_base_price(ctx.current_price, ctx.ttm_eps, ctx.peer_pe, cfg),
        "bear": compute_bear_price(ctx.current_price, ctx.return_52w_low, ctx.ttm_eps, ctx.peer_pe, cfg),
    }
    sc = {s.label: s for s in opinion.scenarios}

    # LLM 확률에 cap 적용 (cfg.bull_probability_cap / bear_probability_cap)
    probs = _apply_probability_cap(sc, cfg)

    # 가격 순서 검증 — 위반은 lineage 보존, 산식 자동 보정 없음
    flags = _validate_price_order(prices, opinion.symbol)

    expected = sum(probs[lbl] * prices[lbl] for lbl in ("bull", "base", "bear"))
    variance = sum(
        probs[lbl] * (prices[lbl] - expected) ** 2 for lbl in ("bull", "base", "bear")
    )

    return ExpectedReturn(
        symbol=opinion.symbol,
        as_of_date=opinion.as_of_date,
        expected_price=expected,
        expected_return=(expected - ctx.current_price) / ctx.current_price,
        variance=variance,
        scenario_prices=prices,
        pricing_config=cfg,
        data_quality_flags=flags,
        scenario_opinion_s3_key=ctx.scenario_s3_key,
        computed_at=datetime.now(timezone.utc),
    )


def _apply_probability_cap(sc, cfg) -> dict[str, float]:
    """v0.3 §4.1 결정 — bull/bear cap 적용 후 잉여 확률을
    *나머지 시나리오의 원래 비율* 로 비례 분배.

    예: bull=0.7, base=0.2, bear=0.1, bull_probability_cap=0.5
        → 잉여 0.2 를 base:bear = 0.2:0.1 (2:1) 비율로 분배
        → bull=0.5, base=0.333, bear=0.167 (합 = 1.0)

    bull 과 bear cap 둘 다 활성 시 bull 먼저, 이어서 bear (cfg 정의 순서).
    cap 후 합은 항상 1.0±부동소수점 오차.
    """
    p = {label: sc[label].probability for label in ("bull", "base", "bear")}
    for label, cap in (("bull", cfg.bull_probability_cap),
                       ("bear", cfg.bear_probability_cap)):
        if cap is None or p[label] <= cap:
            continue
        excess = p[label] - cap
        p[label] = cap
        others = [k for k in p if k != label]
        total_others = sum(p[k] for k in others)
        if total_others > 0:
            for k in others:
                p[k] += excess * (p[k] / total_others)
        else:
            # 나머지 모두 0 — 잉여를 균등 분배 (edge case)
            for k in others:
                p[k] += excess / len(others)
    return p


def _validate_price_order(prices, symbol) -> list[str]:
    """v0.3 §4.1 결정 — bear ≤ base ≤ bull 검증, 위반 시 warning + flag.
    expected_return 산식엔 영향 없음 (확률 가중 합은 그대로 산출).
    lineage 보존 우선 — 자동 swap·보정 없음.
    """
    bull, base, bear = prices["bull"], prices["base"], prices["bear"]
    if bear <= base <= bull:
        return []
    flag = (
        f"price_order_violation: bear={bear:.2f}, base={base:.2f}, "
        f"bull={bull:.2f}"
    )
    logger.warning(flag, extra={"symbol": symbol})
    return [flag]
```

**`_validate_price_order` 의 운영 의미** (CLAUDE.md "발생 안 하는 시나리오 validation 금지" 와의 정합성):
- 단순 assertion 이 아닌 *데이터 품질 신호 누적* 용도 — `base_price_cap_pct=None` 도입 시 또는 `aggressive` mode 사용 시 실제 발생 가능
- 위반 시에도 산식 진행 (CLAUDE.md 원칙 "내부 함수 신뢰") — 4단계 최적화가 *flag 기준 종목 weight 조정* 정책 도입 가능 (M3 후반)
- 4주 운영 후 위반 빈도가 0 이면 §12 미해결 항목에 *검증 제거 검토* 추가

### 4.2 ScenarioPricingConfig — 보수성 파라미터 (9 필드 / 4 그룹)

```python
class ScenarioPricingConfig(BaseModel):
    """가격 산정 공식의 보수성 파라미터. 모든 ExpectedReturn 산출에 사용된
    config 를 함께 저장 — 사후 sensitivity 분석·회귀 가능."""

    # 1. Bull/Bear 가격 산정의 historical vs peer 결합 방식
    bull_aggressiveness: Literal["conservative", "balanced", "aggressive"] = "conservative"
    bear_conservatism: Literal["conservative", "balanced", "aggressive"] = "conservative"

    # 2. Peer P/E percentile 폭 (순서: bear ≤ base ≤ bull — validator 강제)
    peer_pe_bull_percentile: float = Field(default=75.0, ge=50.0, le=95.0)
    peer_pe_base_percentile: float = Field(default=50.0, ge=40.0, le=60.0)
    peer_pe_bear_percentile: float = Field(default=25.0, ge=5.0, le=50.0)

    # 3. Base price cap (현재가 대비 fair value 상한)
    base_price_cap_pct: float | None = Field(default=0.0, ge=-0.5, le=1.0)
    # 0.0:  base ≤ 현재가 (보수)
    # None: cap 없음
    # bound: -0.5 (현재가 50% 하한) ~ 1.0 (200% 상한) — v0.6 무경계 latent bug 제거

    # 3b. Bear price cap (현재가 대비 bear 시나리오 가격 상한) — v0.16 신설, v0.17 승격
    bear_price_cap_pct: float | None = Field(default=0.0, ge=-0.5, le=1.0)
    # 딥밸류 종목에서 peer 함의 적정가 ≫ 현재가일 때 bear conservative=max 가
    # bear 가격을 현재가 위로 올려 bear > bull 역전 발생 (retro §0.5 07-14~08-03)
    # 0.0:  bear ≤ 현재가 ("bear 는 최소한 상승이 아니다") — v0.17 기본 (§12.3 결정)
    # None: cap 없음 (구 동작 — §4.4 "bear_uncapped" counterfactual 대안)

    # 4. LLM 확률 가중치 자체 보정 (마지막 가드 — bull/bear 대칭)
    bull_probability_cap: float | None = Field(default=None, ge=0.0, le=1.0)
    bear_probability_cap: float | None = Field(default=None, ge=0.0, le=1.0)
    # 둘 다 기본 None — LLM 출력 그대로 사용
    # cap 활성 시: 잉여 확률을 *나머지 시나리오의 원래 비율* 로 비례 분배 (§4.1 _apply_probability_cap)
    # 적용 순서: bull → bear. bull/bear 둘 다 cap 활성도 정합 (분배 후 ≤ cap 보장)
    # 비고: cap 은 §1.4.2 #1 (확률 calibration) 측정 전 *임시 가드*. M3 말 회고 시 cap 필요성 재평가

    @model_validator(mode="after")
    def _validate_percentile_order(self) -> Self:
        """v0.6 — config 레벨 가격 역전 사전 차단.
        Field bound 가 겹쳐 (bear le=50, base ge=40) 순서 역전 가능 →
        bear peer target > base peer target 같은 §4.1 가격 순서 위반의 근본 원인."""
        if not (self.peer_pe_bear_percentile
                <= self.peer_pe_base_percentile
                <= self.peer_pe_bull_percentile):
            raise ValueError(
                f"percentile 순서 위반: bear={self.peer_pe_bear_percentile}, "
                f"base={self.peer_pe_base_percentile}, "
                f"bull={self.peer_pe_bull_percentile} — bear ≤ base ≤ bull 필요"
            )
        return self
```

**기본값은 보수적 셋팅** — CHARTER §6 할루시네이션 리스크 보수.

> **v0.6 충분성 결산**: 파라미터는 *충분하되 1개 과잉 (제거) + 검증 2건 보강*. 누락 후보 (확률 floor / historical haircut / variance floor / missing-data policy) 는 모두 거부 또는 타 단계 이관 — §12 참조. `historical_window_days` 제거로 9→8 필드.

### 4.3 Config 변경 채널

| 방법 | 용도 | 변경 빈도 |
|---|---|---|
| `ScenarioPricingConfig` 기본값 (코드) | M3 운영의 *공식 정책* — git 커밋 필요 | 분기별 회고 후 |
| Lambda 환경변수 (`SCENARIO_BULL_AGGRESSIVENESS` 등) | 운영 시 즉시 override (예: 위기 상황 보수화) | 비상 시 |
| Step Functions 입력 JSON (`pricing_config_override`) | dry-run / 백테스트용 일회성 변경 | 회고 분석 시 |

Lambda 핸들러가 환경변수·입력 JSON → `ScenarioPricingConfig.model_validate` 로 파싱. 기본값 + override 병합.

### 4.4 ExpectedReturnsBundle — Sensitivity 옵션 (M3 5주차 이후 권장)

매주 *기본 config + 대안 1~2개*로 동시 계산해 함께 저장:

```python
class ExpectedReturnsBundle(BaseModel):
    primary: ExpectedReturn                          # 기본 config — 4단계 최적화 입력
    alternatives: dict[str, ExpectedReturn]          # 비교용 (LLM 호출 없음, 비용 0)
    # 키 예시: "balanced", "base_cap_10", "aggressive",
    #          "bear_uncapped" (v0.17 — cap 승격 후 counterfactual), "option_b_baseline"
```

저장: `s3://{bucket}/expected_returns/dt={...}/symbol={SYM}.json`

→ **추가 LLM 호출 없음**. 같은 ScenarioOpinion + 다른 config 만 적용. 비용 0.
→ 회고 시 "보수적 config 가 expected $X 예측 → 실제 $Y. 공격적 config 였다면 $Z" 분석 가능.

**옵션 B baseline 슬롯** (§1.4.2 #3 측정 인프라):
- `alternatives["option_b_baseline"]` 에 *LLM 호출 없는 코드 점수화* 결과를 동일 시점에 저장
- 산식: Bull/Bear opinion 의 `arguments[].confidence` 가중합 → `(bull_score - bear_score) / total` 을 *경험적 확률 proxy* 로 사용
- 같은 가격 산정 공식 (§4.1) 에 다른 확률 가중치 입력 → 옵션 B baseline 의 ExpectedReturn 산출
- M3 말 회고 시 옵션 C primary vs option_b_baseline 의 12주 portfolio outcome 비교 (§1.4.2 #3 합격 기준)

---

## 5. 비용

### 5.1 토큰·비용 추정 (단일 호출)

| 항목 | 추정 |
|---|---|
| 시스템 프롬프트 | ~700 tok (Bull/Bear 보다 schema 섹션 약간 큼) |
| ScenarioContext (Bull/Bear 두 opinion + 가격 컨텍스트) | ~2,500 tok |
| 사용자 프롬프트 지시문 | ~150 tok |
| 입력 합계 | **~3,350 tok** |
| 출력 (JSON: 3 scenarios + triggers) | ~500 tok |

Sonnet 4.6 단가 (2026-04 기준): 입력 $3/1M, 출력 $15/1M
- 호출당 비용: 3,350 × $3/1M + 500 × $15/1M ≈ **$0.018**

> **⚠ 추정 미검증 (golden #8 전) — M2 실측 교차검증** (v0.7):
> M2 Bull/Bear 는 입력을 **2x 과대추정** (추정 ~3,250 → 실측 1,551, [02 §5.2.1](02-bull-bear.md#521-max_tokens-상한)). 시나리오 `ScenarioContext ~2,500` 도 동일 추정 스타일 → 구조 분석상 실측 **~2,400~2,700** 예상 (2 opinion whitelist 직렬화 ~1,200~1,600 + price ~200 + system ~700 + 지시 ~150).
> per-call $0.018 이 M2 실측 ($0.0181) 과 일치하나 *구성 반대* (시나리오: 입력↑출력↓ / M2: 입력↓출력↑). **추정이 보수적(상향)이라 예산엔 안전** — golden(#8) 에서 실측 확정.

### 5.2 월 비용 추정

| 시나리오 | 종목/주 | 호출/주 | 호출/월 | 월 비용 |
|---|---|---|---|---|
| 기본 (15 종목) | 15 | 15 | 60 | ~$1.10 |
| 상한 (20 종목) | 20 | 20 | 80 | ~$1.45 |
| 재시도 +20% 가정 | 20 | 24 | 96 | ~$1.75 |

**합계 (2단계 Bull/Bear + 3단계 시나리오)**:
- Bull/Bear $2.76 + 시나리오 $1.45 = **월 ~$4.20** (hard cap $200 의 2.1%)

> **재시도 +20% 는 보수 ceiling** (v0.7) — M2 운영 4주 160 invoke 중 **retry/fallback 0건** ([02 §9](02-bull-bear.md)). golden 1회 retry 도 max_tokens 1024 잘림이 원인 → 2048 상향 후 0. 시나리오는 2048 + 출력 ~500 이라 잘림 위험 없음. 검증이 더 엄격(3 label·prob합·enum)하나 Sonnet 4.6 JSON 신뢰성 입증됨 → **실측: M3 4주 운영 재시도 0%** (v0.15, narrative 300→500 완화 후. 첫 수동 실행의 narrative 초과 재시도만 제외하면 정규 운영 0%).
>
> **S3 result cache 의 비용 의미** (v0.7) — cache (§6.2 lambda_core hit/miss) 는 *재실행(디버깅·Lambda retry·Step Functions 재실행) 폭주 방어* 용이지 steady-state 절감 아님. 주간 정규 배치는 매주 새 데이터라 cache-miss 가 정상 → 위 표의 **80 calls 풀 카운트가 정확**. (M2 dry-run 의 87.5% cache hit 은 같은 종목 재호출 테스트 효과)

CHARTER §3.3 "월 $30~$80" 추정 대비 매우 여유.

> **v2 헤드룸 정량화** (v0.7) — "여력 충분" 의 구체 수치:
> - **Self-Consistency** (5 샘플 majority vote): 시나리오 5× = ~$7.25/월 → 2단계 포함 ~$10/월 = **cap $200 의 ~5%**
> - **Debate** (bull↔bear 2 라운드 반박): 시나리오 2~3× = ~$3~4.4/월
> - 둘 다 동시 실험해도 hard cap 의 10% 미만 — 학습(CHARTER 1순위) 여력 충분

### 5.3 max_tokens

`AgentConfig.max_tokens = 2048` (Bull/Bear 와 동일). 출력 추정 ~500 tok 대비 충분 여유.

---

## 6. 오케스트레이션

### 6.1 호출 패턴 — Step Functions 확장

기존 `infra/step_functions/screening_workflow.asl.json` 에 `ScenarioMap` state 추가:

```
EventBridge (Mon 06:00 ET)
        │
        ▼
[Step Functions]
  ├─ RunScreening                            ← M1
  ├─ BullBearMap (MaxConcurrency=1)          ← M2  *Next:ScenarioMap + ResultPath 교정 (G1)*
  │     for each ScreenedStock:
  │       Parallel: Bull / Bear → S3
  ├─ ScenarioMap (MaxConcurrency=2)          ← M3 추가
  │     for each ScreenedStock:
  │       ScenarioAgent Lambda  [Task: Catch→RecordItemFailure + Lambda throttle Retry]
  │         → fetch BullBearOpinion × 2 from S3
  │         → fetch price context (OHLCV, key-metrics-ttm, peer_pe)
  │         → LLM call → ScenarioOpinion
  │         → compute_expected_return → ExpectedReturn
  │         → S3 write (scenarios/, expected_returns/)
  └─ (다음 state: 4단계 최적화 — M3 후반)
```

**MaxConcurrency=2** (G3) — Anthropic 8K tok/min 한도 산정 (02-bull-bear §5.2.2 공식). 시나리오는 **single-stance** (Bull/Bear 와 달리 stance 분리 없음):
- ScenarioMap 동시 = 2 종목 × max_tokens 2048 = **4,096 tok 예약** ≤ 8,000 ✓
- 이는 M2 `BullBearMap`(=1 × 2 stance × 2048 = 4,096) 과 **동일 부하** — M2 4주 안정 운영으로 검증된 수준. retry burst 여유 3,904 tok
- 공식상 floor(8000/2048)=3 까지 가능하나 retry burst 여유(=3 시 1,856)·M2 429 교훈으로 =2 채택. 한도 상향 시 `floor(N / 2048)`

**G1 — BullBearMap 체이닝 교정 (필수)**:
현재 커밋된 ASL ([screening_workflow.asl.json](../infra/step_functions/screening_workflow.asl.json)) 의 `BullBearMap` 은 M2 종착(`End:true`)이고 `ResultPath` 가 없어 **Map 출력(per-item 결과 배열)이 `$` 를 덮어씀** → `$.result.selected` 소멸. ScenarioMap 이 같은 종목을 순회하려면:
```jsonc
// BullBearMap 수정
"End": true            →  "Next": "ScenarioMap",
                          "ResultPath": "$.bullbear_results"   // $.result 보존
```
→ 이후 ScenarioMap 이 `"ItemsPath": "$.result.selected"` 로 동일 selected 순회 가능.

**ScenarioMap state (교정 ASL — G2 Catch/Retry 는 Task 직착)**:
```jsonc
"ScenarioMap": {
  "Type": "Map",
  "ItemsPath": "$.result.selected",
  "MaxConcurrency": 2,
  "ItemSelector": {                          // agent_scenario 가 bull/bear S3 키 결정적 재구성 (G4)
    "screened_stock.$": "$$.Map.Item.Value",
    "as_of_date.$": "$.result.as_of_date",
    "run_id.$": "$.result.run_id"
  },
  "ItemProcessor": {
    "ProcessorConfig": { "Mode": "INLINE" },
    "StartAt": "ScenarioAgent",
    "States": {
      "ScenarioAgent": {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": { "FunctionName": "<<SCENARIO_LAMBDA>>", "Payload.$": "$" },
        "ResultSelector": { "result.$": "$.Payload" },
        "Retry": [{                          // M2 와 동일 Lambda throttle retry (LLM 사다리와 별개)
          "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException",
                          "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
          "IntervalSeconds": 5, "MaxAttempts": 3, "BackoffRate": 2.0
        }],
        "Catch": [{                          // Parallel 없음 → Task 직착 (BullBear 와 다른 점)
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "RecordScenarioFailure"
        }],
        "End": true
      },
      "RecordScenarioFailure": {
        "Type": "Pass",
        "Parameters": { "status": "failed", "symbol.$": "$.screened_stock.symbol", "error.$": "$.error" },
        "End": true
      }
    }
  },
  "End": true                                // 4단계 추가 시 Next 로 교체 (G5: 4단계는 S3 prefix 로드, state 스레딩 불필요)
}
```

### 6.2 구성 요소 매핑
- **Step Functions**: 기존 워크플로우에 `ScenarioMap` state 1개 추가
- **Lambda**: `agent_scenario` 1개 (Bull/Bear 와 달리 stance 분리 없음 — 단일 함수)
- **S3 레이아웃**:
  - `s3://{bucket}/scenarios/dt={yyyy-mm-dd}/symbol={SYM}.json` — `ScenarioOpinion` (LLM 출력)
  - `s3://{bucket}/expected_returns/dt={yyyy-mm-dd}/symbol={SYM}.json` — `ExpectedReturn` (산식 결과)
  - `s3://{bucket}/expected_returns/dt={...}/symbol={SYM}/context.json` — `ScenarioContext` 원본 (재현용)

**결정성 캐시 정책** (v0.13 — M2 §10 `context_input_hash` 패턴 재사용):
- 캐시 키 = `scenario_input_hash = hash(ScenarioContext − lineage 필드)` — `run_id`/`scenario_s3_key`/`bullbear_s3_keys`/`data_quality_flags` 제외 (Bull/Bear opinion 본문 + 가격 컨텍스트만 해시)
- `lambda_core` 가 hit/miss 분기: 동일 hash 의 저장본 존재 시 **LLM 호출 0회 / `cost_usd=0`** 으로 저장본 반환 — 의도치 않은 재실행(Lambda retry, Step Functions 재실행, 디버깅 invoke) 비용 폭주 0 보장 (CHARTER 2순위 재현성, §5.2 cache 주장의 메커니즘)
- 분기 발표로 Bull/Bear opinion 갱신 시 → hash 변경 → 자연히 cache-miss (신선도 보장). 주간 정규 배치는 새 데이터라 항상 miss = §5.2 의 80 calls 풀 카운트와 정합
- **가격 산식(`compute_expected_return`)은 순수 함수** — 캐시 불필요 (같은 ScenarioOpinion + config → 항상 같은 ExpectedReturn). LLM 출력(ScenarioOpinion)만 캐시 대상

### 6.3 IaC
- 기존 `deploy_lambda.sh` 가 `src/agents/scenario/` 자동 포함 (deploy_lambda v2 의 `agents/` 패키징 그대로)
- `screening_workflow.asl.json` 변경:
  - **`BullBearMap` 수정 (G1)** — `End:true` → `Next:"ScenarioMap"` + `ResultPath:"$.bullbear_results"` (`$.result` 보존). *기존 state 수정* 이라 M2 회귀 주의 — ASL 변경 후 5종목 dry-run 재검증 (#10)
  - **`ScenarioMap` state 추가** + placeholder `<<SCENARIO_LAMBDA>>`
- `deploy_step_functions.sh` 에 placeholder 1개 (`SCENARIO_LAMBDA`) 추가

---

## 7. 트리거 자동 검증 (옵션 C 의 강점 — Self-Verification)

### 7.1 검증 메커니즘

분기 발표 후 (FMP statement 캐시 갱신 시점) 자동 실행. 별도 Lambda 또는 batch 스크립트.

**평가 윈도우 — tripwire (v0.5 P1-G 확정)**:
- 모든 `invalidation_trigger` 는 *다음 분기 발표 시점에 1회* 평가된다 (tripwire = 조기 경보).
- YoY metric 은 *동기 전년 분기* 대비, QoQ 는 *직전 분기* 대비.
- 시나리오 의미: "다음 분기에 *이미 깨졌나*" — 장기 thesis 도 다음 분기에 이미 무효 신호가 보이면 invalidation 으로 간주.
- `evaluation_window` 같은 가변 호라이즌 필드는 **미도입** — 다중 윈도우는 §12 v2 후보.

**출력 스키마 `TriggerEvaluation`** (v0.10 — trigger_evaluator.py):
```python
class TriggerEvaluation(BaseModel):
    symbol: str
    scenario_label: Literal["bull", "base", "bear"]
    metric: str                          # 평가된 InvalidationTrigger.metric
    actual: float | None = None          # 자동 측정값 (qualitative 면 None)
    threshold: float | None = None       # 원본 트리거 threshold
    met: bool | None = None              # True=무효화 발동 / False=미발동 / None=인간 검토 필요
    requires_human_review: bool = False  # peer_announcement·guidance_change·sector 가드
    evaluated_quarter: str               # 평가 기준 분기 (예: "2026-Q2") — 시간 조인 추적 (B)
    scenario_s3_key: str                 # 원본 ScenarioOpinion lineage
    evaluated_at: datetime
```
저장: `s3://{bucket}/trigger_evaluations/dt={발표후-평가일}/symbol={SYM}.json`. 종목당 3개(bull/base/bear) — §7.2 calibration·적중률 누적의 원천.

```python
def evaluate_trigger(
    trigger: InvalidationTrigger,
    symbol: str,
    income_quarterly: list[dict],         # FMP income-statement-quarterly
    cashflow_quarterly: list[dict],       # FMP cash-flow-statement-quarterly
    balance_quarterly: list[dict],        # v0.4 net_debt_yoy 용 (BS)
    earnings_surprises: list[dict],       # v0.4 earnings_surprise 용
    sub_sector: str | None = None,        # v0.4 net_debt_yoy sector 가드
) -> TriggerEvaluation:
    if trigger.metric == "revenue_yoy":
        latest = income_quarterly[0]["revenue"]
        prior_year = income_quarterly[4]["revenue"]   # 4분기 전 (tripwire: 다음 발표 시점)
        if prior_year and prior_year > 0:
            actual = (latest / prior_year - 1) * 100
            met = (
                (trigger.direction == "less_than" and actual < trigger.threshold) or
                (trigger.direction == "greater_than" and actual > trigger.threshold)
            )
            return TriggerEvaluation(actual=actual, threshold=trigger.threshold, met=met)

    elif trigger.metric == "net_debt_yoy":
        # v0.4 — Financials/Utilities 는 시스템 프롬프트가 자제시켰으나 위반 케이스 감지
        if sub_sector in {"Financials", "Utilities"}:
            return TriggerEvaluation(met=None, requires_human_review=True)
        # totalDebt - cashAndShortTermInvestments 의 전년 동기 대비
        # ... (BS 산식)

    elif trigger.metric in {"peer_announcement", "guidance_change"}:
        return TriggerEvaluation(met=None, requires_human_review=True)

    # ... 다른 metric (earnings_surprise 는 earnings_surprises 응답 사용)
```

> **v0.4 인터페이스 메모**: `earnings_surprise` / `net_debt_yoy` 추가로 `evaluate_trigger` 가 `balance_quarterly` / `earnings_surprises` / `sub_sector` 입력을 추가로 받는다. 구현(#7 티켓) 시 시그니처 확정.

### 7.2 회고 데이터 누적

매주·매월 자동 누적:
- 시나리오별 트리거 적중률 (분기 발표 후 측정)
- 확률 calibration (LLM 이 bull 60% 라고 한 시나리오가 실제로 60% 빈도로 발생했나)
- 가격 산정 정확도 (expected_price vs 실제 가격)

이 데이터로 §12 미해결 항목 *기본 config 조정* 의 근거 마련.

**realized 시나리오 정의 — 가격 bin** (v0.10 C — §1.4.2 #1 Brier 측정 근거):
`1_{realized}` (bull/base/bear 중 무엇이 실현됐나) 를 *scenario_prices 중점 bin* 으로 결정:
```python
def realized_scenario(actual_price: float, prices: dict) -> Literal["bull", "base", "bear"]:
    bull, base, bear = prices["bull"], prices["base"], prices["bear"]
    if actual_price >= (bull + base) / 2: return "bull"
    if actual_price <= (base + bear) / 2: return "bear"
    return "base"
```
- **앵커**: 다음 분기 발표일 종가 (트리거 평가와 동일 시점 — 단일 시간축)
- **Brier**: `BS = Σ_s (probability_s - 1_{s == realized})²`. `ExpectedReturn.scenario_prices` (저장본) 재사용 → 추가 데이터 없음
- 가격 순서 위반 종목 (`data_quality_flags`, v0.3) 은 bin 경계 신뢰 어려움 → calibration 표본에서 제외

> **observe-only 경계** (v0.10 E — CHARTER §6 정합): 트리거 평가·calibration 은 **회고 전용**. 트리거 `met=True` (시나리오 무효화 감지) 가 M3 에서 *매매·종목 재분석·다음 주 리밸런싱에 자동 피드백되지 않음*. 매매는 5단계 룰 기반 (CHARTER §6 "LLM 은 근거 생성만"), 트리거는 *사후 학습 신호*. 라이브 피드백(예: met 시 즉시 재스크리닝)은 v2 후보 — §12.

### 7.3 자동 측정 vs 인간 검토

| metric | 자동 측정 | 회고 시 처리 |
|---|---|---|
| revenue / eps / fcf / margin yoy / qoq | ✅ | 분기 발표 후 자동 평가 → met/not_met 기록 |
| `earnings_surprise` *(v0.4)* | ✅ T+0 | 분기 발표 직후 FMP `earnings-surprises` 캐시 갱신 시 즉시 평가 — 회고 데이터 누적 가장 빠름 |
| `net_debt_yoy` *(v0.4)* | ✅ | 분기 BS 캐시 갱신 시 평가. Financials/Utilities sub_sector 종목은 `requires_human_review=True` 로 분기 (시스템 프롬프트 위반 케이스 감지) |
| `guidance_change` | ⚠ semi-auto | M3 후반 텍스트 분석 모듈 또는 인간 검토 |
| `peer_announcement` | ❌ | 분기 회고 시 인간 검토. 가능하면 다른 metric 으로 표현 권장 (system prompt 명시) |

---

## 8. 로깅과 관측

CLAUDE.md "모든 LLM 호출은 다음을 로깅" 규칙 준수.

### 8.1 호출 로그 (CloudWatch + S3)
```json
{
  "timestamp": "2026-06-01T11:00:00Z",
  "model": "claude-sonnet-4-6",
  "purpose": "scenario",
  "symbol": "AAPL",
  "input_tokens": 3380,
  "output_tokens": 510,
  "cost_usd": 0.0178,
  "latency_ms": 4900,
  "retry_count": 0,
  "schema_valid": true,
  "scenarios_probabilities": [0.40, 0.45, 0.15],   // bull/base/bear
  "expected_return": 0.082                          // 산식 결과 (8.2%)
}
```

### 8.2 결과 보존
- `scenarios/dt=*/symbol=*.json` — LLM `ScenarioOpinion` 원본
- `expected_returns/dt=*/symbol=*.json` — 산식 `ExpectedReturn` + 사용된 config
- `scenario_contexts/dt=*/symbol=*.json` — 입력 `ScenarioContext` 원본 (재현용)

### 8.3 주간 리포트 (자동)
- 시나리오 확률 분포 (bull 평균·표준편차, base/bear 동일)
- expected_return 분포 (종목별·sector별)
- 트리거 metric 사용 빈도 (revenue_yoy 가 60%, peer_announcement 가 5% 같은 패턴)

---

## 9. 실패·예외 처리

| 케이스 | 처리 |
|---|---|
| Anthropic API 5xx/타임아웃 | Bull/Bear 와 동일 사다리 (Sonnet → primary retry → Haiku) |
| Pydantic 검증 실패 (확률 합 ≠ 1.0, 라벨 누락 등) | 사다리 진행, 모두 실패 시 `ScenarioAgentError` raise |
| Rate limit | MaxConcurrency=2 로 사전 방어 (§6.1 G3) + 429 retry |
| `ttm_eps=None` (EPS 결측) | peer-implied 가격 산정 불가 → historical-only 로 fallback. 로그 warning |
| 빈 `peer_pe` (sub_sector singleton 등) | peer-implied 불가 → historical-only |
| `return_52w_high/low=None` (OHLCV 결측) | historical-only 불가 → peer-implied 로 fallback (v0.13) |
| **bull/bear 가격 양쪽 입력 결측** (52w + EPS/peer 동시 결측) | `compute_bull/bear_price` 가 `current_price` fallback (base 선례). `data_quality_flags` 기록 (v0.13) |
| `current_price=0` 또는 음수 | 명백한 데이터 오류 — `ScenarioContextError`, 종목 스킵 |
| Bull 또는 Bear `BullBearOpinion` 누락 (Bull/Bear 단계 실패) | 본 단계 스킵 + 로그. 4단계 최적화에 expected_return 없는 종목으로 전달 |

**격리 패턴 (G2 — M2 와 위치 다름)**: M2 의 `Catch` 는 `BullBearParallel`(Parallel state)에 부착되나, ScenarioMap 은 Parallel 없는 단일 Task → `Catch[States.ALL]` 을 **`ScenarioAgent` Task 에 직접** 부착 → `RecordScenarioFailure`(Pass). 한 종목 실패가 Map 전체를 막지 않음. Task 의 **Lambda throttle Retry** (ServiceException 등 4종, M2 동일) 는 *LLM 사다리 retry(Sonnet→Haiku, Lambda 내부)* 와 별개로 둘 다 적용. 교정 ASL 은 §6.1 참조.

---

## 10. 테스트 전략 (CLAUDE.md 준수)

### 10.1 단위 테스트
- `pricing.py`:
  - `combine` 모드 3 가지 × `is_bear` flag — bull 의 conservative=min vs bear 의 conservative=max 동작 분기 (v0.3 §4.1)
  - `percentile` (손수 linear interp, numpy 미사용 v0.9) — numpy 'linear' 대비 동등성, singleton/2개 리스트 경계, `ttm_eps=None`/`peer_pe=[]` fallback
  - `base_price_cap_pct=0.0` 가 base ≤ current 강제, `None` 일 때 cap 미적용
  - `_apply_probability_cap` — bull cap 단독 / bear cap 단독 / 둘 다 / 비례 분배 정확성 / 합 = 1.0 (v0.3 §4.1)
  - `_validate_price_order` — 정상 순서 (flag=[]), 위반 (flag 메시지 포맷) (v0.3 §4.1)
- `pricing_config.py`: 기본값, 환경변수 파싱, 입력 JSON override 병합, `bear_probability_cap` 범위 검증
  - `_validate_percentile_order` — bear ≤ base ≤ bull 정상 통과 / 역전 config (bear=50, base=40) → ValidationError (v0.6)
  - `base_price_cap_pct` bound — `-0.6`/`1.5` 등 범위 밖 → ValidationError (v0.6)
- `schemas.py`: ScenarioOpinion Pydantic — 확률 합, 라벨 unique, narrative 길이, trigger threshold null 조건, `ExpectedReturn.data_quality_flags` 기본값 = `[]`
- `trigger_evaluator.py`: 각 metric 별 자동 평가 (픽스처 분기 데이터 입력)

### 10.2 LLM 호출 모킹
- Bull/Bear `FakeAnthropicClient` 재사용. 정상/검증실패/5xx 시나리오

### 10.3 골든 케이스 (M2 패턴 — 생성/replay 분리)

CLAUDE.md `golden` 마커 = *실제 LLM 호출 없이* 저장 스냅샷 검증. 두 단계로 분리:

**(a) 스냅샷 생성 — 수동, 실제 호출** (`scripts/run_scenario_golden.py`):
- 4종목 (Bull/Bear 골든 그대로: AAPL/XOM/NVDA/JPM) → 시나리오 실제 호출 → 스냅샷 저장
- `tests/golden/scenario/{symbol}.json` — `ScenarioOpinion`(LLM 출력) + `ExpectedReturn`(산식)
- **1회 비용 ~$0.072** (4 × $0.018, single-stance) — *CI 반복 아님*. 프롬프트/스키마 변경 시에만 재생성 + 인간 검토 후 커밋
- P2-H (§11 #5) 의미 확정 후 생성 (스냅샷 일관성)

**(b) golden 테스트 — replay, LLM 호출 0** (`pytest -m golden`, 마커는 이미 `src/pytest.ini` 등록 — 재사용):
- 저장 스냅샷 JSON 만 검증. 스냅샷 없으면 parametrize 0건 → false-positive pass 없음 (M2 패턴)
- 검증 항목:
  1. `ScenarioOpinion` 스키마 통과 + prob 합 1.0 + 라벨 = bull/base/bear + trigger metric ∈ enum
  2. **narrative 에 가격 숫자 미출현** (hard rule #1 "No price estimation" 회귀 — `$\d+`/price target 정규식)
  3. **`ExpectedReturn` 결정성 exact** — 저장 `ScenarioOpinion` + `ScenarioContext` → `compute_expected_return` 재실행 → 저장 `ExpectedReturn` 과 정확 일치 (M2 엔 없던 강한 회귀 — 순수 산식)
  4. 사다리 최소 1회 succeeded (메타 회귀)

### 10.4 통합 테스트
- `lambda_core.py`: moto S3 모킹 + FakeAnthropicClient + 캐시 hit/miss 분기 (Bull/Bear lambda_core 패턴)
- 12~15 케이스 예상

### 10.5 응답 품질 (DeepEval — M2 §11.5 baseline 패턴 확장)
- 3 criteria 후보:
  - `scenarios_probabilities_calibrated`: 확률이 evidence 와 정합 (예: Bull 의견이 weak 한데 bull 70% 같은 불일치 차단)
  - `triggers_are_measurable`: invalidation_trigger 가 자동 측정 가능 metric 선호
  - `narratives_cite_bullbear`: narrative 가 Bull/Bear evidence 인용
- M3 5주차 이후 운영 데이터로 baseline 확립

---

## 11. 구현 순서 (M3 마일스톤)

> M2 종료 후 진입. 각 단계 별도 커밋. LLM 호출 추가 커밋은 비용 추정 명시 (CLAUDE.md).

1. **schemas.py** — `ScenarioContext` / `ScenarioOpinion` / `InvalidationTrigger` / `ExpectedReturn` + 단위 테스트 (`ScenarioPricingConfig` 는 #2 pricing_config.py — v0.9)
   - **P1-E 결정** (§2.4): `metric ↔ threshold_unit` mapping validator 도입 여부. 권장 — `model_validator` 로 무효 조합 차단 (`revenue_yoy`+`qualitative` 등). 거부 시 시스템 프롬프트 명시만. 기존 필드 위 validator 라 무손실
2. **pricing_config.py** — `ScenarioPricingConfig` 모델 + 기본값 + 환경변수 파싱 + 입력 JSON override 병합 + validator (`_validate_percentile_order`, `base_price_cap_pct` bound — §4.2). *pricing.py(#3) 가 import 하므로 먼저* (v0.9 의존성 교정)
3. **pricing.py** — `compute_bull/base/bear_price` + `compute_expected_return` + `combine` + `percentile`(손수 linear interp, numpy 미사용 — v0.9) 순수 함수. config 3 mode × percentile 테스트
4. **context_builder.py** — Bull/Bear S3 로드 + 가격 컨텍스트 조립 + `to_prompt_markdown` 화이트리스트 직렬화
5. **프롬프트 파일 2종** — `scenario_system.md` + `scenario_user.md` (placeholder 4개)
   - **P2-H 결정** (§2.4): `label ↔ trigger direction` 의미 — bull 시나리오의 `invalidation_trigger` 는 *시나리오를 부정하는 방향* 이어야 함. 시스템 프롬프트에 "이 트리거 충족 시 해당 시나리오 무효화" 의미 강화. golden(#8) 전 확정 필요 (스냅샷 일관성)
6. **agent.py 골격** — Bull/Bear `agent.py` 패턴 재사용. AnthropicCaller / 사다리 / Pydantic 검증 / 로깅
7. **trigger_evaluator.py** — metric 별 자동 평가 함수 + 단위 테스트 (`#1 InvalidationTrigger 에만 의존` — agent 경로·golden 과 독립, 활성화는 #13 batch)
   - tripwire 윈도우 (다음 분기 발표 1회) 전제로 구현 (§7.1 v0.5 확정)
   - v0.4 metric (`earnings_surprise`, `net_debt_yoy`) 입력 시그니처 확정 (§7.1 메모 — `balance_quarterly` / `earnings_surprises` / `sub_sector` 추가)
   - **P2-D 결정** (§2.4): `guidance_change` 자동화 방법 — (1) transcript 키워드 정규식 / (2) 추가 LLM 호출 요약 / (3) 인간 회고만. 시작은 `requires_human_review=True` fallback, M3 후반 자동화 검토
8. **golden 케이스 4건** — `run_scenario_golden.py` 가 AAPL/XOM/NVDA/JPM 실제 호출 → 스냅샷 생성(1회 $0.072), `pytest -m golden` 은 replay 검증 (LLM 0, `golden` 마커 기존재 — 재사용). P2-H (#5) 의미 확정 후 진행 (스냅샷 일관성). 상세 §10.3
9. **Lambda 핸들러** — `src/lambdas/agent_scenario/handler.py` + `lambda_core.py` 공유 코어
10. **Step Functions ASL 확장** (§6.1 교정 ASL) — (a) `BullBearMap` 교정: `End`→`Next:ScenarioMap` + `ResultPath:"$.bullbear_results"` (G1 — 안 하면 `$.result.selected` 소멸로 ScenarioMap 실패), (b) `ScenarioMap` state 추가 + Task 직착 `Catch`/Retry (G2). **M2 회귀 주의** (기존 BullBearMap 수정) → 5종목 dry-run 재검증
11. ✅ **20종목 주간 배치 첫 실행** (2026-05-31 수동 → 06-01 부터 주간 자동) — 비용·실패율은 retro §0.5 운영 로그에 주차별 누적, 4주 회고(§0.6) 완료
12. ✅ **ExpectedReturnsBundle sensitivity 로깅** (v0.15 구현) — `alternative_configs`(balanced/base_cap_10/aggressive) 병렬 산출, lambda_core 가 Bundle 저장 (추가 LLM 비용 0). option_b_baseline 슬롯(§1.4.2 #3)은 4단계 시 추가. v0.17 에서 "bear_uncapped" 추가
13. ✅(로컬 PoC) **트리거 자동 검증** — `scripts/run_trigger_batch.py` (2026-08-08, retro §0.5). 9주 163쌍 채점 → S3 `trigger_evaluations/`. **잔여**: Lambda 자동화 여부 (§12.2 D), earnings_surprise 클라이언트 확장, fcf_yoy threshold 프롬프트 가이드 검토
14. **(M3 후반)** DeepEval baseline (§10.5) — 3 criteria 운영 데이터로 baseline 확립 + 회귀 게이트 (M2 §11.5 패턴). judge 호출 비용 발생 (operational §5 와 별도)

---

## 12. 미해결 / 다음 결정 필요

> **v0.12 재구조화** — v0.3~v0.11 누적 22개 flat 리스트를 *결정 시점·의존성* 기준 4그룹으로 재배치 + 중복 병합. M3 착수 시 "진짜 열린 결정" 가시성 확보.

### 12.1 ✅ 이미 해결 (다른 섹션에서 결정 — 추적용)

- [x] **P1-G 트리거 평가 윈도우** (v0.5 §7.1) — *tripwire* (다음 분기 발표 1회 고정). 다중 윈도우는 §12.4 로 이월
- [x] **확률 calibration 평가 *방법*** (v0.10 §7.2) — `realized_scenario` 가격 bin + Brier score 로 정의 완료. *측정* (12주 누적) 은 §12.3 의 "옵션 C 성공 기준 (1)" 로 통합 (이전 별도 항목 중복 제거)
- [x] **variance floor 책임 경계** (v0.6 §부록 B) — *4단계* covariance 대각 구성 시 floor (3단계 config 아님). 경계 확정 — 4단계 설계(`docs/04-optimizer.md`)에서 구현

### 12.2 🟢 즉시 결정 가능 (운영 데이터 불요 — 구현 티켓에서 확정)

- [x] **P1-E `metric ↔ threshold_unit` validator** (§2.4 — #1 schemas.py) — **구현 완료** (`InvalidationTrigger._validate_metric_unit` — qualitative metric ⟺ qualitative unit ⟺ threshold None 강제)
- [x] **P2-H `label ↔ trigger direction` 의미** (§2.4 — #5 prompts) — **구현 완료** (`scenario_system.md` hard rule #4 "Triggers must negate their own scenario" — bull 트리거는 하방, bear 트리거는 상방 이벤트 명시)
- [~] **D 트리거 배치 메커니즘** (§7 — #13) — **로컬 배치 선행으로 결정** (2026-08-08, `scripts/run_trigger_batch.py` — 매 실행 시 미발표분 이어 채점하는 증분 구조라 발표 분산 문제 자연 해소). **잔여**: 수동 → 자동화 전환 시점·형태 (독립 Lambda 권장 — 전방 파이프라인 실패와 격리, 07-06 2주 결번 교훈). 몇 주 수동 운영 후 결정
- [ ] **`guidance_change` 자동화 *방법*** (P2-D, §2.4 — #7) — (1) transcript 정규식 / (2) LLM 요약 / (3) 인간만. *시작은 `requires_human_review` fallback* 확정, 자동화 방법은 M3 후반 (§12.3 데이터 참고)

### 12.3 🔴 데이터·운영 게이트 (M3 운영 데이터 필요 — 지금 결정 불가)

- [~] **`ScenarioPricingConfig` 기본값 조정** — 4주 회고(v0.15): 음수 skew 82.5% 확인(원인 base_cap=0.0). 즉시 변경 대신 **#12 sensitivity 활성화**(balanced/base_cap_10/aggressive A/B 누적) → **4단계 설계 시** optimizer 거동 보고 config 확정. `_validate_price_order` 는 완화 대비 안전망으로 유지
- [x] **`peer_announcement` 트리거 정책** — **유지 확정** (2026-08-10): #13 배치 9주 489 트리거 중 사용 **0건** (정성 metric 은 guidance_change 14건뿐) — 남용 우려 기각, enum 유지 무해. 참고: 사용 분포 fcf_yoy 206(42%)/revenue_yoy 84/revenue_qoq 80/earnings_surprise 57/eps_yoy 29/op_margin 19 — **fcf_yoy 편중 + 좁은 threshold 로 발동 남발**이 실제 이슈 (프롬프트 가이드 후보, DeepEval 게이트 대상)
- [ ] **`_validate_price_order` 위반 빈도** (v0.3 §4.1) — 4주 후 위반 0 이면 검증 제거 검토. `aggressive`/`base_price_cap_pct=None` 도입 시 재측정. **v0.16 갱신**: epsDiluted 수정 후 위반 7/20 × 2주 연속 발생 (retro §0.5) — 제거 논의 폐기, 안전망 확정
- [x] **bear 가격 semantics — `bear_capped` primary 승격** (v0.16 신설 → **v0.17 승격 완료, 2026-08-04**) — bear>bull 역전이 양수 ER 상위를 점유 (07-20: 6/7) → optimizer·§7.2 calibration 오염. 증거 4주 일관 (시뮬 07-20 역전 7→1 / 실운영 07-27 7→1 / 08-03 6→0, 영향은 역전 종목에 국한) → 기본값 0.0 승격, "bear_uncapped" 를 counterfactual 대안으로 유지. **잔여 관찰 항목**: (a) base<bear 유형 (ADM/TKO — peer base ≪ historical bear, 주당 ≤1건) 빈도 추적, (b) ALL 형 퇴화 (bear=base=bull=current → ER=0·variance=0) 발생 시 optimizer 처리, (c) 근본 재설계(bear historical 단독 등)는 §12.4 v2 후보 유지, (d) **극소 양수 TTM EPS 유형** (08-10 DD — 일회성 손실로 TTM EPS 0.45·P/E ~317 → peer 목표가 붕괴, ER -88.5%. `ttm_eps>0` 가드 사각지대. 가드 후보: `current/ttm_eps` 극단(예: P/E>100) 시 peer 갈래를 결측 취급 — 빈도 보고 결정)
- [ ] **TTM EPS 결측 종목 정책** — historical-only fallback 은 §9 확정, *정확도 영향* 은 첫 운영 결측 분포 후. **관찰 데이터** (v0.17 시점): 유효 regime 4주간 음수 TTM(=peer 갈래 의도적 차단) 4~6/20 매주 — 딥밸류 유니버스 특성상 상시 존재. 극소 *양수* TTM 유형(DD, P/E ~317)은 §12.3 bear semantics 잔여 (d) 참조
- [ ] **확률 cap 재평가** (v0.3 §4.2) — `bull/bear_probability_cap` 은 calibration 측정 전 임시 가드. M3 말 Brier 결과로 유지/제거. bull bias 발견 시 활성화
- [ ] **확률 floor (`min_scenario_probability`)** (v0.6 §4.2 거부) — tail 과소평가 왜곡 우려나 옵션 C 는 LLM 신뢰 기본. calibration 이 tail 계통 과소평가 보일 때만 도입. M3 말
- [x] **B 트리거 시간 조인** (v0.10 §7 — #13) — **v1 규칙 확정·실증** (2026-08-08): `filingDate > as_of_date` 인 최초 발표 매칭 + `rows_up_to` 로 채점 분기 고정 (`income[0]` 가정 문제 해소 — 늦은 평가 시 후속 발표 혼입 방지). 발표 분산 6/25~8/7 실데이터로 검증
- [x] **F 트리거 배치 FMP 신선도** (v0.10 §7 — #13, M2 §12) — **v1 해결** (2026-08-08): batch 가 `--max-cache-age-days`(기본 3) 로 90일 TTL 을 우회해 캐시 계층 경유 재수집 — 발표 직후 신선 statement 확보 + 캐시 갱신 (주간 파이프라인도 새 분기 데이터 승계). M2 "이벤트 기반 캐시 무효화" 일반화는 M2 §10 잔존 항목으로 유지
- [ ] **시나리오 sensitivity 의 portfolio 영향** — 같은 LLM 출력 다른 config → 4단계 결과 차이. ExpectedReturnsBundle 로 측정
- [ ] **§2.4 enum 거부 후보 5개 재검토** (v0.4 — 4주 이후/v2):
  - `valuation_multiple`: peer_pe 입력·출력 자기 참조 (bull 실현=트리거 자동 충족). 의미 명확화 후 v2
  - `analyst_estimate_revision`: 후행 지표 + snapshot 시계열 캐시 필요 → M3 범위 외
  - `dividend_yield_change`/`buyback_yield`: income 종목 점유율 제한적. universe 분석 후
  - `sector_specific`: sub_sector×metric 매트릭스 (M2 §10 "sector-specific factor" 통합, v2)
  - `macroeconomic`: 종목별 schema 에 전 시장 metric 부적합 → 별도 macro_trigger/sector overlay (v2)
- [ ] **옵션 C 성공 기준 측정** (§1.4.2 — M3 말 12주 회고):
  - (1) calibration Brier < 0.25 — `trigger_evaluator`(#13) 자동 누적 (방법은 §12.1 확정)
  - (2) 트리거 적중률 ≥ 60% — 자동 측정 metric 한정
  - (3) Portfolio outcome vs 옵션 B baseline 동등/우월 — `ExpectedReturnsBundle.alternatives` (추가 비용 0)
- [ ] **§1.4.1 잠재 약점별 완화책 활성화 시점**:
  - 약점 1 (calibration unverified) — #13 트리거 자동 검증 항상 활성화
  - 약점 2 (M&A 등 비정량) — enum 확장 또는 `peer_announcement` 빈도 모니터링 (M3 5주차)
  - 약점 3 (가격 산식 보수성) — `aggressive` config 또는 Monte Carlo (§12.4)
  - 약점 4 (5단계 cross-validation) — M3 말 portfolio outcome (§1.4.2 #3)

### 12.4 🔵 v2 / 범위 외 연기 (데이터 아닌 스코프로 연기)

- [ ] **v2 Monte Carlo 시나리오** — 3 scenarios 단일 시점 대신 distribution. 옵션 C 4주 안정 후 (M3 후반 후보)
- [ ] **다중 평가 윈도우 (`evaluation_window`)** (v0.5 §7.1 P1-G 이월) — 장기 thesis 조기 false-negative 빈도 높으면 `Literal["next_quarter","next_year"]` Optional 필드. 무손실 마이그레이션. M3 말 tripwire 적중률(§1.4.2 #2)로 판단
- [ ] **`historical_window_days` 재추가** (v0.6 §4.2 제거) — multi-window 가격 산정 (126/378d 등) 구현 시. ScenarioContext 가 `return_Nw_high/low` 제공 필요. 위 다중 윈도우와 함께 v2

---

## 부록 A. 디렉토리 구조 (M3 진입 시점 — 미구현)

```
src/agents/                              ✅ M2 기존
├── bull_bear/                           ✅ M2 완료
├── scenario/                            ⏳ M3 신규
│   ├── schemas.py                       ⏳ §2.1, §2.2, §2.3
│   ├── pricing.py                       ⏳ §4.1 (순수 함수)
│   ├── pricing_config.py                ⏳ §4.2
│   ├── context_builder.py               ⏳ §3.3 to_prompt_markdown 포함
│   ├── agent.py                         ⏳ §3.2 (Bull/Bear agent.py 패턴 재사용)
│   ├── lambda_core.py                   ⏳ §6.2
│   └── trigger_evaluator.py             ⏳ §7
└── prompts/
    ├── scenario_system.md               ⏳ §3.2
    └── scenario_user.md                 ⏳ §3.3

src/lambdas/
└── agent_scenario/                      ⏳ M3 신규
    └── handler.py                       ⏳ thin wrapper

infra/step_functions/
└── screening_workflow.asl.json          ✅ M2 + ⏳ M3 (ScenarioMap state 추가)

scripts/
└── run_scenario_golden.py               ⏳ M3 신규 (4종목 fixture)

tests/
└── golden/scenario/                     ⏳ M3 신규 ({symbol}.json 4개)
```

## 부록 B. 4단계 (최적화) 인터페이스 계약

`docs/04-optimizer.md` (작성 예정) 가 본 단계 출력을 입력으로 받음.

| 4단계 필요 필드 | 본 단계 출력 | 비고 |
|---|---|---|
| `expected_return` (per symbol) | `ExpectedReturn.expected_return` | 직접 사용 |
| `expected_price` (per symbol) | `ExpectedReturn.expected_price` | 직접 사용 |
| `variance` (per symbol) | `ExpectedReturn.variance` | covariance matrix 의 *대각* 입력. 비대각 (종목 간 상관) 은 4단계가 OHLCV 에서 별도 계산. **variance floor 는 4단계 책임** (v0.6 §4.2 경계) — scenario 가격 클러스터로 variance~0 인 종목을 4단계가 riskless 로 오판하지 않도록 covariance 대각 구성 시 floor 적용 |
| 시나리오별 가격 (선택) | `ExpectedReturn.scenario_prices` | sensitivity 분석 / portfolio stress test |
| 사용된 config (lineage) | `ExpectedReturn.pricing_config` | 4단계도 이를 로깅해 어떤 config 의 산출이 최적화에 사용됐는지 추적 |

4단계는 `s3://{bucket}/expected_returns/dt={...}/symbol=*.json` 을 모두 로드 후 PyPortfolioOpt 등 라이브러리 입력.

## 부록 C. 참고

- 옵션 C 결정 근거: docs §1.4 표
- 가격 산정 공식 유도: docs §4.1
- Bull/Bear 인터페이스: [`docs/02-bull-bear.md` §2.2](02-bull-bear.md#22-출력-bullbearopinion)
- Anthropic JSON mode + Pydantic 검증 패턴: [`docs/02-bull-bear.md` §3](02-bull-bear.md#3-프롬프트-설계)
- 트리거 자동 검증의 학습 가치: Self-Verification 패턴 (CHARTER §4 LLM 학습 항목 "3가지 에이전트 패턴" 중 하나)
