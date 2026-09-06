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
    "Вторник":     "1lmZO9Ee6ivFnlS4Hy9d6xReFC_iySsjg",
    "Среда":       "1Ak2fXL5qAuqgBZVfi8ecj8SatXaUAbo5",
    "Четверг":     "1rDKX9wzPA2cxPKATMnPPwnQshV4omsWK",
    "Пятница":     "1pSs0UFOmlqPoAMKJ53HGpe7tSZJtr2BD",
}

HASHES_FILE = "hashes.json"
DRIVE_URL   = "https://drive.google.com/uc?export=download&id={}"
DRIVE_VIEW  = "https://drive.google.com/file/d/{}/view?usp=sharing"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_IDS  = [cid.strip() for cid in os.environ["TELEGRAM_CHAT_ID"].split(",")]

# ──────────────────────────────────────────────
# Telegram: текстовое сообщение
# ──────────────────────────────────────────────
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }, timeout=10)
            if resp.status_code == 200:
                print(f"    ✓ Сообщение отправлено в {chat_id}")
            else:
                print(f"    ✗ Ошибка: {resp.text}")
        except Exception as e:
            print(f"    ✗ Исключение: {e}")

# ──────────────────────────────────────────────
# Telegram: одно цельное фото без сжатия
# sendDocument сохраняет оригинальное качество
# ──────────────────────────────────────────────
def send_image(img_bytes: bytes, caption: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    for chat_id in CHAT_IDS:
        try:
            resp = requests.post(url, data={
                "chat_id":    chat_id,
                "caption":    caption,
                "parse_mode": "HTML",
            }, files={
                "document": ("schedule.png", img_bytes, "image/png")
            }, timeout=30)
            if resp.status_code == 200:
                print(f"    ✓ Фото отправлено в {chat_id}")
            else:
                print(f"    ✗ Ошибка в {chat_id}: {resp.text}")
        except Exception as e:
            print(f"    ✗ Исключение: {e}")

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

    # Склеиваем страницы вертикально
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
# Основная логика
# ──────────────────────────────────────────────
def main():
    tz  = timezone(timedelta(hours=5))
    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    print(f"[{now}] Проверка расписания...")
    print(f"Получатели: {', '.join(CHAT_IDS)}")

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

        if old_md5 is None:
            print(f"первый запуск ({md5[:8]})")
        elif old_md5 != md5:
            print(f"ИЗМЕНИЛСЯ ({old_md5[:8]} → {md5[:8]})")
            changed.append((day, file_id, file_bytes))
        else:
            print(f"без изменений ({md5[:8]})")

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

        if img_bytes:
            print(f"     Размер: {len(img_bytes) // 1024} КБ, отправляю...")
            send_image(img_bytes, caption)
        else:
            print(f"     Конвертация не удалась, отправляю ссылку")
            send_telegram(caption)

    save_hashes(new_hashes)

    if not changed:
        print("Изменений нет.")
    else:
        print(f"Итого обновлений: {len(changed)}")

if __name__ == "__main__":
    main()
