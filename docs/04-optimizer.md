# 04. 포트폴리오 최적화 설계

> **단계**: 4단계 — 포트폴리오 최적화 (**LLM 사용 ❌** — CHARTER §3.3, 순수 코드)
> **상위 문서**: [`CHARTER.md`](../CHARTER.md), [`CLAUDE.md`](../CLAUDE.md)
> **선행 문서**: [`docs/03-scenario.md`](03-scenario.md) — 본 단계 입력원 (부록 B 인터페이스 계약), 운영 중
> **후행 문서**: `docs/05-rebalancing.md` (작성 예정) — 본 단계 출력(`TargetPortfolio`)을 입력으로 받음
> **버전**: v0.2 (2026-08-10)
> **상태**: **설계 확정 — 구현 대기** (구현 순서 §11)
>
> **v0.2 변경 (설계 확정)**:
> ① §9 검토 포인트 3건 사용자 승인 — 종목 상한 **15% 확정** (08-10 실데이터 dry-run 근거: 20% 는 포지션 하한 미달 + 집중 증가, ER 이득 무의미), VAR_FLOOR 0.0025·corr 252d·shrinkage λ=0.2 제안값으로 시작 (구현 후 9주 재실행 재검증), hysteresis 는 5단계 소관 확정.
> ② 잔여 §9 항목은 전부 구현 후 데이터 게이트 또는 v2 — 설계 차단 요소 없음.
>
> **v0.1 핵심 결정 4건** (2026-08-10 확정):
> ① 최적화 방식 = **Mean-Variance (PyPortfolioOpt)** — CHARTER §3.3 예시 그대로
> ② 음수 ER 국면 **현금 보유 허용** — 후보 부족 시 부족분 현금 (§4.5 규칙)
> ③ 패키징 = **컨테이너 이미지** — numpy/scipy/cvxpy 로 50MB zip 한계 초과 확실 → infra/README 1순위 방침 발동 (optimizer Lambda 부터 적용)
> ④ `data_quality_flags` 종목 = **제외** — v0.17 이후 주당 ≤1건 수준이라 유니버스 손실 미미

---

## 0. TL;DR

3단계가 산출한 종목별 `ExpectedReturn`(기대수익·분산)을 입력으로, **주 1회 목표 비중(`TargetPortfolio`)을 결정적으로 산출**한다. LLM 호출 없음 — PyPortfolioOpt mean-variance + CHARTER 제약(long-only·10~15종목·섹터 ≤35%·최소 3%) + 현금 허용. 동시에 **옵션 B baseline 포트폴리오를 병렬 산출**해 §1.4.2 #3(옵션 C vs B portfolio outcome) 측정 인프라를 완성한다.

```
expected_returns/dt=D/*  ─┐
scenario_contexts/dt=D/* ─┼→ [품질 게이트] → [ER·Cov 구성] → [MV 최적화] → [제약 후처리]
ohlcv/ticker=*/          ─┘                                                    │
                                                                               ▼
                                                    portfolios/dt=D/target.json (primary + option_b baseline)
```

## 1. 목적과 범위

### 1.1 무엇을 하는가
- 3단계 산출물(20종목 ExpectedReturn)을 **목표 비중 벡터 + 현금 비중**으로 변환
- 옵션 B baseline (Bull/Bear confidence 코드 점수화) 포트폴리오 병렬 산출 — §1.4.2 #3 측정 인프라
- 산출 lineage 보존: 어떤 config/입력으로 이 비중이 나왔는지 재현 가능 (CHARTER 2순위)

### 1.2 비범위 (이 단계가 하지 않는 것)
- ❌ 매매 결정·주문 (5단계 — 현 보유와 목표 비중의 차이를 매매로 변환)
- ❌ LLM 호출 (CHARTER §3.3 — "에이전트의 에이전트"는 v2)
- ❌ 공매도·레버리지 (CHARTER §5)
- ❌ 일중 갱신 (주 1회, 5단계 리밸런싱 주기와 동일)
- ❌ hysteresis/turnover 억제 — **5단계 소관** (M1 §10 이관 항목과 함께 5단계 설계에서 결정. 4단계는 매주 독립적으로 "지금 최적"만 산출)

### 1.3 CHARTER 정합성 체크

