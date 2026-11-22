import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import fitz  # PyMuPDF
import requests
from cryptography.fernet import Fernet
from PIL import Image

# ──────────────────────────────────────────────
# Файлы расписания (Google Drive, публичные)
# ──────────────────────────────────────────────
FILES = {
    "Понедельник": "1vYT59M2NtWmHu6D7V0dnOkcE5aT9THAZ",
    "Вторник":     "1K51VbGtemAfykL44H2yuHA1FsAOx_z2k",
    "Среда":       "1Ak2fXL5qAuqgBZVfi8ecj8SatXaUAbo5",
    "Четверг":     "1rDKX9wzPA2cxPKATMnPPwnQshV4omsWK",
    "Пятница":     "1pSs0UFOmlqPoAMKJ53HGpe7tSZJtr2BD",
}

HASHES_FILE      = "hashes.json"
SUBSCRIBERS_FILE = "subscribers.enc"
DRIVE_URL        = "https://drive.google.com/uc?export=download&id={}"
DRIVE_VIEW       = "https://drive.google.com/file/d/{}/view?usp=sharing"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FERNET    = Fernet(os.environ["SUBSCRIBERS_KEY"].encode())

WELCOME_TEXT = (
    "👋 Привет! Я бот для отслеживания изменений расписания.\n\n"
    "Слежу за файлами, которые загружает администратор, и присылаю сюда "
    "обновлённую версию, как только она появляется.\n\n"
    "📬 Расписание приходит по мере того, как его реально обновляют — "
    "если пришёл не завтрашний день, это не ошибка бота: значит, именно "
    "этот файл поменяли на сайте, а не тот, что нужен на завтра.\n\n"
    "⚠️ Из-за этого дни могут приходить не по порядку недели — всё "
    "напрямую зависит от того, когда именно админ меняет файлы.\n\n"
    "Чтобы отписаться — отправь /stop"
)
ALREADY_TEXT = "Ты уже подписан на уведомления 🙂"
GOODBYE_TEXT = "Ты отписан от уведомлений. Если захошь вернуться — просто снова отправь /start"
NOT_SUB_TEXT = "Ты и так не был подписан."


# ──────────────────────────────────────────────
# Telegram: отправка с повторами и обработкой 429
# Возвращает: "ok" | "blocked" | "error"
# ──────────────────────────────────────────────
def _post_telegram(
    method: str,
    *,
    json_payload: dict | None = None,
    data: dict | None = None,
    files: dict | None = None,
    timeout: int = 15,
) -> str:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                json=json_payload,
                data=data,
                files=files,
                timeout=timeout,
            )
            if resp.status_code == 200:
                return "ok"
            if resp.status_code == 403:
                return "blocked"
            if resp.status_code == 429:
                try:
                    retry_after = int(resp.json().get("parameters", {}).get("retry_after", 2))
                except Exception:
                    retry_after = 2
                time.sleep(min(max(retry_after, 1), 15))
                continue

            print(f"    ✗ Ошибка Telegram API ({resp.status_code}): {resp.text}")
            if resp.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            return "error"
        except Exception as e:
            print(f"    ✗ Исключение при запросе {method}: {e}")
            time.sleep(1.5 * (attempt + 1))
    return "error"


