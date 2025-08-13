import re
import time
import json
import uuid
import boto3
import traceback
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import concurrent.futures as _f
from typing import Optional, List, Dict, Tuple

# ===== 설정 =====
REGION = "ap-northeast-2"

# S3 (데이터/로그 분리)
S3_BUCKET_DATA = "aws2-airwatch-data"   # 센서 데이터가 들어있는 버킷 (기존)
CHATLOG_BUCKET = "chatlog-1293845"      # <-- 요청하신 채팅 로그 저장용 버킷
S3_PREFIX = ""  # 데이터 폴더 구분은 키에서 자동 판단

# RAG/검색
TOP_K = 8
LIMIT_CONTEXT_CHARS = 100000
MAX_FILES_TO_SCAN = 100000
MAX_WORKERS = 10
MAX_FILE_SIZE = 1024 * 1024  # 1MB
RELEVANCE_THRESHOLD = 1  # 더 관대한 임계값으로 조정

# 필드 동의어/라벨
FIELD_SYNONYMS = {
    "온도": "temperature", "temp": "temperature", "temperature": "temperature",
    "습도": "humidity", "hum": "humidity", "humidity": "humidity",
    "공기질": "gas", "가스": "gas", "gas": "gas", "ppm": "gas",
    "co2": "gas", "co₂": "gas", "이산화탄소": "gas"
}
FIELD_NAME_KOR = {"temperature": "온도", "humidity": "습도", "gas": "이산화탄소(CO2)"}

# Bedrock (Inference Profile ARN for Claude Sonnet 4)
INFERENCE_PROFILE_ARN = "arn:aws:bedrock:ap-northeast-2:070561229682:inference-profile/apac.anthropic.claude-sonnet-4-20250514-v1:0"

# ===== 클라이언트 =====
s3 = boto3.client("s3", region_name=REGION)           # 데이터 접근용
s3_logs = boto3.client("s3", region_name=REGION)      # 로그 저장용 (동일 리전)
bedrock_rt = boto3.client("bedrock-runtime", region_name=REGION)

# ===== 시간대 보정 (내부 비교는 'KST naive') =====
KST = timezone(timedelta(hours=9))
def _to_kst_naive(dt: datetime) -> datetime:
    if dt.tzinfo:
        return dt.astimezone(KST).replace(tzinfo=None)
    return dt

# ===== 토크나이저 & 정규화 =====
def tokenize(s: str):
    return re.findall(r"[A-Za-z0-9가-힣_:+-]+", s.lower())

def normalize_query_tokens(q: str):
    tokens = tokenize(q)
    return [FIELD_SYNONYMS.get(t, t) for t in tokens]

def detect_fields_in_query(raw_query: str):
    q = raw_query.lower()
    fields = set()
    if ("습도" in raw_query) or ("hum" in q) or ("humidity" in q): fields.add("humidity")
    if ("온도" in raw_query) or ("temp" in q) or ("temperature" in q): fields.add("temperature")
    if ("공기질" in raw_query) or ("가스" in raw_query) or ("gas" in q) or ("ppm" in q)or ("co2" in q) or ("co₂" in raw_query) or ("이산화탄소" in raw_query): fields.add("gas")
    return fields

def want_detail_list(query: str) -> bool:
    detail_words = ["상세", "자세히", "자세하게", "상세히", "원본", "목록"]
    q = query.strip()
    return any(word in q for word in detail_words)

# ===== 날짜/시간 파싱 =====
ISO_PAT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
def extract_datetime_strings(s: str):
    out = []
    out += re.findall(ISO_PAT, s)  # ISO8601
    patterns = [
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}",
        r"\d{4}-\d{2}-\d{2}"
    ]
    for p in patterns: out += re.findall(p, s)

    # 개선된 한국어 날짜 파싱 - 여러 패턴 지원 (오전/오후 포함)
    korean_patterns = [
        # 2025년 8월 11일 14시 00분 05초
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분\s*(\d{1,2})\s*초",
        # 2025년 8월 11일 14시 00분
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분",
        # 2025년 8월 11일 14시
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시",
        # 2025년 8월 11일
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        # 8월 11일 14시 1분의 5초 (의 조사 포함)
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분의?\s*(\d{1,2})\s*초",
        # 8월 11일 오후 2시 1분 5초 (오전/오후 포함)
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(오전|오후)\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분\s*(\d{1,2})\s*초",
        # 8월 11일 오후 2시 1분 (오전/오후 포함)
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(오전|오후)\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분",
        # 8월 11일 오후 2시 (오전/오후 포함)
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(오전|오후)\s*(\d{1,2})\s*시",
        # 8월 11일 14시 00분 05초 (연도 없음)
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분\s*(\d{1,2})\s*초",
        # 8월 11일 14시 00분 (연도 없음)
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분",
        # 8월 11일 14시 (연도 없음)
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시"
    ]

    for i, pattern in enumerate(korean_patterns):
        m = re.search(pattern, s)
        if m:
            groups = m.groups()
            if i < 4:  # 연도가 포함된 패턴
                y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
                h = int(groups[3]) if len(groups) > 3 and groups[3] else 0
                mi = int(groups[4]) if len(groups) > 4 and groups[4] else 0
                se = int(groups[5]) if len(groups) > 5 and groups[5] else 0
            elif i == 4:  # "8월 11일 14시 1분의 5초" 패턴
                y = datetime.now().year
                mo, d = int(groups[0]), int(groups[1])
                h = int(groups[2]) if len(groups) > 2 and groups[2] else 0
                mi = int(groups[3]) if len(groups) > 3 and groups[3] else 0
                se = int(groups[4]) if len(groups) > 4 and groups[4] else 0
            elif i >= 5 and i <= 7:  # 오전/오후 패턴 (5, 6, 7번 패턴)
                y = datetime.now().year
                mo, d = int(groups[0]), int(groups[1])
                ampm = groups[2]  # 오전/오후
                h = int(groups[3]) if len(groups) > 3 and groups[3] else 0
                mi = int(groups[4]) if len(groups) > 4 and groups[4] else 0
                se = int(groups[5]) if len(groups) > 5 and groups[5] else 0

                # 오전/오후 처리
                if ampm == "오후" and h != 12:
                    h += 12
                elif ampm == "오전" and h == 12:
                    h = 0
            else:  # 연도가 없는 일반 패턴 (현재 연도로 가정)
                y = datetime.now().year
                mo, d = int(groups[0]), int(groups[1])
                h = int(groups[2]) if len(groups) > 2 and groups[2] else 0
                mi = int(groups[3]) if len(groups) > 3 and groups[3] else 0
                se = int(groups[4]) if len(groups) > 4 and groups[4] else 0

            out.append(f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{se:02d}")
            break  # 첫 번째 매치만 사용

    return out

def parse_dt(dt_str: str):
    try:
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return _to_kst_naive(dt)
    except Exception:
        pass
    for f in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(dt_str, f)
            return _to_kst_naive(dt)
        except Exception:
            pass
    return None

# === 질의 단위 감지 ===
def minute_requested(query: str) -> bool:
    q = query.strip()
    if re.search(r"\d{1,2}\s*시\s*\d{1,2}\s*분", q): return True
    if re.search(r"\b\d{1,2}\s*분\b", q): return True
    if re.search(r"(?:\b|t)\d{1,2}:\d{2}\b", q, flags=re.IGNORECASE): return True
    return False

def second_requested(query: str) -> bool:
    q = query.strip()
    if re.search(r"\d{1,2}\s*초", q): return True  # \b 제거
    if re.search(r"\b\d{1,2}:\d{2}:\d{2}\b", q): return True
    return False

def hour_bucket_requested(query: str) -> bool:
    q = query.strip()
    if second_requested(q) or minute_requested(q):
        return False
    return bool(re.search(r"(\d{1,2})\s*시", q))

def requested_granularity(query: str) -> Optional[str]:
    # 우선순위: 초 > 분 > 시
    if second_requested(query): return "second"
    if minute_requested(query) or ("분의" in query): return "minute"
    if hour_bucket_requested(query): return "hour"
    return None

def get_time_range_from_query(query: str):
    q = query.strip()
    m = re.search(r"(.*?)부터\s+(.*?)까지", q)
    if m:
        s1, s2 = m.group(1), m.group(2)
        dts = extract_datetime_strings(s1) + extract_datetime_strings(s2)
        if len(dts) >= 2:
            start, end = parse_dt(dts[0]), parse_dt(dts[1])
            if start and end and start < end: return start, end
    m = re.search(r"(.*?)~(.*)", q)
    if m:
        s1, s2 = m.group(1), m.group(2)
        dts = extract_datetime_strings(s1) + extract_datetime_strings(s2)
        if len(dts) >= 2:
            start, end = parse_dt(dts[0]), parse_dt(dts[1])
            if start and end and start < end: return start, end
    m = re.search(r"between\s+(.*?)\s+(?:and|to)\s+(.*)", q, flags=re.I)
    if m:
        s1, s2 = m.group(1), m.group(2)
        dts = extract_datetime_strings(s1) + extract_datetime_strings(s2)
        if len(dts) >= 2:
            start, end = parse_dt(dts[0]), parse_dt(dts[1])
            if start and end and start < end: return start, end
    return None, None

def get_duration_range_from_query(query: str):
    if "부터" not in query: return None, None, None
    start_dt = None
    for ds in extract_datetime_strings(query):
        dt = parse_dt(ds)
        if dt: start_dt = dt; break
    if not start_dt: return None, None, None
    after = query.split("부터", 1)[1]
    m_min = re.search(r"(\d+)\s*분", after)
    if m_min:
        minutes = int(m_min.group(1))
        if minutes > 0:
            end_dt = start_dt + timedelta(minutes=minutes) - timedelta(seconds=1)
            return start_dt, end_dt, minutes
    m_hr = re.search(r"(\d+)\s*(?:시간|hour|hours)", after, flags=re.I)
    if m_hr:
        hours = int(m_hr.group(1))
        if hours > 0:
            minutes = hours * 60
            end_dt = start_dt + timedelta(hours=hours) - timedelta(seconds=1)
            return start_dt, end_dt, minutes
    m_day = re.search(r"(\d+)\s*(?:일|day|days)", after, flags=re.I)
    if m_day:
        days = int(m_day.group(1))
        if days > 0:
            minutes = days * 24 * 60
            end_dt = start_dt + timedelta(days=days) - timedelta(seconds=1)
            return start_dt, end_dt, minutes
    return None, None, None

def get_minute_to_minute_range(query: str):
    base = None
    for ds in extract_datetime_strings(query):
        dt = parse_dt(ds)
        if dt: base = dt; break
    if not base: return None, None
    m = re.search(r"(\d{1,2})\s*분부터\s*(\d{1,2})\s*분까지", query)
    if m:
        start_min = int(m.group(1)); end_min = int(m.group(2))
        if 0 <= start_min <= 59 and 0 <= end_min <= 59 and end_min > start_min:
            start_dt = base.replace(minute=start_min, second=0)
            end_dt   = base.replace(minute=end_min, second=0) - timedelta(seconds=1)
            return start_dt, end_dt
    m2 = re.search(r"분부터\s*(\d{1,2})\s*분까지", query)
    if m2:
        end_min = int(m2.group(1)); start_min = base.minute
        if 0 <= start_min <= 59 and 0 <= end_min <= 59 and end_min > start_min:
            start_dt = base.replace(minute=start_min, second=0)
            end_dt   = base.replace(minute=end_min, second=0) - timedelta(seconds=1)
            return start_dt, end_dt
    return None, None

# ===== 파일명에서 시간 추출 =====
def parse_time_from_key(key: str):
    """
    파일명/경로에서 시간 단서를 찾아 datetime(naive KST)로 반환.
    우선순위: YYYYMMDD_HHMM > YYYYMMDDHH > YYYY-MM-DDTHH:MM > YYYY-MM-DD
    반환: (dt, granularity)  # granularity in {"minute","hour","day",None}
    """
    base = key.lower()

    # 20250808_1518
    m = re.search(r"(\d{4})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})", base)
    if m:
        y, mo, d, hh, mm = map(int, m.groups())
        # hourtrend나 houravg 경로면 시간 단위로 강제 설정
        if "hourtrend" in base or "houravg" in base:
            return datetime(y, mo, d, hh, 0), "hour"
        return datetime(y, mo, d, hh, mm), "minute"

    # 2025080815 (hour)
    m = re.search(r"(\d{4})(\d{2})(\d{2})(\d{2})(?!\d)", base)
    if m:
        y, mo, d, hh = map(int, m.groups())
        return datetime(y, mo, d, hh, 0), "hour"

    # 2025-08-08T15:18 or 2025-08-08T15
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})t(\d{2})(?::(\d{2}))?", base)
    if m:
        y, mo, d, hh, mm = m.groups()
        y, mo, d, hh = int(y), int(mo), int(d), int(hh)
        mm = int(mm) if mm else 0
        return datetime(y, mo, d, hh, mm), ("minute" if mm else "hour")

    # 2025-08-08 (day)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})(?![\d:])", base)
    if m:
        y, mo, d = map(int, m.groups())
        return datetime(y, mo, d), "day"

    return None, None

