import asyncio
import yaml
import feedparser
import httpx
import os
import time
import csv
import trafilatura
import uuid
import hashlib
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance, PointIdsList

from hooks import HookManager
from evaluator import SourceEvaluator

# --- 1. 환경 및 경로 설정 ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.45.80:11434/api/embeddings")
OLLAMA_GEN_URL = OLLAMA_URL.replace(
    "/api/embeddings", "/api/generate"
)  # 본문 정제용 주소
QDRANT_HOST = os.getenv("QDRANT_HOST", "192.168.45.80")
SLEEP_INTERVAL = int(os.getenv("SLEEP_INTERVAL", 10800))
CLEANUP_THRESHOLD = int(os.getenv("CLEANUP_THRESHOLD", 300))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", 30))
COLLECTION_NAME = "osint_news"
EMBED_MODEL = "bge-m3"
CLEAN_MODEL = "llama3"

CONFIG_FILE = "config/sources.yaml"
DB_FILE = "config/sources_db.csv"
SQLITE_DB_FILE = "config/reliability.db"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

client = QdrantClient(host=QDRANT_HOST, port=6333)
hook_manager = HookManager()


# --- 2. CSV DB 관리 로직 ---
def load_db():
    db = {}
    if os.path.exists(DB_FILE):
        with open(DB_FILE, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                db[row["URL"]] = row
    return db


def save_db(db):
    with open(DB_FILE, mode="w", encoding="utf-8", newline="") as f:
        fieldnames = ["Name", "URL", "Status", "Last_Checked", "Note"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in db.values():
            writer.writerow(row)


# --- 3. 4060 GPU 엔진 (임베딩 & 정제) ---
async def get_embedding(text, http_client):
    response = await http_client.post(
        OLLAMA_URL, json={"model": EMBED_MODEL, "prompt": text}, timeout=30.0
    )
    return response.json()["embedding"]


def chunk_text(text, chunk_size=2500, overlap=300):
    """
    긴 기사 본문을 문맥이 끊기지 않도록 겹치는 구간(Overlap)을 두어 조각냅니다.
    """
    if not text:
        return []

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)

        if end >= text_length:
            break

        start = end - overlap

    return chunks


async def clean_html_with_ai(raw_html, http_client):
    """HTML 소스를 llama3로 보내서 기사 본문만 빼옵니다."""
    prompt = f"다음 웹페이지 HTML 소스 코드에서 광고, 메뉴, 네비게이션을 모두 무시하고 '순수한 기사 본문 텍스트'만 한국어로 번역해서 추출해줘. 다른 말은 덧붙이지 마.\n\n[HTML 소스]\n{raw_html}"
    try:
        response = await http_client.post(
            OLLAMA_GEN_URL,
            json={"model": CLEAN_MODEL, "prompt": prompt, "stream": False},
            timeout=60.0,
        )
        return response.json()["response"]
    except Exception as e:
        print(f"      [!] AI 본문 정제 실패: {e}")
        return None


# --- 4. 하이브리드 본문 추출 ---
async def extract_full_text(html_content, http_client):
    # 1차: Trafilatura 라이브러리로 초고속 추출
    text = trafilatura.extract(
        html_content, include_comments=False, include_tables=True
    )

    # 2차: 내용이 너무 짧거나 실패하면 4060(llama3) 출격
    if not text or len(text) < 150:
        print("      [!] 라이브러리 추출 미흡. 4060 AI 분석관 개입 중...")
        ai_text = await clean_html_with_ai(html_content[:15000], http_client)
        if ai_text:
            return ai_text

    return text if text else "본문 추출 실패"


# --- 5. 신규 정보원 테스트 로직 ---
async def test_new_source(source, http_client):
    url = source["url"]
    try:
        response = await http_client.get(url, timeout=15.0, follow_redirects=True)

        if response.status_code in [401, 403, 429, 503]:
            return "BLOCKED", f"HTTP {response.status_code} (방화벽/봇 차단 의심)"

        elif response.status_code in [400, 404, 410]:
            return "DEAD_LINK", f"HTTP {response.status_code} (존재하지 않는 페이지)"

        elif response.status_code != 200:
            return "FAILED", f"HTTP {response.status_code} (기타 오류)"

        feed = feedparser.parse(response.text)

        if len(feed.entries) > 0:
            return "SUCCESS", f"정상 (기사 {len(feed.entries)}개)"

        return "EMPTY_FEED", "HTTP 200이나 RSS 데이터 없음"

    except httpx.ConnectTimeout:
        return "DEAD_LINK", "연결 시간 초과 (서버 응답 없음)"
    except httpx.ConnectError:
        return "DEAD_LINK", "DNS/연결 거부 (서버 다운 의심)"
    except Exception as e:
        return "ERROR", f"예외 발생: {str(e)}"


# --- 6. 메인 수집 프로세스 ---
async def process_feed(source, db, http_client):
    url = source["url"]
    name = source["name"]
    record = db.get(url)

    if not record:
        status, note = await test_new_source(source, http_client)
        db[url] = {
            "Name": name,
            "URL": url,
            "Status": status,
            "Last_Checked": time.strftime("%Y-%m-%d %H:%M:%S"),
            "Note": note,
        }
        save_db(db)
        if status != "SUCCESS":
            print(f"[-] {name} 정찰 실패 ({note})")
            return
    elif record["Status"] != "SUCCESS":
        return

    print(f"[*] {name} 수집 및 본문 딥다이브 중...")
    try:
        response = await http_client.get(url, timeout=20.0, follow_redirects=True)
        feed = feedparser.parse(response.text)

        for entry in feed.entries[:10]:
            title = entry.title
            link = entry.link

            try:
                art_resp = await http_client.get(
                    link, timeout=15.0, follow_redirects=True
                )
                full_text = await extract_full_text(art_resp.text, http_client)
            except:
                full_text = entry.get("summary", title)

            chunks = chunk_text(full_text, chunk_size=2500, overlap=300)
            print(
                f"  [*] 원문 길이 {len(full_text)}자 -> {len(chunks)}개의 조각(Chunk)으로 분할 완료."
            )

            current_timestamp = int(time.time())

            for i, chunk_content in enumerate(chunks):
                # 본문 내용을 해싱하여 고속 중복 검사 및 고정 ID 생성
                unique_string = chunk_content
                point_id = str(
                    uuid.UUID(
                        hex=hashlib.md5(unique_string.encode("utf-8")).hexdigest()
                    )
                )

                embed_target = f"{title}: {chunk_content}"
                vector = await get_embedding(embed_target, http_client)

                payload = {
                    "title": title,
                    "link": link,
                    "source_name": name,
                    "project": source.get("project", "General"),
                    "category": source.get("category", "News"),
                    "timestamp": current_timestamp,
                    "content": chunk_content,
                    "chunk_info": f"Part {i + 1} of {len(chunks)}",
                }

                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=[PointStruct(id=point_id, vector=vector, payload=payload)],
                )

                # 🕵️ 감찰관 훅 트리거 — 백그라운드에서 비동기 평가 실행
                await hook_manager.trigger("article_inserted", payload=payload, vector=vector)

                await asyncio.sleep(0.1)

            await asyncio.sleep(1)

        print(f"  -> {name} 완료.")

    except Exception as e:
        print(f"[!] {name} 수집 에러: {e}")


