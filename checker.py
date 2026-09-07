import hashlib
import json
import os
import io
import requests
from datetime import datetime, timezone, timedelta
from pdf2image import convert_from_bytes
from PIL import Image

# ──────────────────────────────────────────────
# Файлы расписания (Google Drive, публичные)
# ──────────────────────────────────────────────
FILES = {
    "Понедельник": "1vYT59M2NtWmHu6D7V0dnOkcE5aT9THAZ",
    "Вторник":     "1fYsQ2Izu3D5urH0eldwnInIFZy2e9qJ0",
    "Среда":       "1Ak2fXL5qAuqgBZVfi8ecj8SatXaUAbo5",
    "Четверг":     "1rDKX9wzPA2cxPKATMnPPwnQshV4omsWK",
    "Пятница":     "1pSs0UFOmlqPoAMKJ53HGpe7tSZJtr2BD",
}

HASHES_FILE      = "hashes.json"
SUBSCRIBERS_FILE = "subscribers.json"
DRIVE_URL   = "https://drive.google.com/uc?export=download&id={}"
DRIVE_VIEW  = "https://drive.google.com/file/d/{}/view?usp=sharing"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

WELCOME_TEXT = (
    "✅ Ты подписан на уведомления об изменении расписания.\n"
    "Как только оно поменяется — пришлю сюда обновлённую картинку.\n\n"
    "Чтобы отписаться — отправь /stop"
)
ALREADY_TEXT   = "Ты уже подписан на уведомления 🙂"
GOODBYE_TEXT   = "Ты отписан от уведомлений. Если захочешь вернуться — просто снова отправь /start"
NOT_SUB_TEXT   = "Ты и так не был подписан."


# ──────────────────────────────────────────────
# Telegram: сообщение / фото ОДНОМУ адресату
# ──────────────────────────────────────────────
def send_telegram_to(chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=10)
        if resp.status_code == 200:
            return True
        print(f"    ✗ Ошибка отправки в {chat_id}: {resp.text}")
        return resp.status_code != 403  # 403 = бот заблокирован, сигнал на удаление подписчика
    except Exception as e:
        print(f"    ✗ Исключение при отправке в {chat_id}: {e}")
        return True  # не удаляем подписчика из-за временной сетевой ошибки


def send_image_to(chat_id: str, img_bytes: bytes, caption: str, filename: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        resp = requests.post(url, data={
            "chat_id":    chat_id,
            "caption":    caption,
            "parse_mode": "HTML",
        }, files={
            "document": (filename, img_bytes, "image/png")
        }, timeout=30)
        if resp.status_code == 200:
            return True
        print(f"    ✗ Ошибка отправки фото в {chat_id}: {resp.text}")
        return resp.status_code != 403
    except Exception as e:
        print(f"    ✗ Исключение при отправке фото в {chat_id}: {e}")
        return True


# ──────────────────────────────────────────────
# Рассылка списку подписчиков (с самоочисткой)
# ──────────────────────────────────────────────
def broadcast_text(recipients: list, text: str, subs: dict):
    for chat_id in recipients:
        ok = send_telegram_to(chat_id, text)
        if ok:
            print(f"    ✓ Сообщение отправлено в {chat_id}")
        else:
            print(f"    ⚠ {chat_id} заблокировал бота — удаляю из подписчиков")
            subs["chats"].pop(chat_id, None)


def broadcast_image(recipients: list, img_bytes: bytes, caption: str, subs: dict, filename: str):
    for chat_id in recipients:
        ok = send_image_to(chat_id, img_bytes, caption, filename)
        if ok:
            print(f"    ✓ Фото отправлено в {chat_id}")
        else:
            print(f"    ⚠ {chat_id} заблокировал бота — удаляю из подписчиков")
            subs["chats"].pop(chat_id, None)


# ──────────────────────────────────────────────
# Обработка входящих /start и /stop
# ──────────────────────────────────────────────
def process_telegram_updates(subs: dict):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, params={"offset": subs.get("offset", 0), "timeout": 0}, timeout=15)
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except Exception as e:
        print(f"  Ошибка получения обновлений Telegram: {e}")
        return

    for upd in updates:
        subs["offset"] = upd["update_id"] + 1

        msg = upd.get("message")
        if not msg or "text" not in msg:
            continue

        chat_id = str(msg["chat"]["id"])
        text    = msg["text"].strip()

        if text == "/start":
            if chat_id in subs["chats"]:
                send_telegram_to(chat_id, ALREADY_TEXT)
            else:
                subs["chats"][chat_id] = {
                    "username":   msg["chat"].get("username", ""),
                    "first_name": msg["chat"].get("first_name", ""),
                }
                print(f"  ➕ Новый подписчик: {chat_id} ({subs['chats'][chat_id].get('username')})")
                send_telegram_to(chat_id, WELCOME_TEXT)

        elif text == "/stop":
            if chat_id in subs["chats"]:
                del subs["chats"][chat_id]
                print(f"  ➖ Отписался: {chat_id}")
                send_telegram_to(chat_id, GOODBYE_TEXT)
            else:
                send_telegram_to(chat_id, NOT_SUB_TEXT)


# ──────────────────────────────────────────────
# PDF → одно цельное PNG (все страницы склеены)
# ──────────────────────────────────────────────
def pdf_to_single_image(pdf_bytes: bytes, dpi: int = 150) -> bytes | None:
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=dpi)
    except Exception as e:
        print(f"  Ошибка конвертации PDF: {e}")
        return None

    if not pages:
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
        resp = session.get(url, timeout=30, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")

        if "text/html" in content_type:
            token = None
            for key, val in resp.cookies.items():
                if "download_warning" in key.lower():
                    token = val
                    break
            if token:
                resp = session.get(
                    url, params={"confirm": token}, timeout=30, stream=True
                )
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")

        md5    = hashlib.md5()
        chunks = []
        for chunk in resp.iter_content(chunk_size=8192):
            md5.update(chunk)
            chunks.append(chunk)

        return md5.hexdigest(), b"".join(chunks), content_type

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
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"offset": 0, "chats": {}}


def save_subscribers(subs: dict):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# Основная логика
# ──────────────────────────────────────────────
def main():
    tz  = timezone(timedelta(hours=5))
    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    print(f"[{now}] Проверка расписания...")

    subs = load_subscribers()
    process_telegram_updates(subs)

    recipients = list(subs["chats"].keys())
    print(f"Подписчиков: {len(recipients)}")

    old_hashes = load_hashes()
    new_hashes = {}
    changed    = []

    for day, file_id in FILES.items():
        print(f"  Проверяю: {day}...", end=" ")
        md5, file_bytes, content_type = download_file(file_id)

        if md5 is None:
            print("ОШИБКА (пропуск)")
            new_hashes[day] = old_hashes.get(day)
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

    if changed and not recipients:
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

        if not recipients:
            continue

        if img_bytes:
            print(f"     Размер: {len(img_bytes) // 1024} КБ, отправляю...")
            broadcast_image(recipients, img_bytes, caption, subs, filename=f"{day}.png")
        else:
            print(f"     Конвертация не удалась, отправляю ссылку")
            broadcast_text(recipients, caption, subs)

    save_hashes(new_hashes)
    save_subscribers(subs)  # сохраняем в любом случае — сдвинулся offset и/или список подписчиков

    if not changed:
        print("Изменений нет.")
    else:
        print(f"Итого обновлений: {len(changed)}")


if __name__ == "__main__":
    main()