# ===== 스코어링 =====
def score_doc(query: str, text: str, key: str = "") -> int:
    text_l = text.lower()
    q_tokens = normalize_query_tokens(query)
    score = 0

    # 기본 파일 타입 점수 (RAG 모드용)
    if "rawdata" in key.lower():
        score += 10  # 원시 데이터
    elif "minavg" in key.lower() or "mintrend" in key.lower():
        score += 8   # 분 단위 집계
    elif "hourtrend" in key.lower() or "houravg" in key.lower():
        score += 6   # 시간 단위 집계

    for qt in q_tokens:
        if len(qt) >= 2:
            score += text_l.count(qt)

    # 기본 필드 점수 (기존과 동일)
    for k in ["\"temperature\"", "\"humidity\"", "\"gas\"", "\"temp\"", "\"hum\""]:
        if k in text_l:
            score += 1

    dt_strs = extract_datetime_strings(query)
    for ds in dt_strs:
        if ds.lower() in text_l:
            score += 5

    # 파일명-시각 매칭 가산점 (대폭 증가)
    target_dt = None
    for ds in dt_strs:
        dd = parse_dt(ds)
        if dd:
            target_dt = dd
            break
    if key and target_dt:
        key_dt, gran_key = parse_time_from_key(key)
        if key_dt:
            gran_query = requested_granularity(query)

            # 정확한 시각 매칭
            if gran_key == "minute" and (key_dt.year,key_dt.month,key_dt.day,key_dt.hour,key_dt.minute) == \
               (target_dt.year,target_dt.month,target_dt.day,target_dt.hour,target_dt.minute):
                score += 100  # 분 정확 매칭 시 대폭 가산
            elif gran_key == "hour" and (key_dt.year,key_dt.month,key_dt.day,key_dt.hour) == \
                 (target_dt.year,target_dt.month,target_dt.day,target_dt.hour):
                score += 100  # 시 정확 매칭 시 대폭 가산
                if gran_query == "hour":
                    score += 200  # 시간 질의와 시간 파일 매칭 시 추가 보너스
            elif gran_key == "day" and key_dt.date() == target_dt.date():
                score += 50   # 일 매칭 시 가산

    # 연도가 명시되거나 한국어 날짜 패턴이 있는 경우 더 정밀한 데이터 우선순위 적용
    has_year = bool(re.search(r'\b(20\d{2})\b', query))
    has_korean_date = bool(re.search(r'\d{1,2}\s*월\s*\d{1,2}\s*일', query))
    requested_gran = requested_granularity(query)

    # 연도가 있거나 한국어 날짜 패턴이 있는 경우 정밀도 순으로 점수 조정
    if has_year or has_korean_date:
        if "\"timestamp\"" in text_l and ("\"temp\"" in text_l or "\"temperature\"" in text_l):
            score += 25  # raw_list 최우선 (초 단위 데이터)
        elif "\"averages\"" in text_l and ("\"minute\"" in text_l or "\"calculatedAt\"" in text_l):
            score += 18  # minavg 차선 (분 단위 데이터)
        elif "\"averages\"" in text_l and "\"hourly_ranges\"" in text_l:
            score += 8   # houravg 최하위 (시간 단위 데이터)

    elif requested_gran == "second":
        # 초 단위 요청: raw_list를 최우선
        if "\"timestamp\"" in text_l and ("\"temp\"" in text_l or "\"temperature\"" in text_l):
            score += 35  # raw_list 대폭 우대
        elif "rawdata" in key.lower():
            score += 30  # rawdata 경로 대폭 우대
        if "\"averages\"" in text_l:
            score -= 10  # 집계 데이터 대폭 감점
        if "\"hourly_ranges\"" in text_l:
            score -= 15  # houravg 대폭 감점

    elif requested_gran == "minute":
        # 분 단위 요청: minavg를 최우선, 그다음 raw_list
        if "\"averages\"" in text_l and ("\"minute\"" in text_l or "\"timestamp\"" in text_l or "\"calculatedAt\"" in text_l):
            score += 30  # minavg 대폭 우대
        elif "minavg" in key.lower() or "mintrend" in key.lower():
            score += 25  # minavg 경로 대폭 우대
        if "\"timestamp\"" in text_l and ("\"temp\"" in text_l or "\"temperature\"" in text_l):
            score += 15  # raw_list도 우대 (하지만 minavg보다 낮음)
        if "\"hourly_ranges\"" in text_l:
            score -= 10  # houravg 감점

    elif requested_gran == "hour":
        # 시 단위 요청: houravg를 대폭 우선
        hour_bonus_applied = False
        if "\"averages\"" in text_l and "\"hourly_ranges\"" in text_l:
            score += 50  # houravg 대폭 우대
            hour_bonus_applied = True
        elif "\"hourtemp\"" in text_l or "\"hourhum\"" in text_l or "\"hourgas\"" in text_l:
            score += 50  # 시간단위 필드가 있는 파일 대폭 우대
            hour_bonus_applied = True
        elif "hourtrend" in key.lower() or "houravg" in key.lower():
            score += 45  # 파일경로에 hourtrend/houravg가 있으면 대폭 우대
            hour_bonus_applied = True

        # 시간 단위 요청에서는 raw data에 페널티 부여
        if "\"timestamp\"" in text_l and ("\"temp\"" in text_l or "\"temperature\"" in text_l):
            if not hour_bonus_applied:  # houravg 보너스가 없는 경우에만 약간 우대
                score += 5
            else:
                score -= 10  # houravg 파일이 있는 경우 raw data에 페널티
        if "\"averages\"" in text_l and "\"minute\"" in text_l:
            score -= 10   # minavg 대폭 감점

    else:
        # 단위가 명시되지 않은 일반 질의: 균형있게
        if "\"timestamp\"" in text_l and ("\"temp\"" in text_l or "\"temperature\"" in text_l):
            score += 8   # raw_list 우대
        if "\"averages\"" in text_l and ("\"minute\"" in text_l or "\"timestamp\"" in text_l):
            score += 5   # minavg 중간
        if "\"averages\"" in text_l and "\"hourly_ranges\"" in text_l:
            score += 3   # houravg 낮음

    # 디버깅 코드 제거 (is_debug 변수 문제로 인해)

    return score

