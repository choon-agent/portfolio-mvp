# 06. v2 재설계안 — M3 말 12주 판정의 후속

> **성격**: 설계 결정 박제 (구현 없음). MVP(2026-04-20~10-20) 종료 시점의 "다음에 무엇을 왜 바꿀 것인가".
> **상위 문서**: [`CHARTER.md`](../CHARTER.md) v0.3, [`docs/03-scenario-retro.md §0.9`](03-scenario-retro.md) (판정 원본)
> **버전**: v0.1 (2026-10-10) — retro §0.9 B1 결정 (d) 의 산출물
> **상태**: 결정 완료 · 구현은 v2 착수 시. **v1 운영은 10-20 까지 코드 변경 없이 지속.**

---

## 0. TL;DR

12주 페이퍼 운영의 결론은 세 문장이다. **(1) 파이프라인은 작동한다** — 5단계 자동 체인, 성공률 92.3%, 재시도 0/660, LLM 비용 $1.05/주. **(2) 3단계 LLM 확률은 정보가 없다** — Brier 0.636 (무작위 0.667), 확률이 0.25/0.28/0.30 격자에 고정, 모델·프롬프트·재호출로 안 바뀜. **(3) 성과 열위의 원인은 LLM 이 아니라 결정적 가격 산식과 집중도다** — ER ≈ p_bull × 52주고점 여력으로 퇴화, 15% cap × 4종목이 TE 17.9% 를 만듦.

