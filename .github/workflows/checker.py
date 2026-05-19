import hashlib
import json
import os
import requests
from datetime import datetime

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
# ──────────────────────────────────────────────
BOT_TOKEN = os.environ["8871709663:AAGWxq2I5SqBCyCs9ViBZd3XotbNomFl1_M"]
CHAT_ID   = os.environ["-5035178270"]

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=10)

# ──────────────────────────────────────────────
# Скачать файл и посчитать MD5
# ──────────────────────────────────────────────
def get_md5(file_id: str) -> str | None:
    """
    Скачивает файл по Drive ID и возвращает MD5.
    Если файл большой, Drive отдаёт страницу-подтверждение —
    обрабатываем оба варианта.
    """
    session = requests.Session()
    url = DRIVE_URL.format(file_id)

    try:
        resp = session.get(url, timeout=30, stream=True)
        resp.raise_for_status()

        # Если Drive отдал HTML страницу подтверждения ("вирус-скан")
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" in content_type:
            # Ищем confirm-токен
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
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    print(f"[{now}] Проверка расписания...")

    old_hashes = load_hashes()
    new_hashes = {}
    changed    = []

    for day, file_id in FILES.items():
        print(f"  Проверяю: {day}...", end=" ")
        md5 = get_md5(file_id)

        if md5 is None:
            print("ОШИБКА (пропуск)")
            new_hashes[day] = old_hashes.get(day)  # сохраняем старый
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

    # Уведомляем об изменениях
    for day, file_id in changed:
        link = DRIVE_VIEW.format(file_id)
        msg = (
            f"📅 <b>Расписание обновлено!</b>\n\n"
            f"День: <b>{day}</b>\n"
            f"Время: {now}\n\n"
            f"🔗 <a href=\"{link}\">Открыть расписание</a>"
        )
        send_telegram(msg)
        print(f"  → Уведомление отправлено: {day}")

    save_hashes(new_hashes)

    if not changed:
        print("Изменений нет.")

if __name__ == "__main__":
    main()