| CHARTER 조항 | 반영 |
|---|---|
| §3.3 4단계 LLM ❌, PyPortfolioOpt 예시 | MV 최적화, LLM 호출 0 (§3) |
| §3.2 포지션 10~15개 | cardinality 후처리 (§4.4) |
| §3.2 단일 섹터 ≤35% | MV 제약 (§4.3) |
| §3.2 최소 포지션 3% | 후처리 컷 + 재정규화 (§4.4) |
| §5 공매도·레버리지 ❌ | long-only, Σw ≤ 1 (§4.3) |
| §2.3 페이퍼 $10,000 | 비중만 산출 — 금액 환산은 5단계 |
| §6 할루시네이션 → 매매는 룰 기반 | LLM 산출물(확률)은 ER 로만 유입, 비중 결정은 결정적 코드 |

## 2. 입출력 스키마

### 2.1 입력 (S3, 같은 dt 파티션)

| 소스 | 사용 필드 | 비고 |
|---|---|---|
| `expected_returns/dt=D/symbol=*.json` | `primary.expected_return` / `primary.variance` / `primary.scenario_prices` / `primary.data_quality_flags` / `primary.pricing_config` (lineage) | Bundle 포맷 (§03 §4.4). `alternatives` 는 회고용 — 최적화 입력은 primary 만 |
| `scenario_contexts/dt=D/symbol=*.json` | `current_price` (variance 수익률 변환) / `sector` (섹터 제약) / `bull_opinion`·`bear_opinion` (option_b baseline — §6) | context 는 시나리오 성공 종목만 존재 = 최적화 유니버스와 자연 일치 |
| `ohlcv/ticker=*/data.parquet` | 일간 `adj_close` → 로그수익률 상관 (§4.2 비대각) | 3단계와 같은 데이터 — 추가 수집 없음 |

### 2.2 출력 (`TargetPortfolio` — Pydantic)

```python
class TargetPortfolio(BaseModel):
    as_of_date: date
    method: Literal["max_sharpe"]              # v1 고정 (§3.2)
    weights: dict[str, float]                  # symbol → 비중 (0 제외, 3%~15%)
    cash_weight: float                         # 1 - Σweights (§4.5)
    expected_portfolio_return: float           # wᵀμ (현금 0% 가정)
    portfolio_variance: float                  # wᵀΣw
    # ---- 품질 게이트 lineage (§5) ----
    universe_size: int                         # 입력 종목 수 (보통 20)
    excluded: dict[str, str]                   # symbol → 제외 사유
    n_candidates: int                          # 게이트 통과 + ER>0
    # ---- 재현성 lineage ----
    pricing_config_hash: str                   # 입력 ER 들의 config (전 종목 동일 검증)
    covariance_params: CovarianceParams        # corr window/shrinkage/floor (§4.2)
    computed_at: datetime

class OptimizerBundle(BaseModel):
    primary: TargetPortfolio                   # 옵션 C (시나리오 ER) — 5단계 실제 입력
    option_b_baseline: TargetPortfolio | None  # §6 — 비교용, 5단계 미사용
```

저장: `s3://{bucket}/portfolios/dt={D}/target.json` (5단계 입력 계약 — 부록 A)

## 3. 최적화 방식

### 3.1 방식 비교 — 왜 Mean-Variance 인가

| 후보 | 장점 | 단점 | 판정 |
|---|---|---|---|
| **A. MV (PyPortfolioOpt)** | CHARTER 예시 그대로 / ER·variance 를 실제로 소비 (3단계 산출물의 존재 이유) / 섹터·비중 제약을 최적화 내에서 처리 | cvxpy 의존 (컨테이너로 해소 — §7) / 입력 노이즈 민감 (§4 게이트·floor 로 완화) | ✅ **채택** |
| B. 스코어 비례 배분 | 의존성 0, 단순 | variance·상관을 버림 — 3단계 산출물 절반 미사용, "최적화" 학습 목표 미달 | 기각 (§9 — MV 불안정 시 fallback 후보로만 박제) |
| C. MV 손수 구현 | 학습 가치 | 구현·검증 비용, solver 품질 | 기각 (v2 학습 아이템) |

### 3.2 목적함수 — max Sharpe (rf=0), v1 고정