# ===== JSON 스키마 감지 =====
def detect_schema(obj):
    """
    returns: "raw_list" | "minavg" | "houravg" | "mintrend" | None
    """
    # rawdata: 리스트 형태의 5초 간격 데이터
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        k = set(obj[0].keys())
        if {"timestamp", "temp", "hum", "gas"}.issubset(k): return "raw_list"
        if {"timestamp", "temperature", "humidity", "gas"}.issubset(k): return "raw_list"

    if isinstance(obj, dict):
        # hourtrend: averages와 hourly_ranges, trends를 모두 가진 구조
        if "averages" in obj and "hourly_ranges" in obj and "trends" in obj:
            return "houravg"

        # houravg: 단순한 시간별 평균 (hourtemp, hourhum, hourgas)
        if "hourtemp" in obj and "hourhum" in obj and "hourgas" in obj:
            return "houravg"

        # minavg: 분별 평균 (mintemp, minhum, mingas)
        if "mintemp" in obj and "minhum" in obj and "mingas" in obj:
            return "minavg"

        # mintrend: data 안에 분별 데이터가 있는 구조
        if "data" in obj and isinstance(obj["data"], dict):
            data = obj["data"]
            if "mintemp" in data and "minhum" in data and "mingas" in data:
                return "mintrend"

        # 기존 averages 기반 감지 (백업용)
        if "averages" in obj and ("minute" in obj or "timestamp" in obj or "calculatedAt" in obj):
            return "minavg"

    return None

# ===== S3 다운로드/스코어 (스키마 포함) =====
def download_and_score_file(key: str, query: str):
    try:
        head_resp = s3.head_object(Bucket=S3_BUCKET_DATA, Key=key)
        file_size = head_resp.get('ContentLength', 0)
        if file_size > MAX_FILE_SIZE:
            obj = s3.get_object(Bucket=S3_BUCKET_DATA, Key=key, Range=f"bytes=0-{MAX_FILE_SIZE-1}")
        else:
            obj = s3.get_object(Bucket=S3_BUCKET_DATA, Key=key)
        data = obj["Body"].read()
        txt = data.decode("utf-8", errors="ignore")
        if not txt.strip():
            return None

        schema = None
        j = None
        try:
            # 먼저 전체 텍스트를 JSON으로 파싱 시도
            j = json.loads(txt)
            schema = detect_schema(j)
        except Exception:
            try:
                # 실패하면 JSON이 여러 줄로 되어 있을 수 있으므로 라인별로 파싱
                lines = txt.strip().split('\n')
                if len(lines) == 1:
                    # 한 줄이면 단일 객체
                    j = json.loads(lines[0])
                    schema = detect_schema(j)
                else:
                    # 여러 줄이면 JSON Lines 형태일 가능성
                    json_objects = []
                    for line in lines:
                        line = line.strip()
                        if line:
                            json_objects.append(json.loads(line))
                    if json_objects:
                        if len(json_objects) == 1:
                            j = json_objects[0]
                        else:
                            j = json_objects  # 리스트로 처리
                        schema = detect_schema(j)
            except Exception:
                # 마지막으로 기존 방식 시도
                try:
                    start = txt.find("{"); alt_start = txt.find("[")
                    if alt_start != -1 and (start == -1 or alt_start < start): start = alt_start
                    end = max(txt.rfind("}"), txt.rfind("]"))
                    if start != -1 and end != -1 and end > start:
                        j = json.loads(txt[start:end+1])
                        schema = detect_schema(j)
                except Exception:
                    pass

        sc = score_doc(query, txt, key=key)

        # 간단한 스키마 점수 (RAG 모드용)
        if schema == "raw_list": sc += 5
        elif schema == "minavg": sc += 4
        elif schema == "houravg": sc += 3

        return {"id": key, "content": txt, "score": sc, "file_size": file_size, "schema": schema, "json": j}
    except Exception:
        return None

# ===== 빠른 증거 스니핑 =====
def quick_sensor_evidence(query: str, max_probe: int = 6) -> dict:
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX)

    keys = []
    for page in pages:
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if not k.lower().endswith(".json"):
                continue
            keys.append(k)
            if len(keys) >= MAX_FILES_TO_SCAN:
                break
        if len(keys) >= MAX_FILES_TO_SCAN:
            break
    if not keys:
        return {'has_schema': False, 'best_schema': None, 'best_score': 0}

    scored = []
    with _f.ThreadPoolExecutor(max_workers=min(6, MAX_WORKERS)) as ex:
        futs = {ex.submit(download_and_score_file, k, query): k for k in keys[:max_probe]}
        for f in _f.as_completed(futs):
            r = f.result()
            if r:
                scored.append(r)
    if not scored:
        return {'has_schema': False, 'best_schema': None, 'best_score': 0}

    top = sorted(scored, key=lambda x: x["score"], reverse=True)[0]
    return {
        'has_schema': top.get("schema") in {"raw_list","minavg","houravg"},
        'best_schema': top.get("schema"),
        'best_score': top.get("score", 0)
    }

# ===== LLM 기반 의도 분류 =====
def _build_intent_prompt(query: str) -> str:
    return (
        "You are a router. Classify the user's query domain.\n"
        "Return STRICT JSON: {\"domain\": \"sensor_data\"|\"general\", \"confidence\": 0.0-1.0}.\n"
        "- Choose \"sensor_data\" ONLY if the user is asking about IoT environmental readings "
        "(temperature/humidity/gas/ppm) from my stored device data, with a time window "
        "(특정 날짜/시/분/초, '부터~까지', '최근', '처음/마지막') or stats (평균/최대/최소/추이 등).\n"
        "- Weather forecasts, sports, finance, 일반 상식 등은 \"general\".\n"
        "- IMPORTANT: 한국어 질의에서 '날씨 예보'가 아니라 '내 센서 로그'일 수 있음.\n\n"
        "Examples:\n"
        "Q: 메시의 경기마다 평균 몇 골을 넣어? → {\"domain\":\"general\",\"confidence\":0.95}\n"
        "Q: 내일 서울 날씨 어때? → {\"domain\":\"general\",\"confidence\":0.95}\n"
        "Q: 2025년 8월 8일 16시 온도, 습도, 공기질을 알려줘 → {\"domain\":\"sensor_data\",\"confidence\":0.95}\n"
        "Q: 2025-08-11 10:15:15 습도? → {\"domain\":\"sensor_data\",\"confidence\":0.95}\n"
        "Q: 최근 공기질 평균 보여줘 → {\"domain\":\"sensor_data\",\"confidence\":0.9}\n"
        "Q: esp32s3-airwatch 15:18 온도 평균 → {\"domain\":\"sensor_data\",\"confidence\":0.95}\n\n"
        f"query: {query}\n"
        "json:"
    )

