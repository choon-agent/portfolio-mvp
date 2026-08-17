#!/usr/bin/env bash
# 긴급 중단 스위치 — CHARTER §6 "월 $200 하드캡, 초과 시 Lambda 자동 중단"의 실행 수단.
#
# 사용법:
#   scripts/emergency_stop.sh           # 주간 파이프라인 스케줄 비활성화 (LLM 호출 원천 차단)
#   scripts/emergency_stop.sh --all     # + 데이터 갱신 스케줄(constituents/ohlcv)까지 중단
#   scripts/emergency_stop.sh --resume  # 전체 재개
#
# 참고: AWS Budgets(portfolio-mvp-monthly-200)가 50/80/100% 에서 이메일 경보 —
# 100% 경보 수신 시 이 스크립트 실행이 운영 절차. 완전 자동 중단(Budgets Action
# → IAM deny)은 실비용이 상한의 ~3% 수준이라 보류 (infra/README 검토 항목).

set -euo pipefail

REGION=${AWS_REGION:-ap-northeast-2}
PIPELINE="portfolio-mvp-weekly-screening"
DATA_SCHEDULES=("portfolio-mvp-update-constituents-weekly-scheduler" "portfolio-mvp-update-ohlcv-daily-scheduler")

set_state() {  # <schedule_name> <ENABLED|DISABLED>
  local name=$1 state=$2
  # update-schedule 은 전체 정의 필수 — 기존 정의를 읽어 State 만 교체
  local def
  def=$(aws scheduler get-schedule --name "$name" --region "$REGION" --no-cli-pager \
        --query "{ScheduleExpression:ScheduleExpression,Target:Target,FlexibleTimeWindow:FlexibleTimeWindow,ScheduleExpressionTimezone:ScheduleExpressionTimezone}" \
        --output json)
  aws scheduler update-schedule --name "$name" --region "$REGION" --state "$state" \
    --cli-input-json "$(echo "$def" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k:v for k,v in d.items() if v is not None}))")" \
    --no-cli-pager >/dev/null
  echo "  $name → $state"
}

if [ "${1:-}" = "--resume" ]; then
  echo "==> 전체 스케줄 재개"
  set_state "$PIPELINE" ENABLED
  for s in "${DATA_SCHEDULES[@]}"; do set_state "$s" ENABLED; done
else
  echo "==> 주간 파이프라인 중단 (LLM 호출 차단)"
  set_state "$PIPELINE" DISABLED
  if [ "${1:-}" = "--all" ]; then
    echo "==> 데이터 갱신 스케줄 중단"
    for s in "${DATA_SCHEDULES[@]}"; do set_state "$s" DISABLED; done
  fi
  echo "재개: scripts/emergency_stop.sh --resume"
fi