v2 는 LLM 을 **ER 의 확률 입력에서 분리**하고(narrative·트리거라는 학습 자산은 유지), 가격 산식과 1↔3단계 방향 충돌을 고치며, 측정 지표(#2 트리거)를 재정의한다. 모두 v1 의 저장 데이터로 오프라인 검증 가능한 변경이다.

## 1. v1 이 남긴 사실 (설계 입력)

| # | 사실 | 근거 | v2 함의 |
|---|---|---|---|
| F1 | LLM 확률 = 격자 상수 (bull sd 0.035, 12주) | retro §0.5 08-24·09-21 / §0.5 09-26 분산 실험 (sd≤0.024) / Part C Brier 전 config 0.55~0.66 | 확률을 LLM 에서 받지 않는다 |
| F2 | ER 이 `p_bull × return_52w_high` 로 퇴화 (후보 78~92%) | 백테스트 Part B (ρ 0.46→0.64 상승 추세) | base/bear cap 0 결합이 원인 — 산식 재설계 |
| F3 | 1단계 모멘텀 ↔ 3단계 52주고점 앵커 정면 충돌 (모멘텀 top-5 의 60~88% 가 ER≤0) | Part B, 전 config 공통 / 52주고점 핑퐁 왕복 4건 (WDC·TGT·MPC·VLO) | config 아닌 설계 — 1단계 또는 3단계 앵커 교체 |
| F4 | (a) base<bear 유형 flag 급증 (09-28 4건) — peer 대비 프리미엄 종목은 peer×EPS base 가 현재가의 ~50% | retro §0.5 09-28 | base 산식이 저평가/프리미엄 종목에 양분 동작 |
| F5 | 옵션 C vs B 실현 동등 (7주 −7.29% vs −7.49%) | retro §0.9 A3 | 3단계 LLM 의 ER 기여 = 0 |
| F6 | TE 17.9% > 15%, 원인은 집중 (bear_uncapped 현금 82% 는 TE 6.8%) | 백테스트 Part A / 실계좌 7주 | 종목 cap 또는 포지션 하한 재검토 |
| F7 | #2 트리거 적중률은 base rate 와 구분 불가 (94.7% = bear 미실현 95%) | retro §0.9 A2 | 지표를 lift 로 재정의 |
| F8 | fcf_yoy 가 발동의 57%, 분모 극소로 +37,133% 류 | trigger batch 전 기간 | 트리거 metric 정의·임계 가이드 |
| F9 | 운영 사고 3건 전부 의존성·배포 (pyarrow 슬리밍, SDK 1.x 암묵 업그레이드, 로그 권한) — 설계 결함 0 | retro §0.5 | lockfile·CI 컨테이너 자동화가 v2 선행 조건 |

## 2. v2 결정 (retro §0.9 B·C 에서 확정)

### 2.1 D1 — ER 확률 leg 를 LLM 에서 분리 (B1=c/d, B2)

- `compute_expected_return` 의 확률 입력을 **결정적 소스**로 교체. 1차 **균등 1/3** (bull/base/bear) — 유효 regime realized 분포(0.48/0.47/0.05)는 n=60·상승장 산물이라 overfit 위험, calibration 이 ≥200 이벤트 쌓이면 base rate 로 갱신.
- LLM 확률은 `ExpectedReturnsBundle.alternatives["llm_prob"]` 로 **병렬 보존** — "LLM 확률이 언젠가 정보를 갖는가" 를 계속 측정 (v1 의 option_b 와 같은 observe-only 패턴).
- LLM 역할 = narrative + 무효화 트리거 + (신규) **방향 순위**: 3-시나리오 확률 대신 "bull/bear 중 어느 쪽 근거가 더 강한가" 를 ordinal 로 요구하는 실험 슬롯. 확률보다 ordinal 이 LLM 에게 자연스러운 과제라는 가설 — v1 Bull/Bear confidence 가 option_b 로 이미 이 역할을 하고 있으나 Brier 최악(0.659)이었으므로 **가설일 뿐**, v2 에서 A/B.
- 효과: ER 완전 결정적 → 재현성 100%, temperature 무관. **"LLM 이 ER 에 기여하는가" 는 v1 에서 "아니오" 로 판정됐음을 명시.**

### 2.2 D2 — 가격 산식 v0.18 (B3)

- `base_price_cap_pct` 0 → **0.10** (백테스트: 퇴화 0.78→0.00, 후보 확보, 8주 +4.95%). 단 base>bull 순서 위반이 늘어 bull 산식 동반 조정 필요 — 백테스트에서 flag 0 인 조합은 `aggressive`(bull 결합 balanced + base cap 10%). **v2 1차 config = aggressive 계열**, 단 8주 +13.6% 는 08-17 한 주 +7.1% 의존이므로 성과 근거로 쓰지 않는다.
- (a) base<bear 유형(F4): peer×EPS base 가 현재가의 60% 미만이면 **peer 갈래를 결측 취급 → historical-only fallback** (= 극소 EPS (d) 가드와 같은 메커니즘, 임계 P/E 비율로 통일). DD 9주 연속 rank 1 소모도 같은 가드로 해소.
- `_validate_price_order` 유지, `peer_announcement` enum 제거, narrative 500 유지.

### 2.3 D3 — 1↔3단계 방향 충돌 해소 (B4) — **택1 미결 (v2 착수 시 백테스트로 결정)**

| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| D3-a | 3단계 bull 앵커를 52주고점 → **분기 실적 기반 적정가**(peer P/E × forward EPS 또는 TTM EPS × 성장 조정)로 교체 | 모멘텀 종목도 ER 양수 가능, 핑퐁 소멸 | EPS 의존 ↑ (음수·극소 EPS 종목은 fallback 필요) |
| D3-b | 1단계 모멘텀 팩터에 **52주고점 거리 페널티** (고점 근접 종목 z-score 감점) | 3단계 무변경, 유니버스 자기선택을 명시화 | 모멘텀 팩터의 정의가 바뀜 — 백테스트 엔진에서 모멘텀 vs 밸류 비교와 충돌 |
| D3-c | 수용 — 현상 기록만 | 0 | 왕복 매매·ER≡0 종목 지속 |

권고 D3-a. 검증: v1 저장 데이터(ctx + opinion, 12주)로 `run_config_backtest.py` 에 앵커 옵션 추가해 오프라인 비교 (LLM 0).

### 2.4 D4 — 집중도 (C1)

- 종목 cap 15% → **10%** 백테스트 (스크립트 cap 인자 1줄). 04 §9 커플링(cap↓ → 종목 수↑, 후보 부족 시 현금↑) 역방향 검증. TE 15% 이하 달성 여부가 Phase 2 실전 전환 판단의 열쇠.
- 보조: 섹터 상한 35% 는 12주 동안 한 번도 바인딩 안 됨(매주 7~10 섹터 분산) — 유지.

### 2.5 D5 — 5단계 (C2)

- band 면제 허용오차 0.5%p — **v1 에서 구현·배포 완료** (05 v0.4, 2026-10-10).
- 신규 편입 지연 "2주 연속 후보" — D3 가 근본 원인이라 **D3 결정 후** 필요 시. v1 왕복 4건은 전부 52주고점 핑퐁 유형.

### 2.6 D6 — 측정 지표 (A2, F7, F8)

- #2 트리거 적중률 → **lift** = P(시나리오 미실현 | 트리거 발동) − P(시나리오 미실현) . 합격선은 lift > 0 유의 (base rate 95% 환경에서 비율 자체는 무의미).
- fcf_yoy: 전기 FCF 절대값 하한(예: 매출의 1%) 미만이면 평가불가 처리. LLM 트리거 가이드에 "yoy 임계는 ±30% 이상, 분모 극소 metric 회피" 추가.
- `earnings_surprise` 는 FMPClient 엔드포인트 확장(12% 사용) 또는 enum 제거 중 택1.
- #1 Brier 는 D1 이후 **LLM 확률(alternatives) 에 대해서만** 계속 측정 — ER 에는 영향 없는 순수 관찰 지표.

### 2.7 D7 — 운영·인프라 (C3)

| 항목 | v2 선행 조건 여부 |
|---|---|
| 의존성 lockfile (pip-compile) + SDK 1.x 이행 (temperature 제거 완료, httpx2 등) | **선행** — F9 사고 3건 중 2건의 재발 방지 |
| CI 컨테이너 자동화 (ECR push, OIDC ecr 권한) | 선행 — 로컬 colima 배포 함정 3건 |
| S3 versioning + noncurrent 30일 lifecycle | 선행 — 08-17 오염 사고 재발 방지. **IAM 권한상 콘솔 작업** (아래 명령) |
| trigger batch · 비용 리포트 Lambda 승격 | 동반 — 수동 실행이 3주 밀린 전력 |
| DeepEval judge 다양화 (Opus 또는 cross-vendor), Lambda 자동화 | 후순위 |
| Debate 패턴 (02 §10 v2) | CHARTER §4 "3패턴" 미완분 — v2 학습 1순위 후보 |

```bash
# S3 versioning (admin 권한 필요 — 콘솔 또는 admin 프로필)
aws s3api put-bucket-versioning --bucket portfolio-mvp-data-s3 --versioning-configuration Status=Enabled
aws s3api put-bucket-lifecycle-configuration --bucket portfolio-mvp-data-s3 --lifecycle-configuration '{"Rules":[{"ID":"noncurrent-30d","Status":"Enabled","Filter":{},"NoncurrentVersionExpiration":{"NoncurrentDays":30}}]}'
```

## 3. v2 에서도 바꾸지 않는 것

- 5단계 파이프라인 구조 (Step Functions 체인, 컨테이너/zip 분리, 실패 비승격 설계) — 12주 무사고.
- 옵션 C 의 **분리 원칙** "LLM ≠ 가격 산정" — 오히려 D1 로 더 강화.
- S3 lineage 레이아웃 (dt 파티션, input_hash 캐시, snapshot 선기록) — 재현성 검증 통과.
- CHARTER §8 확정 사항.

## 4. v2 착수 순서 (제안)

1. D7 선행 조건 (lockfile·SDK 1.x·S3 versioning·CI 컨테이너) — 운영 변경 없음
2. `run_config_backtest.py` 확장: 확률 소스 옵션(D1)·bull 앵커 옵션(D3)·cap 인자(D4) → v1 12주 데이터로 오프라인 A/B
3. 백테스트 결과로 D1/D2/D3/D4 파라미터 확정 → 설계 문서 v0.2
4. 구현·배포 → 새 regime 시작 (v1 과 계좌 분리: `accounts/v2_primary`)
5. 측정 지표 D6 적용, 12주 재판정

## 5. 미결 (v2 착수 시 결정)

- D3 택1 (a/b/c) — 백테스트 후
- D1 의 ordinal 방향 순위 실험 포함 여부 — 학습 1순위 관점에서 포함 권고, 비용 중립
- v2 유니버스: S&P 500 유지 vs 유동성·EPS 양수 필터 (F4·DD 유형 사전 차단) — CHARTER §3.1 범위 내