def _invoke_claude(messages, max_tokens=512, temperature=0.0, top_p=0.9, system=None):
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }
    if system:
        body["system"] = system

    resp = bedrock_rt.invoke_model(
        modelId=INFERENCE_PROFILE_ARN,
        accept="application/json",
        contentType="application/json",
        body=json.dumps(body).encode("utf-8"),
    )
    payload = json.loads(resp["body"].read().decode("utf-8", errors="ignore"))
    text = "".join(
        p.get("text", "")
        for p in (payload.get("content") or [])
        if isinstance(p, dict) and p.get("type") == "text"
    ).strip()
    return text, payload

@lru_cache(maxsize=256)
def classify_query_with_llm(query: str) -> dict:
    user_text = _build_intent_prompt(query)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": user_text}]}
    ]
    text, _raw = _invoke_claude(messages, max_tokens=64, temperature=0.0, top_p=0.9)
    try:
        out = json.loads(text)
        dom = out.get("domain", "general")
        conf = float(out.get("confidence", 0.0))
        if dom not in ("sensor_data","general"): dom = "general"
        conf = max(0.0, min(1.0, conf))
        return {"domain": dom, "confidence": conf}
    except Exception:
        return {"domain": "general", "confidence": 0.0}

# ===== 결정적 신호(센서 단어 + 시간/구간 토큰) 가드레일 =====
_TIME_HINTS = ("년", "월", "일", "시", "분", "초", "-", ":", "부터", "까지", "~", "between")
_RANGE_HINTS = ("구간", "최근", "처음", "첫", "마지막", "최종")
def _deterministic_sensor_signal(query: str) -> bool:
    fields = detect_fields_in_query(query)
    if not fields:
        return False
    has_time_literal = bool(extract_datetime_strings(query))
    has_ko_time_tokens = any(tok in query for tok in _TIME_HINTS)
    has_range = any(tok in query for tok in _RANGE_HINTS)
    return has_time_literal or has_ko_time_tokens or has_range

def decide_route(query: str) -> str:
    if _deterministic_sensor_signal(query):
        return "sensor"

    cls = classify_query_with_llm(query)
    dom, conf = cls["domain"], cls["confidence"]

    if dom == "sensor_data" and conf >= 0.6:
        return "sensor"
    if 0.4 <= conf < 0.6:
        ev = quick_sensor_evidence(query)
        if ev["has_schema"] and ev["best_score"] >= RELEVANCE_THRESHOLD:
            return "sensor"
        return "general"
    return "general"

# ===== 검색 =====
def retrieve_documents_from_s3(query: str, limit_chars: int = LIMIT_CONTEXT_CHARS, max_files: int = MAX_FILES_TO_SCAN, top_k: int = TOP_K):
    # 날짜별 prefix 필터링으로 검색 최적화
    dt_strings = extract_datetime_strings(query)
    target_dt = None
    date_prefixes = []

    # 쿼리에서 날짜 추출
    for ds in dt_strings:
        dt = parse_dt(ds)
        if dt:
            target_dt = dt
            date_prefix = dt.strftime('%Y%m%d')  # YYYYMMDD 형식
            date_prefixes.append(date_prefix)
            break

    gran = requested_granularity(query)
    paginator = s3.get_paginator("list_objects_v2")
    priority_keys = []

    # 날짜가 명시된 경우 해당 날짜 폴더만 검색
    if date_prefixes:
        date_prefix = date_prefixes[0]

        if gran == "hour":
            # 시간 질의: 해당 날짜의 hourtrend/houravg만 검색
            for prefix_path in ["hourtrend/", "houravg/"]:
                try:
                    search_prefix = f"{S3_PREFIX}{prefix_path}{date_prefix}/"
                    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=search_prefix, PaginationConfig={'MaxItems': 50})
                    for page in pages:
                        for obj in page.get("Contents", []):
                            k = obj["Key"]
                            if k.lower().endswith(".json"):
                                priority_keys.append(k)
                            if len(priority_keys) >= 30:
                                break
                        if len(priority_keys) >= 30:
                            break
                except Exception:
                    pass
        elif gran == "minute":
            # 분 질의: 해당 날짜의 minavg/mintrend만 검색
            for prefix_path in ["minavg/", "mintrend/"]:
                try:
                    search_prefix = f"{S3_PREFIX}{prefix_path}{date_prefix}/"
                    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=search_prefix, PaginationConfig={'MaxItems': 50})
                    for page in pages:
                        for obj in page.get("Contents", []):
                            k = obj["Key"]
                            if k.lower().endswith(".json"):
                                priority_keys.append(k)
                            if len(priority_keys) >= 30:
                                break
                        if len(priority_keys) >= 30:
                            break
                except Exception:
                    pass

        # 해당 날짜의 rawdata도 검색
        try:
            search_prefix = f"{S3_PREFIX}rawdata/{date_prefix}/"
            pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=search_prefix, PaginationConfig={'MaxItems': 100})
            for page in pages:
                for obj in page.get("Contents", []):
                    k = obj["Key"]
                    if k.lower().endswith(".json"):
                        priority_keys.append(k)
                    if len(priority_keys) >= 100:
                        break
                if len(priority_keys) >= 100:
                    break
        except Exception:
            pass

        # 날짜별 검색으로 충분한 결과가 있으면 전체 검색 생략
        if len(priority_keys) >= 50:
            keys = priority_keys[:max_files]
        else:
            # 추가 검색 필요시만 제한적 전체 검색
            keys = priority_keys
            pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX, PaginationConfig={'MaxItems': max_files//2})
    else:
        # 날짜가 명시되지 않은 경우 기존 방식
        if gran == "hour":
            for prefix_path in ["hourtrend/", "houravg/"]:
                try:
                    search_prefix = S3_PREFIX + prefix_path
                    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=search_prefix, PaginationConfig={'MaxItems': 50})
                    for page in pages:
                        for obj in page.get("Contents", []):
                            k = obj["Key"]
                            if k.lower().endswith(".json"):
                                priority_keys.append(k)
                            if len(priority_keys) >= 50:
                                break
                        if len(priority_keys) >= 50:
                            break
                except Exception:
                    pass
        elif gran == "minute":
            for prefix_path in ["minavg/", "mintrend/"]:
                try:
                    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX + prefix_path, PaginationConfig={'MaxItems': 50})
                    for page in pages:
                        for obj in page.get("Contents", []):
                            k = obj["Key"]
                            if k.lower().endswith(".json"):
                                priority_keys.append(k)
                            if len(priority_keys) >= 50:
                                break
                        if len(priority_keys) >= 50:
                            break
                except Exception:
                    pass

        keys = priority_keys
        # 제한적 전체 검색
        pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX, PaginationConfig={'MaxItems': max_files})
    for page in pages:
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if not k.lower().endswith(".json"):
                continue
            keys.append(k)
            if len(keys) >= max_files:
                break
        if len(keys) >= max_files:
            break

    # 우선 키들을 앞에 배치
    all_keys = list(set(priority_keys + keys))[:max_files]

    if not all_keys: return [], ""

    scored = []
    with _f.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_key = {executor.submit(download_and_score_file, key, query): key for key in all_keys}
        for future in _f.as_completed(future_to_key):
            result = future.result()
            if result: scored.append(result)

    if not scored: return [], ""

    top = sorted(scored, key=lambda x: x["score"], reverse=True)[:top_k]

    # 컨텍스트(LLM 백업용)
    parts, context_length = [], 0
    for idx, d in enumerate(top, start=1):
        tag = f"D{idx}"; d["tag"] = tag
        remaining_space = limit_chars - context_length - 200
        if remaining_space <= 0: break
        content = d["content"]
        if len(content) > remaining_space:
            content = content[:remaining_space] + "\n[문서가 길어 일부만 표시됩니다...]"
        part = f"[{tag}] (s3://{S3_BUCKET_DATA}/{d['id']})\n{content}\n"
        parts.append(part)
        context_length += len(part)

    context = "\n---\n".join(parts).strip()
    return top, context