def send_telegram_to(chat_id: str, text: str) -> str:
    return _post_telegram(
        "sendMessage",
        json_payload={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )


def send_image_to(chat_id: str, img_bytes: bytes, caption: str, filename: str) -> str:
    return _post_telegram(
        "sendDocument",
        data={
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
        },
        files={
            "document": (filename, img_bytes, "image/png"),
        },
        timeout=45,
    )


# ──────────────────────────────────────────────
# Рассылка списку подписчиков (с самоочисткой)
# ──────────────────────────────────────────────
def broadcast_text(recipients: list, text: str, subs: dict) -> tuple[int, int, int]:
    sent, blocked, errors = 0, 0, 0
    for chat_id in recipients:
        status = send_telegram_to(chat_id, text)
        if status == "ok":
            sent += 1
            print(f"    ✓ Сообщение отправлено в {chat_id}")
        elif status == "blocked":
            blocked += 1
            print(f"    ⚠ {chat_id} заблокировал бота — удаляю из подписчиков")
            subs["chats"].pop(chat_id, None)
        else:
            errors += 1
            print(f"    ✗ Не удалось отправить сообщение в {chat_id}")
        time.sleep(0.05)
    return sent, blocked, errors


def broadcast_image(recipients: list, img_bytes: bytes, caption: str, subs: dict, filename: str) -> tuple[int, int, int]:
    sent, blocked, errors = 0, 0, 0
    for chat_id in recipients:
        status = send_image_to(chat_id, img_bytes, caption, filename)
        if status == "ok":
            sent += 1
            print(f"    ✓ Фото отправлено в {chat_id}")
        elif status == "blocked":
            blocked += 1
            print(f"    ⚠ {chat_id} заблокировал бота — удаляю из подписчиков")
            subs["chats"].pop(chat_id, None)
        else:
            errors += 1
            print(f"    ✗ Не удалось отправить фото в {chat_id}")
        time.sleep(0.05)
    return sent, blocked, errors


# ──────────────────────────────────────────────
# Обработка входящих /start и /stop
# ──────────────────────────────────────────────
def process_telegram_updates(subs: dict):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    while True:
        try:
            resp = requests.get(
                url,
                params={"offset": subs.get("offset", 0), "limit": 100, "timeout": 0},
                timeout=15,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except Exception as e:
            print(f"  Ошибка получения обновлений Telegram: {e}")
            return

        if not updates:
            break

        for upd in updates:
            subs["offset"] = upd["update_id"] + 1

            msg = upd.get("message")
            if not msg or "text" not in msg:
                continue

            raw_text = msg["text"].strip()
            if not raw_text:
                continue

            cmd = raw_text.split()[0].split("@")[0].lower()
            chat_id = str(msg["chat"]["id"])

            if cmd == "/start":
                if chat_id in subs["chats"]:
                    send_telegram_to(chat_id, ALREADY_TEXT)
                else:
                    subs["chats"][chat_id] = {
                        "username":   msg["chat"].get("username", ""),
                        "first_name": msg["chat"].get("first_name", ""),
                    }
                    print(f"  ➕ Новый подписчик: {chat_id} ({subs['chats'][chat_id].get('username')})")
                    send_telegram_to(chat_id, WELCOME_TEXT)

            elif cmd == "/stop":
                if chat_id in subs["chats"]:
                    del subs["chats"][chat_id]
                    print(f"  ➖ Отписался: {chat_id}")
                    send_telegram_to(chat_id, GOODBYE_TEXT)
                else:
                    send_telegram_to(chat_id, NOT_SUB_TEXT)

        if len(updates) < 100:
            break


# ──────────────────────────────────────────────
# PDF → одно цельное PNG (через PyMuPDF, без Poppler)
# ──────────────────────────────────────────────
def pdf_to_single_image(pdf_bytes: bytes, dpi: int = 150) -> bytes | None:
    pages = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.page_count == 0:
                return None
            zoom = dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for page in doc:
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                pages.append(img)
    except Exception as e:
        print(f"  Ошибка конвертации PDF: {e}")
        return None

    width  = max(p.width for p in pages)
    height = sum(p.height for p in pages)

    combined = Image.new("RGB", (width, height), color=(255, 255, 255))
    y = 0
    for page in pages:
        combined.paste(page, (0, y))
        y += page.height

    buf = io.BytesIO()
    combined.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


# ──────────────────────────────────────────────
# Скачать файл → (md5, bytes, content_type)
# ──────────────────────────────────────────────
def download_file(file_id: str):
    session = requests.Session()
    url = DRIVE_URL.format(file_id)

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")

        if "text/html" in content_type:
            token = None
            for key, val in resp.cookies.items():
                if "download_warning" in key.lower():
                    token = val
                    break
            if not token:
                match = re.search(r'name="confirm"\s+value="([^"]+)"', resp.text)
                if match:
                    token = match.group(1)
            if token:
                resp = session.get(url, params={"confirm": token}, timeout=30)
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")

        data = resp.content
        if not data.startswith(b"%PDF-"):
            print(f"  Файл {file_id} не является PDF (Content-Type: {content_type})")
            return None, None, None

        md5 = hashlib.md5(data).hexdigest()
        return md5, data, content_type

    except Exception as e:
        print(f"  Ошибка при скачивании {file_id}: {e}")
        return None, None, None


# ──────────────────────────────────────────────
# Загрузить / сохранить хэши
# ──────────────────────────────────────────────
def load_hashes() -> dict:
    if os.path.exists(HASHES_FILE):
        with open(HASHES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_hashes(hashes: dict):
    with open(HASHES_FILE, "w", encoding="utf-8") as f:
        json.dump(hashes, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# Загрузить / сохранить подписчиков
# ──────────────────────────────────────────────
def load_subscribers() -> dict:
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE, "rb") as f:
            decrypted = FERNET.decrypt(f.read())
            return json.loads(decrypted)
    return {"offset": 0, "chats": {}}


def save_subscribers(subs: dict):
    data = json.dumps(subs, ensure_ascii=False).encode("utf-8")
    with open(SUBSCRIBERS_FILE, "wb") as f:
        f.write(FERNET.encrypt(data))


# ──────────────────────────────────────────────
# Основная логика
# ──────────────────────────────────────────────
def main():
    tz  = timezone(timedelta(hours=5))
    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    print(f"[{now}] Проверка расписания...")

    subs = load_subscribers()
    subs_snapshot = json.dumps(subs, sort_keys=True)
    process_telegram_updates(subs)

    print(f"Подписчиков: {len(subs['chats'])}")

    old_hashes = load_hashes()
    new_hashes = {}
    changed    = []

    for day, file_id in FILES.items():
        print(f"  Проверяю: {day}...", end=" ")
        md5, file_bytes, content_type = download_file(file_id)

        if md5 is None:
            print("ОШИБКА (пропуск)")
            if day in old_hashes:
                new_hashes[day] = old_hashes[day]
            continue

        new_hashes[day] = md5
        old_md5 = old_hashes.get(day)

        if old_md5 != md5:
            if old_md5 is None:
                print(f"первый запуск / хэш не найден ({md5[:8]}) → отправляю")
            else:
                print(f"ИЗМЕНИЛСЯ ({old_md5[:8]} → {md5[:8]})")
            changed.append((day, file_id, file_bytes))
        else:
            print(f"без изменений ({md5[:8]})")

    if changed and not subs["chats"]:
        print("  Расписание изменилось, но подписчиков пока нет — некому слать.")

    for day, file_id, file_bytes in changed:
        link    = DRIVE_VIEW.format(file_id)
        caption = (
            f"📅 <b>Расписание обновлено!</b>\n\n"
            f"День: <b>{day}</b>\n"
            f"Время: {now}\n\n"
            f"🔗 <a href=\"{link}\">Открыть оригинал</a>"
        )
        print(f"  → Конвертирую PDF в изображение: {day}")
        img_bytes = pdf_to_single_image(file_bytes, dpi=150)

        current_recipients = list(subs["chats"].keys())
        if not current_recipients:
            continue

        if img_bytes:
            print(f"     Размер: {len(img_bytes) // 1024} КБ, отправляю...")
            sent_ok, _, errors = broadcast_image(
                current_recipients, img_bytes, caption, subs, filename=f"{day}.png"
            )
        else:
            print("     Конвертация не удалась, отправляю ссылку")
            sent_ok, _, errors = broadcast_text(current_recipients, caption, subs)

        if sent_ok == 0 and errors > 0:
            print(f"     ⚠ Рассылка не удалась — откат хэша для {day}, повторю в следующий запуск.")
            if day in old_hashes:
                new_hashes[day] = old_hashes[day]
            else:
                new_hashes.pop(day, None)

    save_hashes(new_hashes)

    if json.dumps(subs, sort_keys=True) != subs_snapshot:
        save_subscribers(subs)

    if not changed:
        print("Изменений нет.")
    else:
        print(f"Итого обновлений: {len(changed)}")


if __name__ == "__main__":
    main()
