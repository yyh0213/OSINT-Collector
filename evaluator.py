# evaluator.py
"""
 OSINT감찰관 모듈 (Source Evaluator)
-------------------------------------------
수집기(collector.py)가 기사를 Qdrant에 적재한 직후,
훅(Hook)을 통해 비동기로 호출됩니다.

역할:
  1. 정보원(Source)의 신뢰도를 SQLite에 누적 기록
  2. 벡터 유사도 기반으로 표절 의심 기사를 자동 색출
  3. LLM을 통해 독창성(Delta) 점수를 수치화
  4. 3진 아웃제로 어뷰저(Copycat) 매체를 자동 블랙리스트 처리
"""

import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, Range, SearchRequest

# ───────────────────────────────────────────────
# 상수 정의
# ───────────────────────────────────────────────
COLLECTION_NAME = "osint_news"

# 표절 판정 기준값
COPYCAT_SIMILARITY_THRESHOLD = 0.90  # 코사인 유사도 임계값
COPYCAT_DELTA_THRESHOLD = 2.0  # 이 점수 미만 → 어뷰저 의심
COPYCAT_STRIKE_LIMIT = 3  # 3진 아웃

# 신뢰 매체 승격 기준값
TRUSTED_MIN_ARTICLES = 10  # 최소 평가 기사 수
TRUSTED_MIN_RICHNESS = 6.0  # 평균 정보 밀도(richness) 기준
TRUSTED_MIN_DELTA = 5.0  # 평균 델타 기준
TRUSTED_MAX_STRIKES = 0  # 스트라이크 없어야 함