# ===== 통계/추이/윈도우 유틸 =====
def select_rows_in_range(rows, start_dt, end_dt):
    return [r for r in rows if start_dt <= r["timestamp"] <= end_dt]

def select_rows_in_minute(rows, dt_minute: datetime):
    m_start = dt_minute.replace(second=0)
    m_end = m_start + timedelta(minutes=1) - timedelta(seconds=1)
    return [r for r in rows if m_start <= r["timestamp"] <= m_end], m_start, m_end

def select_rows_in_hour(rows, dt_hour: datetime):
    h_start = dt_hour.replace(minute=0, second=0)
    h_end = h_start + timedelta(hours=1) - timedelta(seconds=1)
    return [r for r in rows if h_start <= r["timestamp"] <= h_end], h_start, h_end

def select_rows_in_day(rows, dt_day: datetime):
    d_start = dt_day.replace(hour=0, minute=0, second=0)
    d_end = d_start + timedelta(days=1) - timedelta(seconds=1)
    return [r for r in rows if d_start <= r["timestamp"] <= d_end], d_start, d_end

def compute_stats(rows):
    if not rows: return None
    keys = set().union(*[set(r.keys()) for r in rows]) - {"timestamp"}
    out = {}
    for k in ["temperature","humidity","gas"]:
        if k in keys:
            arr = [r[k] for r in rows if k in r]
            if arr:
                out[k] = {"avg": sum(arr)/len(arr), "min": min(arr), "max": max(arr), "first": arr[0], "last": arr[-1]}
    return out

def compare_trend(curr_stat, prev_stat):
    def diff_pct(a, b):
        if b is None or a is None or b == 0: return None
        return (a - b) / b * 100.0
    out = {}
    for field in ["temperature", "humidity", "gas"]:
        cs = curr_stat.get(field) if curr_stat else None
        ps = prev_stat.get(field) if prev_stat else None
        if not cs or not ps: out[field] = None; continue
        base_curr = cs.get("avg") if cs.get("avg") is not None else cs.get("last")
        base_prev = ps.get("avg") if ps.get("avg") is not None else ps.get("last")
        if base_curr is None or base_prev is None:
            out[field] = None; continue
        delta = base_curr - base_prev
        pct = diff_pct(base_curr, base_prev)
        out[field] = {"delta": delta, "pct": pct}
    return out

def fmt_trend_line(field_kor, stat, trend):
    if not stat: return f"{field_kor}: 데이터 없음"
    avg_s = f"평균 {stat['avg']:.3f}, 범위 [{stat['min']:.3f}~{stat['max']:.3f}]"
    if not trend: return f"{field_kor}: {avg_s}"
    delta = trend["delta"]; pct = trend["pct"]
    if delta is None: return f"{field_kor}: {avg_s}"
    dir_word = "증가" if delta > 0 else ("감소" if delta < 0 else "변화 없음")
    pct_s = f"{pct:+.2f}%" if pct is not None else "N/A"
    return f"{field_kor}: {avg_s} | 직전 구간 대비 {dir_word} ({delta:+.3f}, {pct_s})"

def filter_fields(row: dict, need_fields: set):
    if not need_fields:
        return {k: row[k] for k in ["temperature","humidity","gas"] if k in row}
    return {f: row[f] for f in need_fields if f in row}

def format_point_answer(values: dict, ts: datetime, tag="D1"):
    parts = []
    for k in ["temperature", "humidity", "gas"]:
        if k in values:
            name = FIELD_NAME_KOR.get(k, k)
            value = values[k]
            comment = get_friendly_comment(k, value)
            parts.append(f"{name} **{value}** {comment}")

    if not parts:
        body = "데이터가 없습니다."
    elif len(parts) == 1:
        body = f"해당 시점의 {parts[0]}"
    else:
        body = f"📊 **정확한 시점 데이터**\n" + "\n".join([f"• {part}" for part in parts])

    return f"{ts.strftime('%Y-%m-%d %H:%M:%S')} 기준:\n{body} [{tag}]"

def format_window_answer(rows_in_window, w_start, w_end, need_fields, tag="D1", window_name="구간", show_samples=True):
    fields = list(need_fields) if need_fields else [k for k in ["temperature","humidity","gas"] if any(k in r for r in rows_in_window)]
    name_map = FIELD_NAME_KOR
    lines = [f"[{window_name}] {w_start.strftime('%Y-%m-%d %H:%M:%S')} ~ {w_end.strftime('%Y-%m-%d %H:%M:%S')}"]
    for f in fields:
        arr = [r[f] for r in rows_in_window if f in r]
        if arr:
            a = sum(arr)/len(arr)
            lines.append(f"{name_map.get(f,f)} 평균: {a:.3f}")
        else:
            lines.append(f"{name_map.get(f,f)} 평균: 데이터 없음")
    if show_samples:
        lines.append(f"[{window_name} 데이터 {len(rows_in_window)}개]")
        for r in rows_in_window:
            parts = []
            if "temperature" in fields and "temperature" in r: parts.append(f"T={r['temperature']}")
            if "humidity" in fields and "humidity" in r:    parts.append(f"H={r['humidity']}")
            if "gas" in fields and "gas" in r:               parts.append(f"CO2={r['gas']}")
            lines.append(f"{r['timestamp'].strftime('%Y-%m-%d %H:%M:%S')} | " + ", ".join(parts))
    else:
        lines.append(f"(샘플 {len(rows_in_window)}개는 생략됨 — '상세' 또는 '원본'이라고 물으면 전부 보여줄게)")
    return "\n".join(lines) + f" [{tag}]"

# ===== RAW 변환 =====
def _load_raw_rows(j):
    rows = []
    # rawdata: 리스트 형태
    if isinstance(j, list):
        for i, r in enumerate(j):
            try:
                ts = parse_dt(str(r["timestamp"]))
                if not ts: continue
                temperature = float(r["temperature"]) if "temperature" in r else float(r["temp"])
                humidity    = float(r["humidity"]) if "humidity" in r else float(r["hum"])
                gas         = float(r["gas"])
                rows.append({"timestamp": ts, "temperature": temperature, "humidity": humidity, "gas": gas})
            except Exception:
                continue
    # 단일 항목 데이터들을 행으로 변환
    elif isinstance(j, dict):
        try:
            # houravg 형태 (hourtemp, hourhum, hourgas)
            if "hourtemp" in j:
                ts = parse_dt(str(j["timestamp"]))
                if ts:
                    rows.append({
                        "timestamp": ts,
                        "temperature": float(j["hourtemp"]),
                        "humidity": float(j["hourhum"]),
                        "gas": float(j["hourgas"])
                    })
            # minavg 형태 (mintemp, minhum, mingas)
            elif "mintemp" in j:
                ts = parse_dt(str(j["timestamp"]))
                if ts:
                    rows.append({
                        "timestamp": ts,
                        "temperature": float(j["mintemp"]),
                        "humidity": float(j["minhum"]),
                        "gas": float(j["mingas"])
                    })
            # mintrend 형태 (data 안에 있음)
            elif "data" in j and "mintemp" in j["data"]:
                data = j["data"]
                ts = parse_dt(str(data["timestamp"]))
                if ts:
                    rows.append({
                        "timestamp": ts,
                        "temperature": float(data["mintemp"]),
                        "humidity": float(data["minhum"]),
                        "gas": float(data["mingas"])
                    })
        except Exception:
            pass

    rows.sort(key=lambda x: x["timestamp"])
    return rows

# ====== 마지막 센서 질의 컨텍스트 ======
LAST_SENSOR_CTX: Dict[str, object] = {
    "window": None,  # "second" | "minute" | "hour" | "range" | None
    "start": None,   # datetime
    "end": None,     # datetime
    "rows": None,    # List[dict] (RAW rows)
    "tag": None,     # "D1" 등
    "label": None    # "해당 분" 등
}

def _reset_last_ctx():
    LAST_SENSOR_CTX.update({"window": None, "start": None, "end": None, "rows": None, "tag": None, "label": None})

def _set_last_ctx(window: str, start: datetime, end: datetime, rows: List[dict], tag: str, label: str):
    LAST_SENSOR_CTX["window"] = window
    LAST_SENSOR_CTX["start"] = start
    LAST_SENSOR_CTX["end"] = end
    LAST_SENSOR_CTX["rows"] = rows
    LAST_SENSOR_CTX["tag"] = tag
    LAST_SENSOR_CTX["label"] = label