- `EfficientFrontier.max_sharpe(risk_free_rate=0.0)` — 후보군 내 위험 대비 기대수익 최대화
- min volatility·efficient risk 등 대안은 §9 이월 (§1.4.2 #3 비교가 자리잡은 후 A/B)
- 결정성: 같은 입력 → 같은 출력 (solver seed 무관한 convex 문제). 입력 lineage 로 재현

## 4. 알고리즘 상세

### 4.1 ER 벡터 (μ)

```
μ_i = ExpectedReturn.expected_return  (primary, v0.17 config)
```
- **시간 지평**: 시나리오 ER 은 "다음 분기 발표까지" (~13주) 지평. 연율화하지 않음 —
  포트폴리오 내 *상대 비교* 가 목적이고 전 종목 동일 지평이므로 스케일 일관 (§9 비고)

### 4.2 Covariance (Σ) — 하이브리드 구성 (03 부록 B 계약 이행)

```
대각:   σ²_i = max( variance_i / current_price_i²,  VAR_FLOOR )     # price² → return² 변환 + floor
비대각: Σ_ij = ρ_ij × σ_i × σ_j                                     # 상관은 historical, 스케일은 시나리오
ρ:      OHLCV adj_close 일간 로그수익률 상관 (window 252d) + shrinkage λ=0.2 → ρ' = (1-λ)ρ + λI
```

- **variance 단위 주의**: `ExpectedReturn.variance` 는 가격² 공간 — 반드시 `current_price²` 로 나눠 수익률² 로 변환
- **VAR_FLOOR = 0.0025** (= 가격 5% 이동 상당): ALL 형 퇴화(bear=base=bull=current → var=0, §03 §12.3 (b)) 종목이 riskless 로 오판되는 것 차단 — 03 부록 B 가 지정한 4단계 책임
- shrinkage 는 표본 상관의 노이즈 완화 (20×20, 252 관측 — Ledoit-Wolf 도입은 §9 이월, v1 은 단순 λ 고정)
- PSD 보정: 하이브리드 Σ 가 PSD 아닐 수 있음 → 고유값 클리핑 (`min eigenvalue ≥ 1e-8`) 후 사용

### 4.3 MV 제약 (최적화 내)

| 제약 | 값 | 근거 |
|---|---|---|
| long-only | `w_i ≥ 0` | CHARTER §5 |
| 종목 상한 | `w_i ≤ 0.15` | 포지션 10~15개와 정합 (균등 ~7-10% 대비 여유). **v0.1 제안값 — 검토 포인트** |
| 섹터 상한 | `Σ_{i∈sector} w_i ≤ 0.35` | CHARTER §3.2. sector 는 ScenarioContext 에서 |
| 완전 배분 | `Σw = 1` (후보군 내) | 현금은 §4.5 에서 별도 층위로 처리 |

### 4.4 Cardinality 후처리 (10~15종목 + 최소 3%)

MV 는 cardinality 제약을 직접 못 푼다 → 결정적 후처리 (순서 고정):

```
1. MV 해에서 w_i < 3% 종목 제거 → 남은 종목으로 재최적화 (최대 3회 반복, 수렴 보장)
2. 종목 수 > 15 → 비중 하위부터 제거 후 재최적화
3. 종목 수 < 10 → 그대로 수용 (후보 부족 국면 — §4.5 현금이 흡수. 강제로 채우지 않음)
4. 최종 검증: 3% ≤ w_i ≤ 15%, 섹터 ≤35%, Σw = 1 (후보군 내)
```

### 4.5 현금 규칙 (v0.1 — 단순·설명가능 우선)

```
후보 = 품질 게이트 통과(§5) AND expected_return > 0
투자비중 = min(1.0, n_candidates / 10)          # 후보 10개 이상 → 완전 투자
cash_weight = 1 - 투자비중
weights = MV_해 × 투자비중                       # §4.3~4.4 는 후보군 내에서 수행
```

- 근거: 음수 ER 종목을 long-only 로 살 이유 없음. 음수 skew 40~65% 국면에서 후보가
  5개면 50% 만 투자 — 보수적 config 철학(CHARTER §6)과 정합
- 후보 0개 → 전액 현금 (`weights={}`) — 5단계는 이를 "전량 매도 대기" 로 해석
- 고도화(시장 국면 연동 등)는 §9 — **성급한 정교화 회피** (측정 인프라 먼저)

## 5. 데이터 품질 게이트 (최적화 전 제외 규칙)

| 게이트 | 규칙 | 사유 기록 (`excluded`) |
|---|---|---|
| G1 flag | `data_quality_flags` 비어있지 않음 → 제외 | `"data_quality_flags: <flag>"` |
| G2 ER 결측 | expected_returns 파일 없음 (시나리오 스킵 종목) | `"expected_return_missing"` |
| G3 상관 결측 | OHLCV < 60 거래일 (신규 상장 등) → 제외 | `"insufficient_ohlcv"` |
| G4 config 불일치 | 전 종목 `pricing_config` 동일 검증 — 불일치 시 **런 전체 실패** (부분 배포 등 이상 신호) | (실패 — §8) |

- G1 은 v0.1 결정 ④ (제외). DD 형(극소 EPS, §03 §12.3 (d))도 flag 경유로 자연 제외됨
- 제외 사유는 `TargetPortfolio.excluded` 로 lineage 보존 — 주간 리포트·회고 입력

## 6. 옵션 B baseline (§03 §1.4.2 #3 측정 인프라)

옵션 C(시나리오 ER) vs 옵션 B(Bull/Bear 직접 점수화)의 **portfolio outcome 비교**를 위해
baseline 포트폴리오를 병렬 산출한다. **위치 = 4단계** (3단계 코드 무변경 — bull/bear
opinion 과 가격 산식이 모두 입력으로 이미 존재):

```
# confidence 는 Literal["low","medium","high"] (02 §2.2) → 수치 매핑 low=1/medium=2/high=3
score_bull = Σ score(bull_opinion.arguments[].confidence)
score_bear = Σ score(bear_opinion.arguments[].confidence)
p_base = 0.34 (고정)                                   # 3-class 매핑의 중립 질량
p_bull = (score_bull / (score_bull + score_bear)) × (1 - p_base)
p_bear = 1 - p_base - p_bull
ER_b_i = compute_expected_return(확률만 교체, 같은 scenario_prices·config)
       → 같은 §4~5 파이프라인으로 baseline TargetPortfolio 산출
```

- 추가 LLM 비용 0 (기존 opinion 재사용). `OptimizerBundle.option_b_baseline` 에 저장
- 5단계는 primary 만 소비 — baseline 은 M3 말 12주 회고에서 두 포트폴리오의 가상
  수익률 비교로만 사용 (observe-only)

## 7. 오케스트레이션·패키징

### 7.1 Step Functions

`RunScreening → BullBearMap → ScenarioMap → RunOptimizer` (Task state 추가)
- ScenarioMap 부분 실패(일부 종목 스킵) 허용 — optimizer 는 존재하는 ER 만 로드 (G2)
- RunOptimizer 실패 시 `Catch` → 실패 기록 후 종료 (3단계 산출물은 이미 저장됨 —
  5단계가 "이번 주 목표 비중 없음 = 보유 유지" 로 해석, 5단계 설계에서 확정)

### 7.2 Lambda — 컨테이너 이미지 (v0.1 결정 ③)

- `run_optimizer`: **컨테이너 이미지 기반** 신규 생성 — numpy/scipy/cvxpy/PyPortfolioOpt
  (+pyarrow) 로 zip 50MB 한계 초과 확실 → infra/README "컨테이너 전환 1순위" 방침 첫 적용
- 단일 이미지 (base: `public.ecr.aws/lambda/python:3.12`) + ECR. 빌드 시
  `docker run <image> python -c "import pypfopt, cvxpy"` 스모크 (pyarrow 사고 교훈)
- 기존 6개 zip Lambda 는 유지 — 전환은 다음 한계 도달 시 (방침대로)
- Memory 1024MB / Timeout 300s / LLM 시크릿 불필요 (FMP·Anthropic 접근 없음 — S3 만)

### 7.3 디렉토리 (CLAUDE.md 구조 준수 — LLM ❌ 이므로 `src/optimizer/`)

```
src/optimizer/
├── schemas.py            # TargetPortfolio / OptimizerBundle / CovarianceParams
├── data_loader.py        # S3 로드 + 품질 게이트 (§5) — I/O 격리
├── covariance.py         # §4.2 하이브리드 Σ (순수 함수)
├── optimize.py           # §4.3~4.5 MV + 후처리 + 현금 (순수 함수)
├── baseline.py           # §6 옵션 B 확률 매핑 (순수 함수)
└── lambda_core.py        # 조립 + 로깅 + S3 저장
src/lambdas/run_optimizer/handler.py
infra/docker/optimizer.Dockerfile
```

## 8. 비용·로깅·실패 처리

- **LLM 비용 0**. 인프라 증분: ECR ~$0.1/월, Lambda 실행 주 1회 ~수 초 — 무시 가능
- 로깅 (CloudWatch): `n_candidates / cash_weight / 상위 5 비중 / excluded 사유 / wᵀμ, wᵀΣw`
- 실패 모드: solver infeasible (제약 충돌) → 완화 순서 고정 (종목 상한 15→20% → 섹터 제약 완화 없이 실패 처리) — **섹터 35% 는 완화 불가** (CHARTER 명시 제약)
- 게이트 G4 (config 불일치) → 런 실패 (조용한 오염 방지 — epsDiluted 교훈)

## 9. 미해결 / 검토 포인트 (v0.1)

- [~] **종목 상한 15%** — 제안값. **1차 검증 (2026-08-10 실데이터 미니 dry-run, §4 파이프라인 그대로)**: 후보 12종목에서 cap 15% → **10종목**·HHI 0.109·포트 ER 3.61% / cap 20% → **9종목**(CHARTER §3.2 포지션 하한 10개 **미달**)·HHI 0.131·ER 3.85%. 20% 의 ER 이득 +0.24%p 는 입력 추정 오차 대비 무의미한 반면 상위 2종목 집중 30%→38%. **cap 이 포지션 개수와 커플링** — 상한을 올리면 종목 수가 하한 아래로 떨어짐 (20% 채택 시 §4.4 에 최소 10종목 강제 재분배 로직 필요). → **15% 유지**. 구현 후 9주 재실행으로 최종 확정
- [ ] **VAR_FLOOR 0.0025 / corr window 252d / shrinkage λ=0.2** — 제안값. 민감도는 구현 후 과거 주차 재실행으로 측정 (시나리오 데이터 9주 축적분 활용)
- [ ] **현금 규칙 고도화** (§4.5) — v1 은 후보 수 비례. 시장 국면·ER 크기 연동은 운영 데이터 후
- [ ] **§03 §12.3 config A/B 확정** — sensitivity 4종 대안으로 4개 포트폴리오 시뮬 → primary config 재확정 (구현 후 백테스트 형태로)
- [ ] **목적함수 A/B** (max Sharpe vs min vol) — §1.4.2 #3 측정 자리잡은 후
- [ ] **시간 지평 정합** (§4.1) — 시나리오 ER (~13주) vs 일간 상관 혼합의 이론적 비정합. v1 은 상대 비교 목적으로 수용, 정밀화는 v2
- [ ] **Ledoit-Wolf shrinkage** — v1 단순 λ 고정의 상위 호환
- [ ] **5단계 계약 확정** — 부록 A 는 초안, `docs/05-rebalancing.md` 설계 시 상호 확정

## 10. 테스트 전략 (CLAUDE.md 준수)

- 순수 함수 (covariance/optimize/baseline): 단위 테스트 — 소형 fixture (3~5종목)로
  제약 충족·후처리 수렴·현금 규칙·PSD 보정·G1~G4 게이트 검증
- PyPortfolioOpt 통합: 결정적 fixture 로 golden-형 스냅샷 (LLM 무관 — `golden` 마커 불필요)
- lambda_core: S3 목(moto 대신 기존 fake store 패턴 재사용)
- 컨테이너: 빌드 시 import 스모크 (§7.2) + 로컬 `docker run` 1회 실행 검증

## 11. 구현 순서 (커밋 단위)

1. **schemas.py** — TargetPortfolio/OptimizerBundle + 단위 테스트
2. **covariance.py** — 하이브리드 Σ + floor + PSD (순수 함수) + 테스트
3. **optimize.py** — MV + 제약 + 후처리 + 현금 규칙 + 테스트 (PyPortfolioOpt 로컬 의존 — `requirements-dev.txt`)
4. **baseline.py** — 옵션 B 확률 매핑 + 테스트
5. **data_loader.py + lambda_core.py** — S3 조립 + 게이트 + 저장 + 목 테스트
6. **로컬 dry-run** — 최근 주차(dt=2026-08-10) 실데이터로 스크립트 실행 → 산출 검토 (§9 파라미터 1차 확정)
7. **컨테이너 인프라** — Dockerfile + ECR + `run_optimizer` Lambda 생성 + 배포 스크립트 (`scripts/deploy_lambda_container.sh`) + infra/README 갱신
8. **ASL RunOptimizer state 추가** + dry-run → 주간 자동 운영 편입

## 부록 A. 5단계 (리밸런싱) 인터페이스 계약 (초안)

| 5단계 필요 | 본 단계 출력 | 비고 |
|---|---|---|
| 목표 비중 | `OptimizerBundle.primary.weights` + `cash_weight` | dt 파티션 |
| 목표 부재 처리 | `portfolios/dt=D/target.json` 미존재 | "보유 유지" (5단계 설계에서 확정) |
| 재현 lineage | `pricing_config_hash` / `covariance_params` / `excluded` | 감사·회고 |
| 페이퍼 금액 환산 | 비중 × $10,000 / 주가 | **5단계 소관** (단주 처리 포함) |
