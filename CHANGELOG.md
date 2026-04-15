# Changelog

이 프로젝트의 주요 변경 사항을 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식으로 기록합니다.

---

## [2.1.0] - 2025-04-15

### 🕵️ 감찰관 모듈(evaluator.py) 신규 구현

수집기가 Qdrant에 기사를 적재한 직후, 비동기 훅을 통해 자동으로 매체 신뢰도를 평가하는 **감찰관 모듈**을 구현하고 시스템에 통합했습니다.

### Added

- **`evaluator.py` — `SourceEvaluator` 클래스 전면 구현**
  - SQLite `source_reliability` 테이블 자동 생성 (source_id, status, strikes 등 9개 컬럼)
  - **4단계 평가 파이프라인:**

    | Step | 메서드 | 역할 |
    |------|--------|------|
    | 1 | `_step1_upsert_source` | 매체 신규 등록 또는 `total_articles +1` |
    | 2 | `_step2_find_prior_similar` | Qdrant 벡터 유사도 ≥ 0.90인 선행 기사 검색 |
    | 3 | `_step3_llm_analysis` | LLM(Ollama)에 delta/richness 점수 요청 (0~10점) |
    | 4 | `_step4_update_scores` | 누적 이동 평균(CMA) 갱신 + 3진 아웃 + 상태 판정 |

  - **상태 판정 규칙:**
    - `BLACKLISTED` — `copycat_strikes ≥ 3` (3진 아웃)
    - `TRUSTED` — 기사 ≥ 10건 & delta ≥ 5.0 & richness ≥ 6.0 & strikes = 0
    - `PROBATION` — 그 외 (기본값)
  - 모든 내부 예외가 `try-except`로 격리되어 메인 수집 루프에 영향 없음
  - LLM 미설정 시 Mock 반환(5.0) 지원
  - JSON 파싱 실패 시 정규식 폴백 내장

### Changed

- **`collector.py` — 훅 시스템 통합**
  - `SQLITE_DB_FILE` 상수 추가 (`config/reliability.db`)
  - `hook_manager = HookManager()` 글로벌 인스턴스 생성
  - `main()` 진입 시 `SourceEvaluator` 초기화 및 `article_inserted` 훅 등록
  - Qdrant `upsert()` 직후 `hook_manager.trigger("article_inserted", ...)` 호출
  - LLM 호출용 `httpx.AsyncClient`의 수명을 전체 프로세스 라이프사이클과 동일하게 관리

### 데이터 흐름

```
collector.py                    evaluator.py
     │                               │
     ├─ upsert(기사) → Qdrant        │
     ├─ trigger("article_inserted")──┤
     │                               ├─ Step 1: SQLite upsert
     │                               ├─ Step 2: Qdrant 유사도 검색
     │                               ├─ Step 3: LLM delta/richness 분석
     │                               └─ Step 4: 점수 갱신 & 상태 판정
     ├─ 다음 기사 처리 (비블로킹)     │
     ...
```

---

## [2.0.0] - 2025-04-13

### 🚀 초기 릴리즈 — 자율형 OSINT Collector 2.0

### Added

- RSS 기반 자율 수집 엔진 (`collector.py`)
- `trafilatura` 1차 + `llama3` 2차 하이브리드 본문 추출
- Overlap 기반 지능형 텍스트 청킹 (2500자, 300자 겹침)
- `bge-m3` 임베딩 + Qdrant 벡터 저장
- 자율 데이터 정화 (저품질·만료 데이터 자동 삭제)
- `HookManager` 이벤트 버스 구조 도입
- Docker 컨테이너 배포 지원
- `config/sources.yaml` 기반 정보원 관리
- `config/sources_db.csv` 정보원 상태 자동 추적

### Fixed

- Dockerfile에서 `collector.py`만 복사하던 문제 → `COPY . .`로 전체 파일 복사
- `hook.py` → `hooks.py` 파일명 불일치 수정