def _format_full_rows(rows: List[dict], start: datetime, end: datetime, tag: str, label: str) -> str:
    lines = [f"[{label} 상세] {start.strftime('%Y-%m-%d %H:%M:%S')} ~ {end.strftime('%Y-%m-%d %H:%M:%S')} | 샘플 {len(rows)}개"]
    for r in rows:
        t = r["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        parts = []
        if "temperature" in r: parts.append(f"T={r['temperature']}")
        if "humidity" in r:    parts.append(f"H={r['humidity']}")
        if "gas" in r:         parts.append(f"CO2={r['gas']}")
        lines.append(f"{t} | " + ", ".join(parts))
    return "\n".join(lines) + f" [{tag}]"

# ---- RAW 전체 재수집/정확 매칭 ----
def fetch_raw_rows_for_window_all(start: datetime, end: datetime, max_files: int = MAX_FILES_TO_SCAN) -> Tuple[List[dict], Optional[str]]:
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX)

    keys = []
    for page in pages:
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if not k.lower().endswith(".json"):
                continue
            keys.append(k)
            if len(keys) >= max_files:
                break
        if len(keys) >= max_files:
            break

    all_rows = []
    raw_tag = None
    with _f.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(download_and_score_file, k, f"{start}~{end}"): k for k in keys}
        for f in _f.as_completed(futs):
            r = f.result()
            if not r:
                continue

            # 모든 데이터 타입을 허용
            schema = r.get("schema")
            file_path = r.get("id", "").lower()

            if schema not in ["raw_list", "houravg", "minavg", "mintrend", None]:
                continue

            # rawdata, houravg, minavg, mintrend 파일들은 모두 처리 대상
            if not any(pattern in file_path for pattern in ["rawdata", "houravg", "minavg", "mintrend"]) and schema is None:
                continue
            rows = _load_raw_rows(r.get("json") or [])
            if not rows:
                continue
            subset = select_rows_in_range(rows, start, end)
            if subset:
                all_rows.extend(subset)
                if raw_tag is None:
                    raw_tag = "D?"
    all_rows.sort(key=lambda x: x["timestamp"])
    return all_rows, raw_tag

def fetch_raw_exact_second_all(target_dt: datetime, max_files: int = MAX_FILES_TO_SCAN) -> Tuple[Optional[dict], Optional[str]]:
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX)
    with _f.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = []
        scanned = 0
        for page in pages:
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if not k.lower().endswith(".json"):
                    continue
                futures.append(ex.submit(download_and_score_file, k, str(target_dt)))
                scanned += 1
                if scanned >= max_files:
                    break
            if scanned >= max_files:
                break
        for f in _f.as_completed(futures):
            r = f.result()
            if not r or r.get("schema") != "raw_list":
                continue
            rows = _load_raw_rows(r.get("json") or [])
            for row in rows:
                if row["timestamp"] == target_dt:
                    return row, "D?"
    return None, None

def show_last_detail_if_any(query: str) -> Optional[str]:
    if not want_detail_list(query):
        return None
    if not LAST_SENSOR_CTX.get("start") or not LAST_SENSOR_CTX.get("end"):
        return None
    if not LAST_SENSOR_CTX.get("rows"):
        rows, raw_tag = fetch_raw_rows_for_window_all(LAST_SENSOR_CTX["start"], LAST_SENSOR_CTX["end"])
        if rows:
            _set_last_ctx(
                window=LAST_SENSOR_CTX.get("window") or "range",
                start=LAST_SENSOR_CTX["start"],
                end=LAST_SENSOR_CTX["end"],
                rows=rows,
                tag=raw_tag or LAST_SENSOR_CTX.get("tag") or "D?",
                label=LAST_SENSOR_CTX.get("label") or "요청 구간",
            )
        else:
            return "(최근 센서 구간의 원본 샘플을 찾지 못했어요. 시간/구간이 포함된 센서 질문을 먼저 해주세요.)"

    return _format_full_rows(
        rows=LAST_SENSOR_CTX["rows"],
        start=LAST_SENSOR_CTX["start"],
        end=LAST_SENSOR_CTX["end"],
        tag=LAST_SENSOR_CTX["tag"] or "D?",
        label=LAST_SENSOR_CTX["label"] or "요청 구간"
    )

def _collect_raw_rows_for_window(top_docs, start: datetime, end: datetime) -> Tuple[List[dict], Optional[str]]:
    all_rows = []
    tag = None
    for d in top_docs:
        # 모든 데이터 타입을 허용 (raw_list, houravg, minavg, mintrend, null)
        schema = d.get("schema")
        file_path = d.get("id", "").lower()

        # 데이터가 있을 가능성이 있는 파일들을 모두 시도
        if schema not in ["raw_list", "houravg", "minavg", "mintrend", None]:
            continue

        # rawdata, houravg, minavg, mintrend 파일들은 모두 처리 대상
        if not any(pattern in file_path for pattern in ["rawdata", "houravg", "minavg", "mintrend"]) and schema is None:
            continue
        rows = _load_raw_rows(d.get("json") or [])
        if not rows:
            continue
        subset = select_rows_in_range(rows, start, end)
        if subset:
            all_rows.extend(subset)
            if tag is None: tag = d.get("tag", "D?")
    all_rows.sort(key=lambda x: x["timestamp"])
    return all_rows, tag

# ===== 보조: 파일 탐색 (정확 매칭) =====
def find_houravg_doc_for_hour(target_dt: datetime, max_scan: int = MAX_FILES_TO_SCAN):
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX)

    scanned = 0
    for page in pages:
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if not k.lower().endswith(".json"):
                continue
            key_dt, gran = parse_time_from_key(k)
            if gran == "hour" and key_dt and \
               (key_dt.year, key_dt.month, key_dt.day, key_dt.hour) == \
               (target_dt.year, target_dt.month, target_dt.day, target_dt.hour):
                d = download_and_score_file(k, f"{target_dt}")
                if d and d.get("schema") == "houravg":
                    d["tag"] = d.get("tag","D?")
                    return d
            scanned += 1
            if scanned >= max_scan:
                break
        if scanned >= max_scan:
            break
    return None

def find_minavg_doc_for_minute(target_dt: datetime, max_scan: int = MAX_FILES_TO_SCAN):
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=S3_BUCKET_DATA, Prefix=S3_PREFIX)
    scanned = 0
    for page in pages:
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if not k.lower().endswith(".json"):
                continue
            key_dt, gran = parse_time_from_key(k)
            if gran == "minute" and key_dt and \
               (key_dt.year, key_dt.month, key_dt.day, key_dt.hour, key_dt.minute) == \
               (target_dt.year, target_dt.month, target_dt.day, target_dt.hour, target_dt.minute):
                d = download_and_score_file(k, f"{target_dt}")
                if d and d.get("schema") == "minavg":
                    d["tag"] = d.get("tag","D?")
                    return d
            scanned += 1
            if scanned >= max_scan:
                break
        if scanned >= max_scan:
            break
    return None

# ===== 형식화 유틸 (houravg 출력) =====
def format_houravg_answer_from_doc(d, need_fields: set) -> str:
    tag = d.get("tag","D?")
    j = d.get("json") or {}
    av = j.get("averages", {}) or {}
    ranges = j.get("hourly_ranges", {}) or {}
    trends = j.get("trends", {}) or {}

    av_std = {"temperature": av.get("temp"),
              "humidity": av.get("hum"),
              "gas": av.get("gas")}
    rng_std = {
        "temperature": (ranges.get("temp") or {}),
        "humidity":    (ranges.get("hum") or {}),
        "gas":         (ranges.get("gas") or {}),
    }
    tr_std  = {
        "temperature": trends.get("temperature"),
        "humidity":    trends.get("humidity"),
        "gas":         trends.get("gas"),
    }

    fields = list(need_fields) if need_fields else ["temperature","humidity","gas"]

    if len(fields) == 1:
        f = fields[0]
        name = FIELD_NAME_KOR.get(f, f)
        parts = []
        if av_std.get(f) is not None:
            parts.append(f"{name} 평균: {av_std[f]}")
        r = rng_std.get(f) or {}
        if r:
            parts.append(f"{name} 범위: [{r.get('min')}~{r.get('max')}]")
        t = tr_std.get(f)
        if t:
            cr = t.get("change_rate"); st = t.get("status")
            se = f"(시작 {t.get('start_value')}, 끝 {t.get('end_value')})" if t and t.get("start_value") is not None else ""
            parts.append(f"{name} 추세: {st} {cr} {se}".strip())
        return ("\n".join(parts) if parts else f"{name}: 데이터 없음") + f" [{tag}]"

    lines = ["[시간 단위 집계 요약]"]
    for f in fields:
        name = FIELD_NAME_KOR.get(f, f)
        if av_std.get(f) is not None:
            lines.append(f"{name} 평균: {av_std[f]}")
        r = rng_std.get(f) or {}
        if r:
            lines.append(f"{name} 범위: [{r.get('min')}~{r.get('max')}]")
        t = tr_std.get(f)
        if t:
            cr = t.get("change_rate"); st = t.get("status")
            se = f"(시작 {t.get('start_value')}, 끝 {t.get('end_value')})" if t and t.get("start_value") is not None else ""
            lines.append(f"{name} 추세: {st} {cr} {se}".strip())
    overall = trends.get("overall")
    if overall:
        lines.append(f"전체 추세: {overall}")
    return "\n".join(lines) + f" [{tag}]"

