# 05. 리밸런싱 설계

> **단계**: 5단계 — 리밸런싱 오케스트레이터 (**매매 결정 LLM ❌** — CHARTER §3.3 룰 기반. LLM "근거/요약 생성"은 v2 이월 §9)
> **상위 문서**: [`CHARTER.md`](../CHARTER.md), [`CLAUDE.md`](../CLAUDE.md)
> **선행 문서**: [`docs/04-optimizer.md`](04-optimizer.md) — 본 단계 입력원 (부록 A 인터페이스 계약), 운영 중
> **버전**: v0.2 (2026-09-03) — **설계 확정** (§8 검토 포인트 ①~⑤ 전건 사용자 승인)
> **상태**: 설계 확정 — 구현 착수 (§10 순서)
>
> **v0.2 확정 사항 5건** (§8 검토 포인트 — 2026-09-03 사용자 승인):
> ① 체결 모델 = **직전 거래일(금요일) adj_close 를 체결가로 기록, 수수료·슬리피지 0, 소수점 주식 허용** (look-ahead 없음·재현성 우선)
> ② hysteresis = **no-trade band |Δw| < 1.5%p** 단독 + **CHARTER 범위(3~15%) 밖 드리프트는 band 면제** (신규 편입 지연 규칙은 데이터 게이트)
> ③ **계좌 2개 병렬 운영** (primary + option_b 실계좌, 동일 규칙) — §03 §1.4.2 #3 실현수익률 비교 인프라, LLM 비용 0
> ④ 벤치마크 = **SPY 를 update_ohlcv 수집 대상에 추가**, tracking error = 주간 액티브 수익률 std × √52 ≤ 15% (조작적 정의 — M3 말 Charter 재검토 시 추인)
> ⑤ 패키징 = **전용 컨테이너 이미지** (`infra/docker/rebalancer.Dockerfile` + ECR `portfolio-mvp/run_rebalancer`) — optimizer 와 독립 배포, `deploy_lambda_container.sh` 패턴 재사용

---

## 0. TL;DR

4단계가 산출한 목표 비중(`portfolios/dt=D/target.json`)과 페이퍼 계좌의 현 보유 상태를
비교해, **주 1회 룰 기반으로 매매 목록을 산출·체결하고 계좌 상태를 갱신**한다. LLM 호출
없음. 동일 로직으로 primary(옵션 C)와 option_b(baseline) **두 계좌를 병렬 운영**해
§03 §1.4.2 #3(portfolio outcome 비교)의 실현수익률 트랙을 만든다. CHARTER §4.1 실전
전환 기준 중 두 가지(주간 리밸런싱 성공률·tracking error vs SPY)의 측정 주체가 본 단계다.

```
portfolios/dt=D/target.json ─┐
accounts/{id}/state.json    ─┼→ [평가(NAV)] → [Δw 산출 + band] → [매도→매수 체결] → 상태 갱신
ohlcv/ticker=*/ (체결가)    ─┘                                                        │
                                                                                      ▼
                                        accounts/{id}/dt=D/snapshot.json (+ state.json 갱신)
```

## 1. 목적과 범위