# ───────────────────────────────────────────────
# SQLite 스키마
# ───────────────────────────────────────────────
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_reliability (
    source_id           TEXT PRIMARY KEY,
    source_name         TEXT NOT NULL,
    total_articles      INTEGER DEFAULT 0,
    copycat_strikes     INTEGER DEFAULT 0,
    avg_lag_time_mins   INTEGER DEFAULT 0,
    avg_richness_score  REAL    DEFAULT 0.0,
    delta_contribution  REAL    DEFAULT 0.0,
    status              TEXT    DEFAULT 'PROBATION',
    last_evaluated      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


# ───────────────────────────────────────────────
# LLM 분석 프롬프트
# ───────────────────────────────────────────────
_DELTA_SYSTEM_PROMPT = (
    "당신은 저널리즘 독창성 평가 AI입니다. "
    "두 기사를 비교하여 '신규 기사'가 '선행 기사' 대비 추가한 "
    "고유 명사·수치·인용·사건·장소 등 새로운 팩트(Delta)가 얼마나 되는지 "
    "0.0(완전 동일) ~ 10.0(완전히 새로운 내용) 사이의 숫자 하나만 반환하십시오. "
    'JSON 형식으로 {"delta": <float>} 만 출력하십시오.'
)

_RICHNESS_SYSTEM_PROMPT = (
    "당신은 저널리즘 품질 평가 AI입니다. "
    "아래 기사의 정보 밀도(구체적 수치, 인용, 고유명사, 사건 설명 등)를 "
    "0.0(매우 빈약) ~ 10.0(매우 풍부) 사이의 숫자 하나만 반환하십시오. "
    'JSON 형식으로 {"richness": <float>} 만 출력하십시오.'
)


# ───────────────────────────────────────────────
# SourceEvaluator 클래스
# ───────────────────────────────────────────────
class SourceEvaluator:
    """
    정보원감찰관 클래스.

    사용 방법 (collector.py 측):
        evaluator = SourceEvaluator(
            sqlite_db_path="config/reliability.db",
            qdrant_client=client,
            llm_client=httpx_async_client,   # Ollama /api/generate 엔드포인트를 가진 클라이언트
        )
        hook_manager.register("article_inserted", evaluator.on_article_inserted)
    """

    def __init__(
        self,
        sqlite_db_path: str,
        qdrant_client: QdrantClient,
        llm_client,  # httpx.AsyncClient 또는 이에 준하는 객체
        llm_gen_url: str = "",  # Ollama /api/generate URL
        llm_model: str = "llama3",
    ):
        self.db_path = sqlite_db_path
        self.qdrant = qdrant_client
        self.llm = llm_client
        self.llm_gen_url = llm_gen_url
        self.llm_model = llm_model
        self._init_sqlite_db()

    # ── 초기화 ────────────────────────────────
    def _init_sqlite_db(self):
        """SQLite 테이블을 최초 1회 생성합니다."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(_SCHEMA_SQL)
                conn.commit()
            print(f"[감찰관] SQLite DB 준비 완료: {self.db_path}")
        except Exception as e:
            print(f"[감찰관] ⚠️  SQLite 초기화 실패: {e}")

    # ── 퍼블릭 훅 진입점 ─────────────────────
    async def on_article_inserted(self, payload: dict, vector: list):
        """
        수집기(collector.py)가 Qdrant에 기사를 적재한 직후 호출됩니다.

        Parameters
        ----------
        payload : dict
            Qdrant에 저장된 포인트의 payload (title, link, project, category,
            timestamp, content, chunk_info 포함)
        vector : list
            해당 포인트의 임베딩 벡터
        """
        source_name = payload.get("source_name", payload.get("project", "Unknown"))
        source_id = self._make_source_id(source_name)
        timestamp = payload.get("timestamp", int(time.time()))
        content = payload.get("content", "")
        title = payload.get("title", "")

        print(
            f"\n[감찰관] ▶ 감찰 개시 — 매체: '{source_name}' | 기사: '{title[:40]}...'"
        )

        try:
            # ── Step 1: DB 기본 갱신 ──────────────────────────────────────
            await self._step1_upsert_source(source_id, source_name)

            # ── Step 2: 벡터 유사도 검색 (표절 의심군 색출) ───────────────
            prior_article = await self._step2_find_prior_similar(
                vector, timestamp, source_id
            )

            # ── Step 3: LLM 델타 및 풍부함 검증 ──────────────────────────
            delta_score, richness_score, lag_mins = await self._step3_llm_analysis(
                content, title, prior_article, timestamp
            )

            # ── Step 4: 신뢰도 점수 갱신 및 상태 판정 ────────────────────
            await self._step4_update_scores(
                source_id=source_id,
                source_name=source_name,
                delta_score=delta_score,
                richness_score=richness_score,
                lag_mins=lag_mins,
                is_copycat=(
                    prior_article is not None and delta_score < COPYCAT_DELTA_THRESHOLD
                ),
            )

            print(
                f"[감찰관] ✅ 감찰 완료 — delta={delta_score:.1f} | richness={richness_score:.1f} | lag={lag_mins}min"
            )

        except Exception as e:
            # 백그라운드 훅이므로 메인 루프에 절대 예외를 전파하지 않습니다.
            print(f"[감찰관] ❌ 내부 오류 (무시됨): {e}")

    # ── Step 1 ────────────────────────────────
    async def _step1_upsert_source(self, source_id: str, source_name: str):
        """매체가 없으면 신규 등록, 있으면 total_articles +1."""
        print(f"[감찰관] [1/4] 신원 확인 중 — source_id: {source_id}")
        try:
            with sqlite3.connect(self.db_path) as conn:
                # 없으면 INSERT, 있으면 total_articles 증가
                conn.execute(
                    """
                    INSERT INTO source_reliability (source_id, source_name, total_articles)
                    VALUES (?, ?, 1)
                    ON CONFLICT(source_id) DO UPDATE SET
                        total_articles   = total_articles + 1,
                        last_evaluated   = CURRENT_TIMESTAMP
                    """,
                    (source_id, source_name),
                )
                conn.commit()
        except Exception as e:
            print(f"[감찰관]   ⚠️  DB upsert 실패: {e}")
            raise

    # ── Step 2 ────────────────────────────────
    async def _step2_find_prior_similar(
        self, vector: list, current_ts: int, current_source_id: str
    ) -> Optional[dict]:
        """
        Qdrant에서 코사인 유사도 0.90 이상이며,
        현재 기사보다 시간이 앞선(과거의) 기사를 검색합니다.

        Returns
        -------
        dict | None
            유사 선행 기사의 payload. 없으면 None.
        """
        print(
            f"[감찰관] [2/4] 표절 의심군 탐색 중 (임계값: {COPYCAT_SIMILARITY_THRESHOLD})..."
        )
        try:
            # 현재 기사보다 과거에 수집된 기사만 대상
            time_filter = Filter(
                must=[
                    FieldCondition(
                        key="timestamp",
                        range=Range(lt=current_ts),  # strictly before
                    )
                ]
            )

            results = self.qdrant.search(
                collection_name=COLLECTION_NAME,
                query_vector=vector,
                query_filter=time_filter,
                limit=5,
                score_threshold=COPYCAT_SIMILARITY_THRESHOLD,
                with_payload=True,
            )

            if not results:
                print(f"[감찰관]   ✔ 유사 선행 기사 없음 — 독자 보도로 잠정 판정.")
                return None

            # 가장 유사도가 높은 1건만 비교 대상으로 채택
            best = results[0]
            prior_source = best.payload.get("source_name", best.payload.get("project", "Unknown"))
            print(
                f"[감찰관]   ⚠️  유사 선행 기사 발견! "
                f"출처: '{prior_source}' | 유사도: {best.score:.3f}"
            )
            return {"payload": best.payload, "score": best.score}

        except Exception as e:
            print(f"[감찰관]   ⚠️  Qdrant 검색 실패 (표절 검사 스킵): {e}")
            return None

    # ── Step 3 ────────────────────────────────
    async def _step3_llm_analysis(
        self,
        content: str,
        title: str,
        prior_article: Optional[dict],
        current_ts: int,
    ) -> tuple[float, float, int]:
        """
        LLM을 통해 다음 두 가지를 분석합니다.
          - richness_score : 기사 자체의 정보 밀도 (0~10)
          - delta_score    : 선행 기사 대비 새로운 팩트 비중 (0~10, 선행 없으면 10.0)
          - lag_mins       : 선행 기사 대비 보도 지연 (분)

        Returns
        -------
        (delta_score, richness_score, lag_mins)
        """
        print(f"[감찰관] [3/4] LLM 독창성 정밀 분석 중...")

        # ── 3-A. 정보 밀도(Richness) 분석 ──────
        richness_score = await self._llm_score_richness(content)

        # ── 3-B. 델타(Delta) 분석 ───────────────
        if prior_article is None:
            # 선행 기사 없음 → 독자 보도로 최고점 부여
            delta_score = 10.0
            lag_mins = 0
            print(f"[감찰관]   선행 기사 없음 → delta=10.0 (독자 보도 가산점)")
        else:
            prior_payload = prior_article["payload"]
            prior_content = prior_payload.get("content", "")
            prior_ts = prior_payload.get("timestamp", current_ts)

            delta_score = await self._llm_score_delta(content, title, prior_content)
            lag_mins = max(0, (current_ts - prior_ts) // 60)

            verdict = (
                "🔴 어뷰징 의심"
                if delta_score < COPYCAT_DELTA_THRESHOLD
                else "🟢 독창성 인정"
            )
            print(f"[감찰관]   delta={delta_score:.1f} | lag={lag_mins}분 | {verdict}")

        return delta_score, richness_score, lag_mins

    # ── Step 4 ────────────────────────────────
    async def _step4_update_scores(
        self,
        source_id: str,
        source_name: str,
        delta_score: float,
        richness_score: float,
        lag_mins: int,
        is_copycat: bool,
    ):
        """
        SQLite의 평균 점수를 누적 갱신하고,
        어뷰저(Copycat) 판정 시 스트라이크를 부여합니다.
        이후 상태(TRUSTED / PROBATION / BLACKLISTED)를 결정합니다.
        """
        print(f"[감찰관] [4/4] 신뢰도 점수 갱신 중...")
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM source_reliability WHERE source_id = ?",
                    (source_id,),
                ).fetchone()

                if row is None:
                    # Step1에서 insert했으나 race condition 방어
                    return

                cols = [
                    d[0]
                    for d in conn.execute(
                        "SELECT * FROM source_reliability LIMIT 0"
                    ).description
                ]
                rec = dict(zip(cols, row))

                n = rec["total_articles"]  # 이미 +1 된 상태
                old_rich = rec["avg_richness_score"]
                old_delta = rec["delta_contribution"]
                old_lag = rec["avg_lag_time_mins"]
                strikes = rec["copycat_strikes"]

                # 누적 이동 평균 (Cumulative Moving Average)
                new_rich = _cma(old_rich, richness_score, n)
                new_delta = _cma(old_delta, delta_score, n)
                new_lag = int(_cma(old_lag, lag_mins, n))

                if is_copycat:
                    strikes += 1
                    print(
                        f"[감찰관]   🚨 어뷰징 스트라이크 +1 → 누적 {strikes}/{COPYCAT_STRIKE_LIMIT}"
                    )

                # 상태 결정
                new_status = _determine_status(
                    strikes=strikes,
                    total_articles=n,
                    avg_delta=new_delta,
                    avg_richness=new_rich,
                )

                if new_status != rec["status"]:
                    print(
                        f"[감찰관]   📋 상태 변경: [{rec['status']}] → [{new_status}]"
                    )

                conn.execute(
                    """
                    UPDATE source_reliability SET
                        copycat_strikes     = ?,
                        avg_lag_time_mins   = ?,
                        avg_richness_score  = ?,
                        delta_contribution  = ?,
                        status              = ?,
                        last_evaluated      = CURRENT_TIMESTAMP
                    WHERE source_id = ?
                    """,
                    (strikes, new_lag, new_rich, new_delta, new_status, source_id),
                )
                conn.commit()

        except Exception as e:
            print(f"[감찰관]   ⚠️  점수 갱신 실패: {e}")
            raise

    # ── LLM 헬퍼 ──────────────────────────────
    async def _llm_score_delta(
        self, new_content: str, new_title: str, prior_content: str
    ) -> float:
        """선행 기사 대비 신규 기사의 델타 점수를 LLM에 요청합니다."""
        if not self.llm or not self.llm_gen_url:
            print(f"[감찰관]   (Mock) LLM 미설정 → delta=5.0 반환")
            return 5.0

        user_prompt = (
            f"[선행 기사]\n{prior_content[:2000]}\n\n"
            f"[신규 기사 제목] {new_title}\n"
            f"[신규 기사 본문]\n{new_content[:2000]}"
        )
        return await self._call_llm_for_score(
            system_prompt=_DELTA_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            key="delta",
            fallback=5.0,
        )

    async def _llm_score_richness(self, content: str) -> float:
        """기사의 정보 밀도(Richness)를 LLM에 요청합니다."""
        if not self.llm or not self.llm_gen_url:
            print(f"[감찰관]   (Mock) LLM 미설정 → richness=5.0 반환")
            return 5.0

        user_prompt = f"[기사 본문]\n{content[:2000]}"
        return await self._call_llm_for_score(
            system_prompt=_RICHNESS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            key="richness",
            fallback=5.0,
        )

    async def _call_llm_for_score(
        self,
        system_prompt: str,
        user_prompt: str,
        key: str,
        fallback: float = 5.0,
    ) -> float:
        """
        Ollama /api/generate 엔드포인트를 호출하여 JSON 응답에서 숫자 점수를 파싱합니다.
        실패 시 fallback 값을 반환하며 예외를 전파하지 않습니다.
        """
        try:
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            response = await self.llm.post(
                self.llm_gen_url,
                json={
                    "model": self.llm_model,
                    "prompt": full_prompt,
                    "stream": False,
                },
                timeout=45.0,
            )
            raw_text = response.json().get("response", "")
            return _parse_score(raw_text, key, fallback)

        except Exception as e:
            print(f"[감찰관]   ⚠️  LLM 호출 실패 (fallback={fallback}): {e}")
            return fallback

    # ── 유틸리티 ──────────────────────────────
    @staticmethod
    def _make_source_id(source_name: str) -> str:
        """
        'MBC News (Politics)' 같은 문자열을 안전한 source_id로 변환합니다.
        예) "MBC News" → "mbc_news"
        """
        safe = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", source_name).strip("_").lower()
        return safe or "unknown"


# ───────────────────────────────────────────────
# 모듈 레벨 순수 함수 (테스트 용이성 확보)
# ───────────────────────────────────────────────


def _cma(old_avg: float, new_value: float, n: int) -> float:
    """
    Cumulative Moving Average: 기존 평균에 새 값을 누적합니다.
    n은 이미 new_value를 포함한 총 기사 수입니다.
    """
    if n <= 1:
        return float(new_value)
    return old_avg + (new_value - old_avg) / n


def _determine_status(
    strikes: int,
    total_articles: int,
    avg_delta: float,
    avg_richness: float,
) -> str:
    """
    스트라이크·평가지표를 종합하여 매체 상태를 결정합니다.

    Rules:
      - strikes >= 3                               → BLACKLISTED
      - articles >= 10 & delta >= 5.0 & richness >= 6.0 & strikes == 0 → TRUSTED
      - 그 외                                       → PROBATION
    """
    if strikes >= COPYCAT_STRIKE_LIMIT:
        return "BLACKLISTED"

    if (
        total_articles >= TRUSTED_MIN_ARTICLES
        and avg_delta >= TRUSTED_MIN_DELTA
        and avg_richness >= TRUSTED_MIN_RICHNESS
        and strikes == TRUSTED_MAX_STRIKES
    ):
        return "TRUSTED"

    return "PROBATION"


def _parse_score(raw_text: str, key: str, fallback: float) -> float:
    """
    LLM 응답 문자열에서 JSON key 값을 추출합니다.
    파싱 실패 시 fallback 반환.

    지원 형식:
      {"delta": 7.5}
      {"richness":3}
      ... delta: 7.5 ...  (JSON 파싱 실패 시 정규식 폴백)
    """
    # 1차: JSON 파싱 시도
    try:
        # LLM 응답에 JSON 외 텍스트가 섞일 수 있어 중괄호 블록만 추출
        match = re.search(r"\{[^}]+\}", raw_text)
        if match:
            data = json.loads(match.group())
            value = float(data[key])
            return max(0.0, min(10.0, value))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        pass

    # 2차: 정규식으로 숫자 직접 추출
    try:
        pattern = rf'"{key}"\s*:\s*([\d.]+)'
        match2 = re.search(pattern, raw_text)
        if match2:
            value = float(match2.group(1))
            return max(0.0, min(10.0, value))
    except (ValueError, AttributeError):
        pass

    return fallback
