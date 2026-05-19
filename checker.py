import hashlib
import json
import os
import requests
from datetime import datetime, timezone, timedelta

# ──────────────────────────────────────────────
# Файлы расписания (Google Drive, публичные)
# ──────────────────────────────────────────────
FILES = {
    "Понедельник": "1NxzC8xoYkOBZV1PmoQTgNgUHakDYYPFl",
    "Вторник":     "1lmZO9Ee6ivFnlS4Hy9d6xReFC_iySsjg",
    "Среда":       "1Uue4I2nIhA8VB5WBd6bbJbG4U0tE9v3Q",
    "Четверг":     "1LYtbGmStSiJktyDEo3575Kq3LSWorL_b",
    "Пятница":     "1-iH0PDSIG2j72yOPVGply39IJd3_Vu77",
}

HASHES_FILE = "hashes.json"
DRIVE_URL   = "https://drive.google.com/uc?export=download&id={}"
DRIVE_VIEW  = "https://drive.google.com/file/d/{}/view?usp=sharing"

# ──────────────────────────────────────────────
# Telegram
# Несколько получателей — через запятую в секрете TELEGRAM_CHAT_ID
# Пример: 5558636337,-5035178270
# ──────────────────────────────────────────────
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_IDS  = [cid.strip() for cid in os.environ["TELEGRAM_CHAT_ID"].split(",")]

def send_telegram(text: str):
    """Отправляет сообщение всем получателям из CHAT_IDS."""
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
                print(f"    ✓ Отправлено в {chat_id}")
            else:
                print(f"    ✗ Ошибка отправки в {chat_id}: {resp.text}")
        except Exception as e:
            print(f"    ✗ Исключение при отправке в {chat_id}: {e}")

# ──────────────────────────────────────────────
# Скачать файл и посчитать MD5
# ──────────────────────────────────────────────
def get_md5(file_id: str) -> str | None:
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

        md5 = hashlib.md5()
        for chunk in resp.iter_content(chunk_size=8192):
            md5.update(chunk)
        return md5.hexdigest()

    except Exception as e:
        print(f"  Ошибка при скачивании {file_id}: {e}")
        return None

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
    now = datetime.now(timezone(timedelta(hours=5))).strftime("%d.%m.%Y %H:%M")
    print(f"[{now}] Проверка расписания...")
    print(f"Получатели: {', '.join(CHAT_IDS)}")

    old_hashes = load_hashes()
    new_hashes = {}
    changed    = []

    for day, file_id in FILES.items():
        print(f"  Проверяю: {day}...", end=" ")
        md5 = get_md5(file_id)

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
            changed.append((day, file_id))
        else:
            print(f"без изменений ({md5[:8]})")

    for day, file_id in changed:
        link = DRIVE_VIEW.format(file_id)
        msg = (
            f"📅 <b>Расписание обновлено!</b>\n\n"
            f"День: <b>{day}</b>\n"
            f"Время: {now}\n\n"
            f"🔗 <a href=\"{link}\">Открыть расписание</a>"
        )
        print(f"  → Отправка уведомления: {day}")
        send_telegram(msg)

    save_hashes(new_hashes)

    if not changed:
        print("Изменений нет.")
    else:
        print(f"Итого обновлений: {len(changed)}")

if __name__ == "__main__":
    main()