def format_minavg_answer_from_doc(d, need_fields: set) -> str:
    tag = d.get("tag","D?")
    j = d.get("json") or {}

    # 실제 minavg 데이터 구조에 맞게 파싱
    av_std = {"temperature": j.get("mintemp"),
              "humidity": j.get("minhum"),
              "gas": j.get("mingas")}

    fields = list(need_fields) if need_fields else ["temperature","humidity","gas"]

    if len(fields) == 1:
        f = fields[0]
        name = FIELD_NAME_KOR.get(f, f)
        value = av_std.get(f)
        if value is not None:
            comment = get_friendly_comment(f, value)
            return f"해당 분의 {name}는 평균 **{value}**입니다. {comment} [{tag}]"
        else:
            return f"해당 분의 {name} 데이터가 없습니다. [{tag}]"

    lines = ["**분 단위 환경 상태**"]
    for f in fields:
        name = FIELD_NAME_KOR.get(f, f)
        value = av_std.get(f)
        if value is not None:
            comment = get_friendly_comment(f, value)
            lines.append(f"• {name}: **{value}** {comment}")
        else:
            lines.append(f"• {name}: 데이터 없음")
    return "\n".join(lines) + f" [{tag}]"

def get_friendly_comment(field: str, value: float) -> str:
    """필드값에 따른 친절한 설명 추가"""
    if field == "temperature":
        if value < 18:
            return "다소 춥네요. 냉방병 걸리기 쉬운 온도에요!"
        elif value < 22:
            return "시원하고 쾌적해요. 이대로 유지하면 좋겠어요!"
        elif value < 26:
            return "적정 온도로 편안해요. 이대로 유지하면 좋겠어요!"
        elif value < 30:
            return "조금 덥네요. 에어컨을 트는 것이 좋겠어요!"
        else:
            return "많이 더워요. 주의가 필요해요!"
    elif field == "humidity":
        if value < 30:
            return "건조해요. 습도를 올리면 좋겠어요!"
        elif value < 50:
            return "쾌적한 습도예요. 이대로면 좋겠어요!"
        elif value < 60:
            return "적정 습도로 좋아요. 이대로도 괜찮아요!"
        elif value < 70:
            return "조금 습해요. 제습기를 돌리면 좋겠어요!"
        else:
            return "습도가 많이 높아요!"
    elif field == "gas":
        if value < 400:
            return "공기가 매우 깨끗해요"
        elif value < 600:
            return "공기 상태가 좋아요"
        elif value < 1000:
            return "보통 수준이에요"
        elif value < 1500:
            return "환기가 필요해요!"
        else:
            return "환기가 필요해요!"
    return ""

# ===== 정확 모드 =====
def find_sensor_data_from_s3_logs(query: str) -> Optional[Dict]:
    """
    S3 로그 데이터에서 해당 시간의 센서 데이터를 찾는 함수
    """
    # 요청된 시간 추출
    target_dt = None
    dt_strings = extract_datetime_strings(query)
    for ds in dt_strings:
        dt = parse_dt(ds)
        if dt:
            target_dt = dt
            break

    if not target_dt:
        return None

    try:
        # S3에서 로그 파일 목록 조회 (최근 1000개)
        prefix = f"{CHATLOG_PREFIX}{SESSION_ID}/"
        response = s3_logs.list_objects_v2(Bucket=CHATLOG_BUCKET, Prefix=prefix, MaxKeys=1000)

        if 'Contents' not in response:
            return None

        # 각 로그 파일을 확인해서 해당 시간의 센서 데이터 찾기
        for obj in response['Contents']:
            try:
                log_response = s3_logs.get_object(Bucket=CHATLOG_BUCKET, Key=obj['Key'])
                log_data = json.loads(log_response['Body'].read().decode('utf-8'))

                # sensor_data 필드가 있는지 확인
                sensor_data_list = log_data.get('sensor_data', [])
                if not sensor_data_list:
                    continue

                # 해당 시간과 일치하는 센서 데이터 찾기
                for sensor_entry in sensor_data_list:
                    data = sensor_entry.get('data')
                    if not data:
                        continue

                    schema = sensor_entry.get('schema')
                    if schema == 'raw_list' and isinstance(data, list):
                        # raw_list에서 정확한 시간 찾기
                        for row in data:
                            row_time = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
                            if row_time == target_dt:
                                return {
                                    'timestamp': row['timestamp'],
                                    'temperature': row.get('temperature'),
                                    'humidity': row.get('humidity'),
                                    'gas': row.get('gas'),
                                    'source': 's3_log',
                                    'log_key': obj['Key']
                                }

                    elif schema in ['minavg', 'houravg'] and isinstance(data, dict):
                        # 집계 데이터에서 시간 단위별 매칭
                        data_time = datetime.strptime(data['timestamp'], '%Y-%m-%d %H:%M:%S')

                        # 분 단위 비교 (minavg) 또는 시간 단위 비교 (houravg)
                        if schema == 'minavg' and data_time.replace(second=0) == target_dt.replace(second=0):
                            return {
                                'timestamp': data['timestamp'],
                                'temperature': data.get('temperature'),
                                'humidity': data.get('humidity'),
                                'gas': data.get('gas'),
                                'source': 's3_log',
                                'schema': schema,
                                'log_key': obj['Key']
                            }
                        elif schema == 'houravg' and data_time.replace(minute=0, second=0) == target_dt.replace(minute=0, second=0):
                            return {
                                'timestamp': data['timestamp'],
                                'temperature': data.get('temperature'),
                                'humidity': data.get('humidity'),
                                'gas': data.get('gas'),
                                'source': 's3_log',
                                'schema': schema,
                                'log_key': obj['Key']
                            }

            except Exception:
                continue  # 해당 로그 파일 처리 실패시 다음으로

        return None

    except Exception as e:
        print(f"[오류] S3 로그 조회 중 오류: {e}")
        return None

