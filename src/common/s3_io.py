"""S3 I/O 헬퍼.

규칙:
- 모든 경로는 키(key) 형식 (s3:// 접두사 제외)
- Parquet 쓰기는 임시 키에 먼저 쓰고 copy → 읽기 측에서 원자적으로 보임
- 빈/없는 객체는 None 반환 (예외 발생 안 함)

LLM 사용: 없음.
"""
from __future__ import annotations

import io
import logging
import uuid
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# 모듈 레벨 클라이언트 (CLAUDE.md 의 Lambda 콜드스타트 최소화 규칙)
_s3 = boto3.client("s3")


def read_parquet(bucket: str, key: str) -> pa.Table | None:
    """Parquet 객체를 읽음. 키가 존재하지 않으면 None 반환."""
    try:
        resp = _s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    body = resp["Body"].read()
    return pq.read_table(io.BytesIO(body))


def write_parquet_atomic(bucket: str, key: str, table: pa.Table) -> None:
    """원자적 교체 방식으로 Parquet 쓰기 (임시 키에 쓰기 → copy → 임시 키 삭제).

    읽기 측에서 쓰기 도중의 반쪽 파일을 볼 수 없게 보장.
    """
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)

    temp_key = f"{key}.tmp.{uuid.uuid4().hex}"
    _s3.put_object(Bucket=bucket, Key=temp_key, Body=buf.getvalue())
    try:
        _s3.copy_object(
            Bucket=bucket,
            Key=key,
            CopySource={"Bucket": bucket, "Key": temp_key},
        )
    finally:
        _s3.delete_object(Bucket=bucket, Key=temp_key)


def append_parquet(bucket: str, key: str, new_table: pa.Table) -> None:
    """기존 Parquet 객체에 행 추가 (read-merge-write).

    작은 append-only 로그(변경 이벤트)용. 고처리량 환경에는 부적합.
    """
    existing = read_parquet(bucket, key)
    if existing is None:
        combined = new_table
    else:
        # 스키마 정합: 필요 시 new_table을 기존 스키마로 캐스팅
        combined = pa.concat_tables([existing, new_table], promote_options="default")
    write_parquet_atomic(bucket, key, combined)


def object_exists(bucket: str, key: str) -> bool:
    try:
        _s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return False
        raise


def get_secret(secret_id: str, region_name: str | None = None) -> str:
    """AWS Secrets Manager에서 평문 시크릿 가져오기."""
    client = boto3.client("secretsmanager", region_name=region_name)
    resp = client.get_secret_value(SecretId=secret_id)
    return resp["SecretString"]