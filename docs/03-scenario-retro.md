# M3 시나리오 운영 회고 — 4주 (회고 프롬프트 + 체크리스트)

> **사용법 (다음 스레드)**: 4주(또는 원하는 누적 시점) 후 새 대화에서 아래처럼 시작.
>
> ```
> docs/03-scenario-retro.md 기반으로 M3 시나리오 단계의 N주 운영 회고를 진행해줘.
> 먼저 §2 데이터 수집부터 하고, §3 체크리스트로 평가한 뒤 §4 산출물(문서 박제·config 조정)을 제안해줘.
> ```
>
> 이 파일이 컨텍스트·데이터 수집 명령·평가 체크리스트·산출물 템플릿을 담는다. 회고 결과는
> `docs/03-scenario.md §12` 항목 처리 + v0.x 헤더 박제로 반영한다 (재현성 = CHARTER 2순위).

---

## 0. 컨텍스트 (회고 시작 시 빠르게 파악)

- **프로젝트**: LLM 에이전트 오케스트레이션 학습 + S&P500 페이퍼 트레이딩 MVP (`CHARTER.md` / `CLAUDE.md`)
- **M3** = 3단계 시나리오 모델링 (옵션 C: LLM 이 narrative + 확률 + 무효화 트리거만 생성, **가격은 결정적 산식** — LLM ≠ 가격 산정 분리)
- **구현 상태**: #1~#10 완료. 매주 **월 06:00 ET EventBridge** 가 `portfolio-mvp-screening` state machine 자동 실행 → `RunScreening → BullBearMap → ScenarioMap`. S3 산출: `scenarios/`, `expected_returns/`, `scenario_contexts/`
- **첫 운영 (2026-05-31)**: 20/20 ok. narrative 300→500 완화(v0.14)로 3종목(CRL/WTW/EIX) 실패 해결. 캐시 17 hit/cost 0. expected_return **음수 skew** 관찰(보수 config). 추가비용 $0.054
- **S3 버킷**: `portfolio-mvp-data-s3`

## 0.5 운영 로그 (주차별 — 회고 evidence 누적)

