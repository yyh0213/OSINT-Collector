# evaluator.py
import sqlite3
import asyncio


class SourceEvaluator:
    """정보원의 신뢰도를 평가하고 앵무새를 색출하는 감찰관 로직"""

    def __init__(self, sqlite_db_path, qdrant_client, llm_client):
        self.db_path = sqlite_db_path
        self.qdrant = qdrant_client
        self.llm = llm_client
        self._init_sqlite_db()

    def _init_sqlite_db(self):
        """평가표(SQLite 테이블) 세팅"""
        pass  # (테이블 생성 쿼리 예정)

    async def on_article_inserted(self, payload: dict, vector: list):
        """수집기가 기사를 넣었을 때 발동할 평가 로직"""
        print(
            f"  [🕵️ 감찰관] '{payload['project']}' 신규 기사 유사도 및 독창성 검증 중..."
        )

        # 1. Qdrant 벡터 유사도 검색
        # 2. LLM 델타 분석 (독창성 검증)
        # 3. SQLite 신뢰도 점수 업데이트

        await asyncio.sleep(0.1)  # 로직 실행 대기