async def cleanup_database():
    """매 사이클마다 DB를 스캔하여 저품질 및 노후 데이터를 제거합니다."""
    print(
        f"[*] 데이터베이스 자율 정화 시작 (기준: {CLEANUP_THRESHOLD}자 미만, {RETENTION_DAYS}일 경과)"
    )

    deleted_count = 0
    offset = None
    now_ts = time.time()
    retention_ts = now_ts - (RETENTION_DAYS * 24 * 3600)

    while True:
        points, next_page = client.scroll(
            collection_name=COLLECTION_NAME, limit=100, with_payload=True, offset=offset
        )

        target_ids = []
        for point in points:
            content = point.payload.get("content", "")
            timestamp = point.payload.get("timestamp", now_ts)

            is_low_quality = len(content) < CLEANUP_THRESHOLD
            is_expired = timestamp < retention_ts

            if is_low_quality or is_expired:
                target_ids.append(point.id)

        if target_ids:
            client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=PointIdsList(points=target_ids),
            )
            deleted_count += len(target_ids)

        if not next_page:
            break
        offset = next_page

    if deleted_count > 0:
        print(f"  [+] 정화 완료: 총 {deleted_count}개의 데이터가 정리되었습니다.")


# --- 7. 초기화 및 무한 루프 ---
# --- 7. 초기화 및 단일 사이클 로직 ---
def setup_collector():
    """Qdrant 컬렉션 등 시스템을 초기에 준비합니다."""
    print("🚀 자율형 OSINT Collector 2.0 (FastAPI 통합) 준비 중...")
    if not client.collection_exists(collection_name=COLLECTION_NAME):
        print(f"[*] '{COLLECTION_NAME}' 컬렉션이 존재하지 않아 새로 생성합니다...")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=1024,
                distance=Distance.COSINE,
            ),
        )
        print(f"  [+] 컬렉션 생성 완료 (차원수: 1024)")
    else:
        print(f"[*] '{COLLECTION_NAME}' 컬렉션 상태 확인 완료 (정상).")

async def run_crawl_cycle():
    """1회성 크롤링/수집 사이클을 실행합니다. (스케줄러나 수동 트리거 트리거 시 호출됨)"""
    print(f"\n[{time.ctime()}] 🚀 수집 사이클 딥다이브 시작...")
    
    # 훅 초기화 (중복 등록 방지)
    hook_manager._hooks["article_inserted"] = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=60.0) as http_client:
        # 감찰관 초기화 (1사이클용)
        evaluator = SourceEvaluator(
            sqlite_db_path=SQLITE_DB_FILE,
            qdrant_client=client,
            llm_client=http_client,
            llm_gen_url=OLLAMA_GEN_URL,
            llm_model=CLEAN_MODEL,
        )
        hook_manager.register("article_inserted", evaluator.on_article_inserted)

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            db = load_db()

            for source in config.get("sources", []):
                await process_feed(source, db, http_client)

            await cleanup_database()
            print(f"[{time.ctime()}] ✅ 수집 사이클이 무사히 완료되었습니다.")
        except Exception as e:
            print(f"❌ 수집 사이클 중 에러 발생: {e}")