> 매주 실행 후 한 줄씩 append (M2 02-bull-bear §9 #9 패턴). 4주 누적 후 §3 평가의 raw data.
> `attempts>1` = 재시도/폴백 발생 종목 수. `exp_ret 음수` = expected_return < 0 종목 수.

| 주차 | dt | 종목 | ok/fail | attempts>1 | cache | 총비용 | exp_ret 음수 | turnover | flags |
|---|---|---|---|---|---|---|---|---|---|
| 1 (첫 스케줄) | 2026-06-01 | 20 | **20/0** | **0 (0%)** | 20 miss | **$0.360** | 16/20 | — | 0 |
| 2 | 2026-06-08 | 20 | **20/0** | **0 (0%)** | 20 miss | **$0.360** | 17/20 | **25% (±5)** | 0 |
| 3 | 2026-06-15 | 20 | **20/0** | **0 (0%)** | 20 miss | **$0.363** | 16/20 | **15% (±3)** | 0 |
| 4 | 2026-06-22 | 20 | **20/0** | **0 (0%)** | 20 miss | **$0.361** | 17/20 | **5% (±1)** | 0 |
| **합계 (4주)** | — | **80** | **80/0 (100%)** | **0%** | 320 miss | **~$1.45/월** | **66/80 (82.5%)** | ~15%/주 | **0** |

**메모**:
- 2026-05-31 (수동 첫 실행, baseline): narrative 300자 한계로 3종목(CRL/WTW/EIX) 실패 → v0.14(300→500) 수정 후 재실행 20/20. 첫 실행은 재시도 多($~0.5).
- Week 1: narrative 수정 후 **재시도 0%** (§5.2 "+20% ceiling" 가정 검증) / 비용 $0.36 = 예측 정합 / 음수 skew 지속 / 절반가량 전주와 동일 expected_return (가격 산식 결정성 + 하루차 데이터 동일 — §1.4.1 약점3 실증).
- Week 2: 운영 health 2주 연속 clean (20/20, 재시도 0%, $0.36, flags 0). **종목 turnover 25%** (빠짐 HII/SPG/EXPE/NTRS/WTW, 들어옴 DVA/TPR/CBOE/ALL/ZBH) → 캐시 주간 무효(전 종목 miss, $0.36 풀 비용 확정) + 포트폴리오 churn 우려(M1 §10 hysteresis 연결). **음수 skew 악화(16→17)** → §12.3 config 조정 근거 누적.
- Week 3: 3주 연속 clean. turnover 15% (빠짐 TPR/TGT/SYF, 들어옴 IQV/SPG/NTRS) — **SPG/NTRS 가 W1→W2 이탈→W3 복귀**, 전체 드리프트 아닌 *rank-20 경계 노이즈* 확인. 음수 skew 16/20 고착(3주 16/17/16 ≈ 80%). **다음 주(Week 4) = 4주 회고 트리거** — §3 체크리스트로 본격 회고.
- 2026-06-29 / 07-06: **미실행 (결번)** — pyarrow 슬리밍 배포 사고로 전 람다 import 즉사 (`302f3cb` 참조). 주차 카운트·12주 판정 시점 계산 시 2주 공백 반영.
- 2026-07-13 (복구 후 첫 정기 실행): 20/20 ok, 재시도 0, $0.36, flags 0 — 배포 수정 end-to-end 검증. 단 **sensitivity 3개 대안이 20종목 전부 primary 와 동일값** → epsDiluted 필드 버그 발견 (§0.7 정정).
- 2026-07-14 (epsDiluted 수정 후 수동 재실행, **regime change 기준점**): 20/20 ok, 재시도 0, $0.36. **sensitivity 최초 분화 14/20** (동일값 6종목 CNC/VTRS/F/KHC/CRL/SJM 은 전부 TTM EPS 음수 — `ttm_eps>0` 가드의 의도된 fallback, 정상). **음수 skew 19/20(95%, 07-13) → 11/20(55%)** — peer P/E 갈래 부활 효과, §0.6 "config 탓" 가설 사실상 기각. **`price_order_violation` 최초 7/20** (APA/EIX/ADM/ALL/PCG/SNA/INCY — bear > bull 역전): 딥밸류 종목의 peer 함의 적정가 ≫ 현재가 + bear `conservative=max` 결합의 상호작용 (§0.6 결정 2 가 예견한 케이스). expected_return 이 bear 시나리오에 의해 상방 왜곡 (EIX +35% 등) → **4단계 optimizer 투입 전 bear 가격 semantics (bear ≤ current cap 여부) 를 §12.3 에서 결정 필요**. 이번 주부터 유효 A/B 누적 시작.
- 2026-07-20 (정기, 유효 A/B 2주차): 20/20 ok, 재시도 0, $0.36. turnover 4/20 (빠짐 CRL/SJM/SNA/INCY, 들어옴 HST/DAL/TGT/UAL — 경계 노이즈 수준). sensitivity 분화 16/20 (동일값 CNC/VTRS/F/KHC = TTM EPS 음수 그룹 유지). 음수 skew 13/20(65%, 전주 55% — 주간 노이즈 범위). **config 간 부호 역전 첫 관찰**: GM primary -5.2% vs balanced +27.9% / TGT -1.9% vs +12.0% — 4단계 편입 여부가 config 에 좌우, #12 의 목적 데이터. **price_order_violation 7/20 지속** (APA/EIX/PCG/ADM/ALL 반복 + DAL/UAL 신규, 반복 종목 가격 전주와 거의 동일 = 결정적 재생산). **양수 ER 7종목 중 6종목이 역전 flag 보유** (깨끗한 양수는 ZBH 뿐) — ER 상위권이 왜곡 종목에 집중되는 구조 확인 → **§12.3 bear semantics 결정을 4단계 설계 선행 조건으로 격상 제안**.
- 2026-07-27 (정기, 유효 A/B 3주차 · **bear_capped 실운영 1주차**): 20/20 ok, 재시도 0, $0.36. turnover **6/20 (30%)** — 빠짐 VTRS/F/ADM/BDX/UAL/KHC, 들어옴 HON/DD/VLO/TKO/WDC/NEM (HON 은 HONA 스핀오프 metadata 변경 직후 편입 — 주시). 음수 skew 12/20(60%, 3주 55/65/60 안정). 위반 7/20 (EIX/APA/PCG/ALL 3주 연속 + HON/TKO/NEM 신규). **bear_capped 검증 (컨텍스트 재계산)**: 위반 7 → **1** — cap 이 bear>current 유형 6건 전부 해소 (EIX/ALL/APA/PCG/NEM/HON 모두 base 가 current 에 cap 된 상태에서 bear 만 초과 → cap 후 bear=base=current 로 순서 정상화). **잔여 1건 TKO 는 base<bear 유형** (current 180.62, bear 155.61 < current 라 cap 무관, peer base 58.72 이 극단 저평가 — ADM 유형, 신규 편입 주). capped ER 순위: HON +22.8% / NEM +14.6% / WDC +7.6% / APA +6.4% — 왜곡 제거 후에도 자연스러운 분포. 특이: ALL 은 bear=base=bull=current 로 퇴화 → ER=0·variance=0 (optimizer 중립 입력 — §12.3 비고). **승격 판단은 계획대로 8/3 한 주 더 보고** — 현재 증거는 승격 지지 (시뮬 2주 + 실운영 1주 일관).
- 2026-08-03 (정기, 유효 A/B 4주차 · bear_capped 실운영 2주차 · **§12.3 승격 결정**): 20/20 ok, 재시도 0, $0.37. turnover 5/20 (빠짐 HON/PCG/TKO/STLD/ROST, 들어옴 EXPE/KHC/UAL/MPC/CFG). 음수 skew 11/20(55%). 위반 6/20 (EIX/APA/ALL 4주 연속 + DAL/UAL/NEM) — **전부 bear>current 유형, bear_capped 적용 시 6→0 잔여 없음** (컨텍스트 재계산 검증. base<bear 유형인 ADM/TKO 는 universe 이탈). capped ER: EIX +46.1%→+2.7% / UAL +21.0%→+3.7% / NEM +29.5%→+10.2% 등 — 4주 연속 동일 패턴. **결정: `bear_price_cap_pct` 기본값 0.0 승격 (v0.17)** — 근거는 시뮬 2주(07-20 7→1) + 실운영 2주(07-27 7→1, 08-03 6→0) 일관성. 대안은 "bear_uncapped"(counterfactual) 로 교체해 구 동작 계속 관찰. **4단계(04-optimizer) 선행 조건 해소**. 특이 관찰: MU 가 -46.9%(07-27)→+10.2%(08-03) 대폭 스윙 — LLM 확률·52주 앵커 롤링에 따른 주간 변동, #13 calibration 데이터로 추적.

- 2026-08-08 (**#13 trigger batch 로컬 PoC 첫 실행** — `scripts/run_trigger_batch.py`, LLM 비용 0): 전체 9주 × 20종목 = 200쌍 중 **163 채점 완료** (37 not_filed_yet — 발표 분산 6/25~8/7, §12.3 B filingDate 조인 실증). 결과 S3 `trigger_evaluations/` 박제. **트리거 489건: met 115(23.5%) / 인간검토 14(guidance_change — EIX 반복) / 평가불가 82** (earnings_surprise 57 = v1 클라이언트 미지원, fcf_yoy 17 = prior≤0 정의불가). **관찰 ①**: fcf_yoy 발동 남발 — FCF 분기 변동성(MU +951%, STLD +2190% 등)에 비해 LLM threshold(±15~20%)가 너무 좁음 → 트리거 품질 이슈, 프롬프트 가이드 후보 (§12.3, DeepEval 게이트 대상). **관찰 ② calibration (표본 145, flag 제외 18)**: realized base 77 / bull 67 / **bear 1** — Brier 평균 0.619 (uniform 0.667 대비 근소 우위, 합격선 0.25 미달). 단 (a) 표본 대부분이 epsDiluted 버그 regime 가격 bin 이라 §1.4.2 판정에 그대로 못 씀, (b) 인접 주차가 같은 발표를 채점 → 독립 관측은 ~35개 수준 (통계 주의). **관찰 ③**: earnings_surprise 가 489중 57(12%) — FMPClient 확장(earnings-surprises 엔드포인트) 가치 확인.

## 0.6 4주 회고 결과 (2026-06-22)

**판정**: M3 시나리오 4주 운영 — **운영 health 전 기준 합격, 옵션 C 유지**. 산출물에 음수 skew 1건 확인(설계된 보수성 부작용), 측정 인프라(#12) 활성화로 데이터 기반 config 결정 준비.

### 합격 (운영)
- 성공률 **80/80 = 100%** (CHARTER §4.1 ≥90%) / 재시도 **0%** / 비용 **~$1.45/월** (§5.2 추정 정합) / `data_quality_flags` **0/80** / 자동 운영 4주 무사고.

### 결정
1. **음수 skew (82.5%, 4주 16/17/16/17)** — 원인 `base_price_cap_pct=0.0`(base ≤ 현재가) + bull conservative. 4단계 long-only 퇴화 위험. → **#12 sensitivity 로깅 활성화** 결정 (지금 config 변경 대신 ExpectedReturnsBundle 로 balanced/base_cap_10/aggressive 대안을 *병렬 산출*, 추가 비용 0). 몇 주 A/B 누적 후 4단계 설계 시 config 확정.
2. **`_validate_price_order` 유지** — 4주 위반 0 이나 config 완화(#12 대안/추후 변경) 시 위반 가능 → 안전망 유지 (제거 안 함).
3. **§5.2 재시도 주석** — "+20% ceiling" 추정 → **실측 0%** (4주) 로 갱신.
4. **turnover ~15%/주 (경계 노이즈)** — M3 아닌 **M1 §10 hysteresis** 과제로 이관.

### 미측정 (이월 — 12주 + #13 + 4단계 필요)
- §1.4.2 옵션 C 최종 성공 판정: #1 Brier calibration / #2 트리거 적중률 / #3 portfolio vs 옵션 B. → #13(trigger batch, 분기 발표 후) + 4단계 완성 후 별도 회고.

### S3 데이터 필요 (미완)
- metric 사용 빈도(peer_announcement 비율) / narrative 길이 분포(500 충분성) / 확률 분포 → `§2 데이터 수집` 후 다음 회고에서.

## 0.7 정정 (2026-07-14) — epsDiluted 필드 버그로 §0.6 일부 결론 재해석

**발견**: 07-13 실행에서 sensitivity 3개 대안(balanced/base_cap_10/aggressive)이
20종목 전부 primary 와 소수점까지 동일 → 추적 결과 **FMP v3→stable 마이그레이션 때
소비 코드 4곳이 구 표기 `epsdiluted` 를 읽어 `ttm_eps` 가 M2 가동 시점부터 전 종목
None** (stable 은 `epsDiluted`). 캐시엔 데이터 40분기치 정상 존재 — 읽는 키만 불일치.
수정: `common.fundamentals.normalize_income_rows`(fetch 시점 정규화) + 읽기 4곳
camelCase 통일 + 픽스처를 실제 API 표기로 갱신 (픽스처가 코드 복제품이라 443개
테스트가 못 잡았음).

**§0.6 결론 영향**:
1. **음수 skew 원인 정정** — §0.6 결정 1 의 "`base_price_cap_pct=0.0`(base ≤ 현재가)
   + bull conservative" 는 기전이 틀림. 실제로는 `ttm_eps=None` → peer P/E 갈래 전멸
   → bull/bear 는 52주 고저 단독, **base 는 cap 이 아니라 결측 fallback 으로 현재가
   고정**. cap/aggressiveness 는 죽은 경로였음.
2. **#12 sensitivity A/B 데이터 전부 무효** (첫 가동 ~ 07-13) — 세 대안이 조절하는
   peer 갈래 자체가 죽어 있어 차이가 생길 수 없었음. A/B 누적은 수정 배포 후부터 재시작.
3. **운영 health 판정(합격)은 유지** — 성공률·재시도·비용·플래그는 이 버그와 무관.
4. **데이터 단절(regime change)**: 수정 배포 이후 expected_return 은 peer P/E 갈래가
   살아나며 수준·음수 비율이 구조적으로 변함. **이전 주차와 분포 비교 금지**, config
   완화 여부(§12.3)는 수정 후 데이터로 다시 판단.

**교훈**: (a) 결측 허용(fallback) 설계는 이런 유형의 버그를 무증상으로 만든다 —
`ttm_eps` 결측을 `data_quality_flags` 에 추가하는 것 검토 (§12 후보). (b) 픽스처는
실제 API 응답에서 뜰 것. (c) health 지표(성공률/비용)는 산출물의 의미적 결함을 못 본다.

## 1. 선행 읽기

- `docs/03-scenario.md`: **§1.4.2**(성공 기준 3) / **§5.2**(비용·재시도 가정) / **§7**(트리거 자동검증) / **§12.1~12.4**(미해결 4그룹)
- `docs/03-scenario.md` v0.14 헤더 (첫 운영 피드백 — narrative / 재시도율 / 음수 skew)

## 2. 데이터 수집 (N주 누적)

```bash
REGION=ap-northeast-2
BUCKET=portfolio-mvp-data-s3
mkdir -p retro_data

# (1) 주차 수·종목 수 파악
aws s3 ls s3://$BUCKET/expected_returns/        # dt=... 폴더 몇 개(=몇 주)
aws s3 ls s3://$BUCKET/expected_returns/ --recursive | grep -c 'symbol='

# (2) 분석용 다운로드 (expected_returns = 산식 결과, scenarios = LLM 출력)
aws s3 cp s3://$BUCKET/expected_returns/ retro_data/expected_returns/ --recursive
aws s3 cp s3://$BUCKET/scenarios/         retro_data/scenarios/         --recursive

# (3) CloudWatch — 비용/재시도/캐시 (agent_scenario)
#     완료 로그: cost_usd / attempts / cache / expected_return / data_quality_flags
aws logs filter-log-events --region $REGION \
  --log-group-name /aws/lambda/portfolio-mvp-agent_scenario \
  --filter-pattern '"stage": "completed"' --start-time <N주전_epoch_ms> \
  --query 'events[].message' --output text > retro_data/completed.log
#     검증 실패(재시도 원인) 로그:
aws logs filter-log-events --region $REGION \
  --log-group-name /aws/lambda/portfolio-mvp-agent_scenario \
  --filter-pattern '"schema_valid": false' --start-time <N주전_epoch_ms> \
  --query 'events[].message' --output text > retro_data/schema_fail.log
```

> 다운로드 후 파이썬으로 집계 (expected_return 분포, data_quality_flags 빈도, scenario 의 metric 사용 빈도, 확률 평균 등). scenarios/*.json 의 `scenario_opinion.scenarios[].invalidation_trigger.metric` 을 카운트하면 metric 사용 분포가 나온다.

## 3. 평가 체크리스트

### A. 운영 health (4주에 측정 가능)
- [ ] **주간 실행 성공률** (실패 종목/주) — CHARTER §4.1 실전전환 기준 "≥90%"
- [ ] **주간 비용** (cost_usd 합/주) — steady-state **~$0.36 예상**(20×$0.018) vs 실제 → §5.2 갱신
- [ ] **재시도율** (attempts>1 비율) — narrative 수정 후 *진짜* retry rate. §5.2 "+20% ceiling" 가정 검증 (M2 는 0%)
- [ ] **캐시 hit율** (의도치 않은 재실행 시 cost=0 유지)
- [ ] **`data_quality_flags` 빈도** — `price_order_violation` 발생률 → §12.3 "4주 위반 0 이면 `_validate_price_order` 제거 검토"
- [ ] **narrative 길이 분포** — 500 한계 충분한지 (450자+ 빈번하면 추가 완화 / schema_fail.log 에 length 에러 잔존?)

### B. 산출물 분포 (4주에 측정 가능)
- [ ] **expected_return 분포** (종목·sector별) — 음수 skew 정도 → §12.3 "config 기본값 조정" 판단 근거
- [ ] **scenario_prices 순서** — bear≤base≤bull 위반 빈도
- [ ] **metric 사용 빈도** — `peer_announcement` 비율(§12.3 "5주차 정책 결정") / 자동측정 metric 비율(목표 ≥80%)
- [ ] **확률 분포** (bull/base/bear 평균·표준편차) — cap/floor 필요성(§12.3)

### C. §12.3 데이터 게이트 결정 (충분하면 결정 → docs §12 `[x]`)
- [ ] **config 기본값 조정** — 음수 skew 심하면 `bull_aggressiveness`/`base_price_cap_pct` 완화 검토 (pricing_config.py 기본값 변경 + 회귀 + 골든 재검토)
- [ ] **`peer_announcement` 정책** — 빈도 따라 enum 유지/제거/치환 유도
- [ ] **`_validate_price_order` 제거 여부** — 위반 0 이면
- [ ] **TTM EPS 결측 정책** — 결측 분포 확인 후
- [ ] **narrative 500 충분성** — length 에러 재발 여부

### D. 아직 측정 불가 (12주 / #13 / 4단계 필요 — 별도 일정으로 분리)
- §1.4.2 **#1 Brier calibration** — `trigger_evaluator`(#13) batch 활성화 + **분기 발표 데이터** 필요
- §1.4.2 **#2 트리거 적중률** — 동일 (자동측정 metric 한정)
- §1.4.2 **#3 portfolio outcome vs 옵션 B** — **#12** option_b_baseline 산출 + **4단계(docs/04-optimizer.md)** 완성 필요
- → 이 3개(옵션 C 최종 성공 판정)는 **12주 + 분기 발표 + 4단계** 후 별도 회고. 4주 회고에서는 *"#12/#13 활성화 시점"* 만 결정.

## 4. 산출물 (회고 결과 반영)

- `docs/03-scenario.md` §12 항목 `[ ]→[x]` 처리 + **v0.x 헤더에 회고 결정 박제** (날짜·근거·결정)
- config 조정 시: `src/agents/scenario/pricing_config.py` 기본값 변경 → `pytest -q` 회귀 → 골든(`pytest -m golden`) 재검토 (필요시 `run_scenario_golden.py` 재생성)
- §5.2 비용·재시도 주석을 **실측치로 갱신** (추정 → 실측)
- **다음 단계 결정**: (a) #12 sensitivity 로깅 활성화 / (b) #13 trigger batch (분기 발표 시즌) / (c) docs/04-optimizer.md 4단계 착수 (부록 B 인터페이스 계약 사용)

---

## 부록: 빠른 집계 스니펫 (다운로드 후)

```python
import json, glob, statistics
ers = [json.load(open(p)) for p in glob.glob("retro_data/expected_returns/**/*.json", recursive=True)]
rets = [e["expected_return"] for e in ers]
print("n:", len(rets), "음수비율:", sum(r < 0 for r in rets) / len(rets))
print("expected_return 평균/중앙값:", statistics.mean(rets), statistics.median(rets))
flagged = [e["symbol"] for e in ers if e.get("data_quality_flags")]
print("data_quality_flags 종목:", flagged)

# metric 사용 빈도
from collections import Counter
metrics = Counter()
for p in glob.glob("retro_data/scenarios/**/*.json", recursive=True):
    op = json.load(open(p))["scenario_opinion"]
    for s in op["scenarios"]:
        metrics[s["invalidation_trigger"]["metric"]] += 1
print("metric 사용:", metrics.most_common())
```
