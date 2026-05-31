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