def maybe_answer_from_sensor_json(query: str, top_docs):
    if not top_docs: return None

    ql = query.lower()
    want_avg = ("평균" in query) or ("average" in ql)
    want_max = ("최대" in query) or ("max" in ql)
    want_min = ("최소" in query) or ("min" in ql)
    want_latest = ("최근" in query) or ("latest" in ql)
    want_first = any(k in query for k in ["처음", "첫", "첫번째"]) or ("first" in ql) or ("start" in ql)
    want_last  = ("마지막" in query) or ("끝" in query) or ("최종" in ql) or ("last" in ql)
    want_trend = ("추이" in query) or ("trend" in ql) or ("증감" in ql) or ("변화" in ql)
    want_minute_of = ("분의" in query)

    need_fields = detect_fields_in_query(query)

    # 파싱된 단일 시점
    target_dt = None
    for ds in extract_datetime_strings(query):
        dt = parse_dt(ds)
        if dt: target_dt = dt; break

    # --- 정확 매칭 전용 처리: 초/분/시 ---
    gran = requested_granularity(query)

    # (A) 초 단위: 정확히 동일한 샘플만 허용
    if gran == "second" and target_dt is not None:
        for d in top_docs:
            if d.get("schema") != "raw_list":
                continue
            rows = _load_raw_rows(d.get("json") or [])
            for row in rows:
                if row["timestamp"] == target_dt:
                    sel = filter_fields(row, need_fields)
                    m_rows, m_start, m_end = select_rows_in_minute(rows, target_dt)
                    _set_last_ctx(window="minute", start=m_start, end=m_end, rows=m_rows, tag=d.get("tag","D?"), label="해당 분")
                    return format_point_answer(sel, target_dt, tag=d.get("tag","D?"))
        row, raw_tag = fetch_raw_exact_second_all(target_dt)
        if row:
            sel = filter_fields(row, need_fields)
            _set_last_ctx(window="second", start=target_dt, end=target_dt, rows=[row], tag=raw_tag or "D?", label="해당 초")
            return format_point_answer(sel, target_dt, tag=raw_tag or "D?")
        return f"(요청한 {target_dt.strftime('%Y-%m-%d %H:%M:%S')}의 데이터가 없습니다.)"

    # (B) 분 단위
    if (gran == "minute" or want_minute_of) and target_dt is not None:
        # 먼저 minavg 파일 찾기
        for d in top_docs:
            if d.get("schema") == "minavg":
                key_dt, gran_k = parse_time_from_key(d["id"])
                if gran_k == "minute" and key_dt and \
                   (key_dt.year, key_dt.month, key_dt.day, key_dt.hour, key_dt.minute) == \
                   (target_dt.year, target_dt.month, target_dt.day, target_dt.hour, target_dt.minute):
                    return format_minavg_answer_from_doc(d, need_fields)

        matched = find_minavg_doc_for_minute(target_dt)
        if matched:
            return format_minavg_answer_from_doc(matched, need_fields)

        # minavg 없으면 raw_list에서 해당 분 찾기
        w_start = target_dt.replace(second=0)
        w_end = w_start + timedelta(minutes=1) - timedelta(seconds=1)
        target_filename = f"{target_dt.strftime('%Y%m%d%H%M')}_rawdata.json"

        # 정확히 매칭되는 파일 먼저 찾기
        for d in top_docs:
            if d.get("schema") == "raw_list" and target_filename in d.get("id", ""):
                rows = _load_raw_rows(d.get("json") or [])
                if rows:
                    minute_rows = select_rows_in_range(rows, w_start, w_end)
                    if minute_rows:
                        _set_last_ctx(window="minute", start=w_start, end=w_end, rows=minute_rows, tag=d.get("tag","D?"), label="해당 분")
                        return format_window_answer(minute_rows, w_start, w_end, need_fields, tag=d.get("tag","D?"), window_name="해당 분", show_samples=False)

        # 정확 매칭 실패하면 다른 raw_list 파일들 시도
        for d in top_docs:
            if d.get("schema") == "raw_list":
                rows = _load_raw_rows(d.get("json") or [])
                if rows:
                    minute_rows = select_rows_in_range(rows, w_start, w_end)
                    if minute_rows:
                        _set_last_ctx(window="minute", start=w_start, end=w_end, rows=minute_rows, tag=d.get("tag","D?"), label="해당 분")
                        return format_window_answer(minute_rows, w_start, w_end, need_fields, tag=d.get("tag","D?"), window_name="해당 분", show_samples=False)

        return f"(요청한 {target_dt.strftime('%Y-%m-%d %H:%M')}의 데이터가 없습니다.)"

    # (C) 시 단위: houravg 우선, 없으면 raw 데이터에서 집계
    if gran == "hour" and target_dt is not None:
        matched = None
        for d in top_docs:
            if d.get("schema") != "houravg":
                continue
            key_dt, gran_k = parse_time_from_key(d["id"])
            if gran_k == "hour" and key_dt and \
               (key_dt.year, key_dt.month, key_dt.day, key_dt.hour) == \
               (target_dt.year, target_dt.month, target_dt.day, target_dt.hour):
                matched = d
                break
        if not matched:
            matched = find_houravg_doc_for_hour(target_dt)

        # houravg가 있으면 사용
        if matched:
            return format_houravg_answer_from_doc(matched, need_fields)

        # houravg가 없으면 raw 데이터에서 해당 시간대 집계
        h_start = target_dt.replace(minute=0, second=0, microsecond=0)
        h_end = h_start + timedelta(hours=1) - timedelta(seconds=1)

        # raw_list에서 해당 시간대 데이터 찾기
        for d in top_docs:
            if d.get("schema") == "raw_list":
                rows = _load_raw_rows(d.get("json") or [])
                if rows:
                    hour_rows = select_rows_in_range(rows, h_start, h_end)
                    if hour_rows:
                        _set_last_ctx(window="hour", start=h_start, end=h_end, rows=hour_rows, tag=d.get("tag","D?"), label="해당 시간")
                        return format_window_answer(hour_rows, h_start, h_end, need_fields, tag=d.get("tag","D?"), window_name="해당 시간", show_samples=False)

        return f"(요청한 {target_dt.strftime('%Y-%m-%d %H시')}의 데이터가 없습니다.)"

    # ----- 일반 질의/구간 질의 -----
    range_start, range_end = get_time_range_from_query(query)

    for d in top_docs:
        j = d.get("json")
        if j is None:
            try:
                txt = d["content"]
                start = txt.find("{"); alt_start = txt.find("[")
                if alt_start != -1 and (start == -1 or alt_start < start): start = alt_start
                end = max(txt.rfind("}"), txt.rfind("]"))
                if start != -1 and end != -1 and end > start:
                    j = json.loads(txt[start:end+1])
            except Exception:
                j = None
        schema = detect_schema(j) if j is not None else None

        if schema == "raw_list":
            rows = _load_raw_rows(j)
            if not rows: continue

            # 분→분 구간
            mm_start, mm_end = get_minute_to_minute_range(query)
            if mm_start and mm_end and mm_start < mm_end:
                cur_rows = select_rows_in_range(rows, mm_start, mm_end)
                if not cur_rows: return "(요청한 분→분 구간에 해당하는 데이터가 없습니다.) " + f"[{d.get('tag','D1')}]"
                ans = format_window_answer(
                    cur_rows, mm_start, mm_end, detect_fields_in_query(query),
                    tag=d.get("tag"), window_name="분→분 구간",
                    show_samples=want_detail_list(query)
                )
                _set_last_ctx(window="range", start=mm_start, end=mm_end, rows=cur_rows, tag=d.get("tag","D?"), label="분→분 구간")
                return ans

    return None

# ===== 누락된 함수들 =====

# 전역 변수들
SESSION_ID = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
TURN_ID = 0
HISTORY = []
ENABLE_CHATLOG_SAVE = True
CHATLOG_PREFIX = "chatlogs/"

# 후속 타임스탬프 저장
FOLLOWUP_TIMESTAMP = None

def set_followup_timestamp(dt: datetime):
    global FOLLOWUP_TIMESTAMP
    FOLLOWUP_TIMESTAMP = dt

def expand_followup_query_with_last_window(query: str) -> str:
    """후속 질문 확장 (기본 구현)"""
    return query

def build_prompt(query: str, context: str, history: list = None) -> str:
    """RAG 프롬프트 생성"""
    hist_str = ""
    if history:
        for h in history[-3:]:  # 최근 3개 대화만
            hist_str += f"Q: {h['query']}\nA: {h['answer']}\n\n"
    
    return f"""당신은 스마트홈 IoT 센서 데이터 분석 전문가입니다.

이전 대화:
{hist_str}

관련 센서 데이터:
{context}

사용자 질문: {query}

위 센서 데이터를 바탕으로 정확하고 친절하게 답변해주세요. 온도는 ℃, 습도는 %, CO2는 ppm 단위를 사용하세요."""

def build_general_prompt(query: str, history: list = None) -> str:
    """일반 프롬프트 생성"""
    hist_str = ""
    if history:
        for h in history[-3:]:
            hist_str += f"Q: {h['query']}\nA: {h['answer']}\n\n"
    
    return f"""당신은 도움이 되는 AI 어시스턴트입니다.

이전 대화:
{hist_str}

사용자 질문: {query}

친절하고 정확하게 답변해주세요."""

def generate_answer_with_nova(prompt: str) -> str:
    """LLM을 사용해 답변 생성"""
    try:
        messages = [
            {"role": "user", "content": [{"type": "text", "text": prompt}]}
        ]
        text, _ = _invoke_claude(messages, max_tokens=1024, temperature=0.3)
        return text
    except Exception as e:
        return f"답변 생성 중 오류가 발생했습니다: {str(e)}"

def save_turn_to_s3(session_id: str, turn_id: int, route: str, query: str, answer: str, top_docs: list = None):
    """S3에 대화 로그 저장"""
    try:
        log_data = {
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "route": route,
            "query": query,
            "answer": answer,
            "top_docs": top_docs or []
        }
        
        key = f"{CHATLOG_PREFIX}{session_id}/turn_{turn_id:03d}.json"
        s3_logs.put_object(
            Bucket=CHATLOG_BUCKET,
            Key=key,
            Body=json.dumps(log_data, ensure_ascii=False, indent=2),
            ContentType='application/json'
        )
    except Exception as e:
        print(f"로그 저장 실패: {e}")