### 1.1 무엇을 하는가
- 목표 비중 → **매매 주문 목록(TradeOrder) + 체결 후 계좌 상태(AccountState)** 변환
- 페이퍼 계좌 상태의 유일한 기록원 (가상 $10,000 — CHARTER §2.3)
- 주간 성과 측정: NAV·주간 수익률·SPY 대비 액티브 수익률 (§6) — CHARTER §4.1 데이터
- primary / option_b 두 계좌 병렬 운영 (§1.4.2 #3 측정 인프라 완성 — 4단계 §6 의 후속)
- 산출 lineage 보존: 어떤 target/가격/규칙으로 이 매매가 나왔는지 재현 가능 (CHARTER 2순위)

### 1.2 비범위 (이 단계가 하지 않는 것)
- ❌ 비중 재계산·최적화 (4단계 소관 — 본 단계는 `weights` 소비만)
- ❌ LLM 호출 — 매매는 룰 기반 (CHARTER §3.3·§6). "근거/요약 생성"(🟡)도 v1 제외 (§9)
- ❌ 실전 주문 (Phase 2 에서도 수동 확정 후 — CHARTER §5)
- ❌ 일중/일일 리밸런싱 (주 1회 고정), 긴급 리밸런싱 자동 트리거 (수동만 — CHARTER §2.4)
- ❌ 세금·배당 모델링 (페이퍼 단순화 — 배당은 §8 검토 포인트, v1 무시)

### 1.3 CHARTER 정합성 체크

| CHARTER 조항 | 반영 |
|---|---|
| §3.3 5단계 매매는 룰 기반, LLM 근거/요약만 | LLM 호출 0 (요약 생성도 v2 — §9) |
| §2.3 페이퍼 $10,000 | 계좌 초기값 (§4.1) |
| §2.4 주 1회 (월 프리마켓) / 긴급은 수동만 | 파이프라인 편입 주 1회 실행. 체결가 = 직전 거래일 종가 (§3.2). 수동 invoke 는 멱등 가드 (§7.3) |
| §3.2 최대 15개·3~15%·섹터 ≤35% | 4단계 산출이 이미 보장 — 본 단계는 재검증만 (§5 G3) |
| §5 공매도·레버리지 ❌ | 매도 후 매수 순서 + 현금 부족 시 매수 축소 (§3.4) — 음수 현금·음수 수량 불가 |
| §4.1 리밸런싱 성공률·tracking error 측정 | 주간 스냅샷 + 성과 필드 (§6) |
| §6 할루시네이션 → 매매는 룰 기반 | 입력은 4단계 결정적 산출물만 |

## 2. 입출력 스키마

### 2.1 입력

| 소스 | 사용 필드 | 비고 |
|---|---|---|
| `portfolios/dt=D/target.json` | `primary.weights`/`cash_weight` (primary 계좌), `option_b_baseline.*` (option_b 계좌), lineage 필드 | 04 부록 A 계약. **미존재 시 보유 유지** (§5 G1) |
| `accounts/{id}/state.json` | 현 보유 (shares·cash) | 미존재 시 $10,000 현금으로 초기화 (§4.1) |
| `ohlcv/ticker=*/data.parquet` | 최신 `adj_close` (체결가·평가가) | 보유 종목이 유니버스 top-20 을 이탈해도 가격 필요 → OHLCV 가 유일한 공통 소스 (scenario_contexts 는 20종목만 존재) |
| `ohlcv/ticker=SPY/data.parquet` | 벤치마크 (§6) | **신규 수집 대상 1종 추가** — 기존 update_ohlcv 파이프 재사용 |

### 2.2 출력 (Pydantic — `src/rebalancer/schemas.py`)

```python
class TradeOrder(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    shares: float                       # 소수점 4자리 (§3.3)
    ref_price: float                    # 체결가 = 직전 거래일 adj_close
    notional: float                     # shares × ref_price
    reason: Literal["rebalance", "new_position", "exit_position", "liquidate_all"]

class Position(BaseModel):
    shares: float
    avg_cost: float                     # 평균 취득 단가 (참고용 — 매매 규칙엔 미사용)

class AccountState(BaseModel):
    account_id: Literal["primary", "option_b"]
    as_of_date: date                    # 마지막 리밸런싱 기준일
    cash: float
    positions: dict[str, Position]
    inception_date: date                # 계좌 생성일 (성과 기산점)

class RebalanceSnapshot(BaseModel):     # accounts/{id}/dt=D/snapshot.json
    account_id: str
    as_of_date: date
    pre_state: AccountState             # 체결 전 상태 (원복·감사용 — 08-17 오염 사고 교훈)
    trades: list[TradeOrder]
    post_state: AccountState
    # ---- 평가·성과 (§6) ----
    nav: float                          # 체결 후 NAV (= cash + Σ shares×price)
    prices_used: dict[str, float]       # 평가가 lineage
    weekly_return: float | None         # 직전 스냅샷 대비 (첫 주 None)
    spy_weekly_return: float | None
    # ---- lineage ----
    target_dt: date | None              # 소비한 target.json 파티션 (보유 유지 주는 None)
    no_trade_band: float
    skipped_by_band: dict[str, float]   # symbol → |Δw| (band 미달로 스킵)
    computed_at: datetime
```

저장 레이아웃 (S3, 신규 prefix `accounts/`):

```
accounts/primary/state.json             # 현재 상태 (덮어쓰기 — 단, snapshot 선기록 후)
accounts/primary/dt=2026-09-07/snapshot.json
accounts/option_b/...                   # 동일 구조
```

**쓰기 순서 고정**: snapshot(불변) 먼저 → state.json(가변) 갱신. state 가 유실돼도
최신 snapshot 의 post_state 로 복원 가능 (S3 versioning 미설정 환경 방어 — retro §0.5 08-17).

## 3. 매매 규칙 (결정적 알고리즘)

### 3.1 전체 흐름 (순서 고정)

```
1. state 로드 (없으면 초기화) / target 로드 (없으면 G1 — 보유 유지 스냅샷만 기록)
2. 평가: NAV = cash + Σ shares × price  (price = 직전 거래일 adj_close)
3. Δw 산출: 대상 종목 = 보유 ∪ target. Δw_i = target_w_i − current_w_i
   (target 에 없는 보유 종목은 target_w = 0)
4. no-trade band: |Δw_i| < BAND(1.5%p) 면 스킵 (§3.5) — skipped_by_band 에 기록
5. 매도 먼저 전량 체결 → 현금 확보 → 매수 체결
6. 현금 검증: Σ매수 notional > 가용 현금이면 매수 전체를 비례 축소 (§3.4)
7. 상태 갱신 + 스냅샷 기록 (§2.2 쓰기 순서)
```

### 3.2 체결 모델 — 확정 (검토 포인트 ①, 2026-09-03)

- **체결가 = 실행 시점 OHLCV 의 최신 `adj_close`** (월요일 실행 → 금요일 종가).
  CHARTER §2.4 "월요일 프리마켓" 과 정합 — 프리마켓 시점에 존재하는 마지막 확정 가격
- **수수료 0 / 슬리피지 0** — 페이퍼 v1. 근거: 주 1회 × ~$10k 계좌에서 실측 비용은
  수 $ 수준으로 tracking error 판정(±15%)에 무의미. 정교화는 §8 (5bps 대안 박제)
- 같은 가격을 평가(NAV)와 체결에 동일 사용 → 체결로 인한 NAV 점프 없음 (결정성)

### 3.3 단주 처리 — 소수점 주식 허용 (소수 4자리 절사)

정수 주 강제는 **불가능**: 유니버스에 MU $966 같은 종목 존재 — 1주 = 계좌의 ~10% 라
3~15% 비중 밴드를 정수 주로 유지할 수 없음 (2026-08-31 실데이터). 페이퍼이므로
fractional 이 단순·정확. 실전 전환(M4) 시 브로커 지원 여부로 재검토 (§8).

### 3.4 Long-only·무레버리지 불변식

- 매도 shares ≤ 보유 shares (초과 매도 절대 불가 — clip)
- 매수는 매도 후 잔여 현금 한도 내. 부족 시 **매수 전체 비례 축소** (개별 우선순위
  없음 — 자의적 순서 배제). 체결 후 `cash ≥ 0` assert
- target 이 빈 dict (후보 0) → **전량 매도** (`reason=liquidate_all`) — 04 §4.5 계약 이행

### 3.5 Hysteresis — no-trade band — 확정 (검토 포인트 ②, 2026-09-03)

```
BAND = 0.015 (1.5%p, 총자산 대비)   # 환경변수 조정 가능 (§7.2)
|Δw_i| < BAND → 매매 스킵 (보유 유지 / 미편입 유지)
단, 현재 비중이 CHARTER 범위 밖이면 band 면제 (항상 체결):
  current_w_i > 0.15  (cap 초과 드리프트 — 트림 강제)
  0 < current_w_i < 0.03  (하한 미달 드리프트 — 목표대로 증액 또는 청산)
```

- **1.5%p 근거**: 최소 포지션 3% 의 절반 — 신규 편입(Δw ≥ 3%)·전량 청산(Δw ≥ 3%)은
  band 에 걸리지 않고 항상 실행되므로, band 는 *기존 보유의 미세 조정만* 걸러냄.
  $10,000 기준 $150 미만 거래 제거 효과
- **범위 밖 면제 근거**: band 만으로는 cap 종목이 주중 상승해 15%+band 까지 초과
  보유될 수 있음 (예: 16.4% 시 Δw 1.4%p < band → 트림 스킵) — CHARTER §3.2
  상한·하한은 band 보다 우선. 면제는 *체결 강제* 이며 목표 비중 자체는 불변
- 효과 실측 (08-17~08-31 target 시뮬): 거래 건수 ~25% 감소 (주당 8~9건 중 2건 스킵),
  턴오버 금액 절감은 ~1%p 로 미미 — 몸통은 종목 교체·대형 이동 (band 비대상).
  band 의 주 목적은 잔거래 제거 + M4 실전 대비 hysteresis 동작 측정 데이터 축적
  (v1 은 거래비용 0 이라 금전 효과 0 — §3.2)
- **비대상**: 종목 교체 자체(WDC 1주 보유 → 이탈 유형)는 band 로 막을 수 없음.
  "신규 편입 2주 연속 후보 요건" 같은 지연 규칙은 **도입 보류** — 상태 복잡도 추가
  대비 근거 사례가 1건(WDC)뿐. band 단독 운영 4주 후 턴오버 재측정으로 결정 (§8)
- band 로 스킵된 잔차는 다음 주 Δw 에 자연 이월 (누적 드리프트가 band 초과 시 체결)

### 3.6 두 계좌 병렬 운영 — 확정 (검토 포인트 ③, 2026-09-03)

- 같은 알고리즘을 `primary`(옵션 C)와 `option_b`(baseline) 에 각각 적용 — 코드 1벌,
  루프 2회. LLM·데이터 비용 0
- 목적: §03 §1.4.2 #3 판정을 **예측 ER 비교가 아닌 실현 수익률 비교**로 승격.
  현재 3주 연속 baseline 예측 ER 우위 관찰(retro §0.5) — 실현 트랙 없이는 판정 불가
- option_b 가 target.json 에 없는 주(null): 해당 계좌만 보유 유지 (G1 과 동일 처리)
- 5단계 이후 단계(실전 전환)는 **primary 계좌만** 대상 — option_b 는 observe-only

## 4. 계좌 수명주기

### 4.1 초기화
- `state.json` 미존재 시: `cash=10000, positions={}, inception_date=실행일` 생성
- **백필 (§10 구현 순서 #7)**: 첫 배포 전에 로컬 스크립트로 2026-08-17(첫 target)부터
  주차별 리플레이해 계좌를 씨딩 — 저장된 target.json + OHLCV 만 사용 (lookahead 없음,
  결정적). 10월 초 판정 시점의 #3 실현 표본을 3주 앞당기는 효과. 리플레이 결과는
  동일 스냅샷 포맷으로 S3 박제 (`inception_date=2026-08-17`)

### 4.2 주간 갱신
- 정기: Step Functions `RunOptimizer → RunRebalancer` (§7.1)
- 보유 유지 주 (target 부재·상류 실패): 매매 없이 스냅샷만 기록 — **성과 시계열은
  끊기지 않음** (NAV 평가는 가격만 있으면 가능)

### 4.3 멱등성 (08-17 파티션 오염 사고 교훈 — retro §0.5)
- `accounts/{id}/dt=D/snapshot.json` **이미 존재 시 no-op 종료** (재실행이 상태를
  이중 적용하는 것 차단). 강제 재실행은 로컬 스크립트 + 명시 `--force` 만
- 과거 as_of 수동 실행 금지 (infra/README 경고와 동일 — 스크리닝처럼 현재 데이터로
  재계산되는 문제는 없지만, state 순서가 꼬임)

## 5. 게이트 (실행 전 검증)

| 게이트 | 규칙 | 처리 |
|---|---|---|
| G1 target 부재 | `portfolios/dt=D/target.json` 미존재 | **보유 유지** — 매매 0, 스냅샷 기록 (04 부록 A 계약 확정) |
| G2 가격 결측 | 보유·target 종목 중 OHLCV 최신가 없음 | **런 실패** (부분 체결은 상태 오염 — 조용히 스킵하지 않음) |
| G3 사후 불변식 | 체결 후 cash < 0, 비중 합 ≠ 1±0.01, 음수 shares | assert 실패 → 상태 미갱신·런 실패 |
| G4 가격 신선도 | 최신 adj_close 가 실행일 기준 5 거래일 초과 과거 | **런 실패** (update_ohlcv 상류 문제 신호) |

- G2~G4 실패 시 state.json 은 **건드리지 않음** — 다음 주 정상 실행이 자연 복구
  (Δw 는 상태 기반이라 한 주 건너뛰어도 목표 수렴)

## 6. 성과 측정 (CHARTER §4.1 데이터 산출)

- **주간 수익률**: `r_t = NAV_t / NAV_{t-1} − 1` (연속 스냅샷 간, 같은 평가 규칙)
- **벤치마크**: SPY 동일 구간 수익률 (adj_close — 배당 반영). **SPY 를 update_ohlcv
  수집 대상에 추가** (검토 포인트 ④ 확정, 2026-09-03 — 기존 파이프 재사용, 코드 변경 최소)
- **Tracking error 정의 (CHARTER §4.1 "≤15%" 의 조작적 정의)**:
  `TE = std(r_t − r_SPY,t) × √52` (주간 액티브 수익률의 연환산 표준편차).
  누적 4주부터 스냅샷에 기록, 판정은 M3 말 (§8 검토 — CHARTER 문언이 정의를
  특정하지 않아 본 문서가 확정, Charter 재검토 시 추인)
- **리밸런싱 성공률**: 주차별 스냅샷 존재 여부가 곧 성공 기록 — CloudWatch 실패
  로그와 함께 §4.1 "3개월 ≥90%" 판정의 원천
- 주간 리포트·시각화는 M3 말 회고에서 일괄 (v1 은 스냅샷 원본 축적만 — 성급한
  대시보드 회피)

## 7. 오케스트레이션·패키징

### 7.1 Step Functions

`RunScreening → BullBearMap → ScenarioMap → RunOptimizer → RunRebalancer` (Task 추가)
- `RunOptimizer` 실패(Catch → RecordOptimizerFailure) 경로에서도 **RunRebalancer 는
  실행** — G1 보유 유지 스냅샷으로 성과 시계열 유지 (ASL: RecordOptimizerFailure →
  RunRebalancer 체이닝)
- `RunRebalancer` 실패 시 Catch → RecordRebalancerFailure (비승격 — 1~4단계 산출물
  보존, §5 의 자연 복구 전제)

### 7.2 Lambda — 전용 컨테이너 이미지 — 확정 (검토 포인트 ⑤, 2026-09-03)

- pyarrow(OHLCV parquet 읽기) 필요 → zip 슬리밍은 전 람다 import 즉사 사고 전례
  (302f3cb). infra/README 방침(한계 도달 시 컨테이너) 적용
- **전용 이미지**: `infra/docker/rebalancer.Dockerfile` (base:
  `public.ecr.aws/lambda/python:3.12`) + `infra/docker/rebalancer-requirements.txt`
  (pyarrow·pydantic — cvxpy/PyPortfolioOpt 불필요, optimizer 보다 경량) →
  ECR `portfolio-mvp/run_rebalancer`. optimizer 와 **독립 배포** (커플링 없음)
- 빌드·배포는 기존 `scripts/deploy_lambda_container.sh` 패턴 재사용 — import 스모크
  (`docker run <image> python -c "import pyarrow; import rebalancer.schemas"`) 포함
  (pyarrow 사고·colima 함정은 infra/README 박제분 준수)
- Memory 512MB / Timeout 120s / 시크릿 불필요 (S3 만)
- 설정 채널: `REBALANCE_BAND`(기본 0.015), `INITIAL_CASH`(기본 10000) 환경변수

### 7.3 디렉토리 (CLAUDE.md 구조 준수 — LLM ❌ 이므로 `src/rebalancer/`)

```
src/rebalancer/
├── schemas.py          # §2.2 (TradeOrder / AccountState / RebalanceSnapshot)
├── pricing.py          # OHLCV 최신가 로드 + G2/G4 (I/O 격리)
├── trade_rules.py      # §3 매매 규칙 (순수 함수 — state × target × prices → trades)
├── performance.py      # §6 수익률·TE (순수 함수)
└── lambda_core.py      # 계좌 루프(primary/option_b) + 게이트 + S3 저장 + 로깅
src/lambdas/run_rebalancer/handler.py
scripts/run_rebalancer_dry.py     # 로컬 dry-run + 백필(--replay-from) + --force
```

## 8. 미해결 / 검토 포인트 (①~⑤ 확정 — v0.2. 잔여는 데이터 게이트·v2)

- [x] **① 체결 모델** (§3.2): **확정 (2026-09-03 사용자 승인)** — 체결가 = 직전
  거래일(금요일) adj_close 그대로 기록 (look-ahead 없음: 의사결정 시점에 이미 확정된
  과거 가격 + 1~4단계와 동일 데이터 시점), 수수료·슬리피지 0, 소수점 주식 허용.
  현실과의 차이는 주말 갭뿐 (방향성 없는 노이즈 — TE 판정 무영향). 실전 전환 시 재설계
- [x] **② no-trade band 1.5%p** (§3.5): **확정 (2026-09-03 사용자 승인)** — 범위 밖
  드리프트 band 면제 포함. 4주 운영 후 턴오버 실측으로 크기 재검증.
  "신규 편입 2주 요건" 은 band 단독 운영 데이터 본 뒤 결정 (데이터 게이트)
- [x] **③ 계좌 2개 병렬** (§3.6): **확정 (2026-09-03 사용자 승인)** — option_b 도
  실계좌 운영 (동일 규칙·동일 band). 턴오버·band 효과까지 동일 조건 비교
- [x] **④ 벤치마크·TE 정의** (§6): **확정 (2026-09-03 사용자 승인)** — SPY 를
  update_ohlcv 수집 대상에 추가, TE = 주간 액티브 수익률 std × √52 ≤ 15%.
  Charter 재검토(M3 말) 시 정의 추인
- [x] **⑤ 패키징** (§7.2): **확정 (2026-09-03 사용자 승인)** — 전용 컨테이너 이미지
  (`rebalancer.Dockerfile` + ECR `portfolio-mvp/run_rebalancer`), optimizer 와 독립
  배포 (커플링 회피)
- [ ] **배당 처리**: adj_close 사용으로 벤치마크에는 반영되나 보유 종목 현금
  배당은 미모델링 (adj_close 기반 수익률엔 근사 반영). v1 무시 — 오차 주간
  bp 수준. 실전 전환 시 재검토
- [ ] **긴급 리밸런싱 수동 절차**: CHARTER §2.4 허용 범위의 runbook 화 (v2)
- [ ] **4단계 부록 A 상호 확정**: "target 부재 = 보유 유지" 본 문서 §5 G1 로 확정
  → `docs/04-optimizer.md` 부록 A 의 초안 표기 해제 (구현 #1 커밋에서 문서 동기화)

## 9. v2 이월

- LLM 매매 근거/요약 생성 (CHARTER §3.3 🟡) — 주간 스냅샷 → 자연어 리포트.
  M3 말 재검토에서 ER 퇴화·확률 상수성 이슈(retro §0.8) 정리 후 착수 판단
- hysteresis 고도화 (거래비용 모델 연동 최적화) / 월간 성과 리포트 Lambda
  (retro §0.8 C 의 비용 리포트와 묶음)

## 10. 구현 순서 (커밋 단위)

1. **schemas.py** — §2.2 모델 + 불변식 validator + 단위 테스트. (+ 04 부록 A 확정 반영)
2. **trade_rules.py** — §3 순수 함수 (band·매도우선·비례축소·전량매도) + 테스트
   (소형 fixture: 초기화·미세조정 스킵·종목 교체·후보 0·현금 부족 케이스)
3. **performance.py** — §6 수익률·TE + 테스트
4. **pricing.py + lambda_core.py** — S3 조립 + G1~G4 + 계좌 루프 + 목 테스트
   (기존 fake store 패턴 재사용)
5. **로컬 dry-run** — `scripts/run_rebalancer_dry.py`, 08-17~최신 주차 리플레이 검증
6. **update_ohlcv 에 SPY 추가** + `rebalancer.Dockerfile`/ECR 리포지토리 생성 +
   `deploy_lambda_container.sh` 로 빌드·배포 (import 스모크 포함) + invoke 검증
7. **백필 실행** — §4.1 리플레이로 두 계좌 씨딩 (S3 박제, `--force` 경로 검증 겸)
8. **ASL RunRebalancer state 추가** — §7.1 체이닝 (RecordOptimizerFailure 경로 포함)
   + step-functions policy 갱신 + E2E. 다음 정기 실행부터 1~5단계 자동

## 부록 A. 상류(4단계) 인터페이스 계약 — 확정

| 04 부록 A 초안 항목 | 본 단계 확정 |
|---|---|
| 목표 비중 (`primary.weights` + `cash_weight`) | §3 소비. option_b 도 동일 소비 (§3.6) |
| 목표 부재 (`target.json` 미존재) | **"보유 유지" 확정** (§5 G1) |
| 재현 lineage | snapshot 에 `target_dt` + 가격 lineage 보존 (§2.2) |
| 페이퍼 금액 환산 (비중 × $10,000 / 주가) | §3.2~3.3 — 단주는 fractional 로 해소, NAV 기준 환산 (고정 $10,000 아님 — 계좌는 복리 누적) |
