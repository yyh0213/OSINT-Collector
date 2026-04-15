# 🚀 Autonomous OSINT Collector 2.0 (Intelligence)

"Intelligence"는 오픈 소스 정보(OSINT)를 자율적으로 수집, 정제 및 벡터화하여 지식 베이스를 구축하는 고성능 자동화 시스템입니다. RSS 피드로부터 최신 소식을 추적하고, AI를 활용해 광고가 제거된 순수 본문만을 추출하여 시맨틱 검색이 가능한 상태로 저장합니다.

## ✨ 주요 기능

- **자율 수집 엔진**: 설정된 주기마다 다양한 정보원(RSS)으로부터 최신 게시물을 자동으로 탐색합니다.
- **하이브리드 본문 추출**: 
    - 1차: `trafilatura`를 통한 고속 텍스트 추출.
    - 2차: 추출 실패 시 `Llama-3` AI를 기용하여 본문만 정교하게 분리 및 한국어 최적화.
- **지능형 텍스트 청킹(Chunking)**: 긴 문서를 문맥 손실 없이 인덱싱하기 위해 Overlap 구간을 둔 가변적 분할 시스템을 탑재했습니다.
- **AI 임베딩 및 시맨틱 저장**: `bge-m3` 오픈소스 모델을 활용해 고차원 벡터를 생성하고 `Qdrant` 벡터 데이터베이스에 저장하여 의미 기반 검색을 지원합니다.
- **자율 데이터 정화**: 설정된 보존 기간 및 저품질(짧은 본문 등) 기준에 따라 데이터베이스를 스스로 관리합니다.
- **확장 가능한 이벤트 기반 구조**: `HookManager`와 `SourceEvaluator`를 통해 수집된 데이터의 독창성을 검증하고 이벤트를 처리합니다.

## 🛠 기술 스택

- **Main Logic**: Python 3.10+ (Asyncio, Httpx)
- **AI Infrastructure**: Ollama (`llama3` for cleaning, `bge-m3` for embeddings)
- **Database**: Qdrant (Vector Store)
- **Deployment**: Docker, Docker Compose

## 🚀 시작하기

### 1. 전제 조건
- AI 엔진으로 활용할 **Ollama** 서버가 구동 중이어야 합니다 (`bge-m3`, `llama3` 모델 필요).
- 벡터 데이터를 저장할 **Qdrant** 서버가 필요합니다.

### 2. 환경 설정
`collector.py`의 상단 환경 변수 또는 환경 변수(ENV)를 통해 주소를 설정합니다.
- `OLLAMA_URL`: Ollama 서버 API 주소
- `QDRANT_HOST`: Qdrant 서버 주소
- `SLEEP_INTERVAL`: 수집 주기 (기본 3시간)

### 3. 정보원(Sources) 관리
`config/sources.yaml` 파일에 수집하고자 하는 RSS 주소와 카테고리를 추가합니다.

```yaml
sources:
  - name: "보안뉴스"
    url: "https://example.com/rss"
    project: "CyberSecurity"
    category: "News"
```

### 4. 실행
```bash
# 직접 실행
python collector.py

# Docker 사용 시
docker build -t osint-collector .
docker run -d --name osint-collector osint-collector
```

## 📂 프로젝트 구조

- `collector.py`: 메인 루프 및 수집/임베딩 로직.
- `evaluator.py`: 수집된 데이터의 신뢰도 및 독창성 검증 (감찰관 모듈).
- `hooks.py`: 이벤트 기반 비동기 작업을 위한 매니저.
- `config/`: 정보원 설정(`sources.yaml`) 및 상태 추적 DB 저장.
- `dockerfile`: 컨테이너 빌드 설정.
- `CHANGELOG.md`: 버전별 변경 이력.
- `docs/PHILOSOPHY.md` 📖: 시스템의 철학, 운영 원리, 의의 및 한계점이 자세히 서술된 백서.

## 📝 라이선스
이 프로젝트는 개인 연구 및 정보 수집을 위해 제작되었습니다. 수집하는 소스에 대한 저작권 및 이용 약관을 준수하시기 바랍니다.
