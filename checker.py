import hashlib
import json
import os
import io
import requests
from datetime import datetime, timezone, timedelta
from pdf2image import convert_from_bytes
from PIL import Image
import pdfplumber

# ──────────────────────────────────────────────
# Файлы расписания (Google Drive, публичные)
# ──────────────────────────────────────────────
FILES = {
    "Понедельник": "1NxzC8xoYkOBZV1PmoQTgNgUHakDYYPFl",
    "Вторник":     "1lmZO9Ee6ivFnlS4Hy9d6xReFC_iySsjg",
    "Среда":       "1Ak2fXL5qAuqgBZVfi8ecj8SatXaUAbo5",
    "Четверг":     "1LYtbGmStSiJktyDEo3575Kq3LSWorL_b",
    "Пятница":     "1-iH0PDSIG2j72yOPVGply39IJd3_Vu77",
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
# Telegram: отправка изображения
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
                "document": ("schedule_11a.png", img_bytes, "image/png")
            }, timeout=30)
            if resp.status_code == 200:
                print(f"    ✓ Фото отправлено в {chat_id}")
            else:
                print(f"    ✗ Ошибка в {chat_id}: {resp.text}")
        except Exception as e:
            print(f"    ✗ Исключение: {e}")

# ──────────────────────────────────────────────
# Резервный метод: вся страница целиком
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
# Умный метод: поиск 11А, вырезание и склейка
# ──────────────────────────────────────────────
def generate_11a_image(pdf_bytes: bytes, dpi: int = 150) -> bytes | None:
    scale = dpi / 72.0 
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            tables = page.find_tables()
            if not tables:
                return None
            
            table = tables[0]
            parsed_table = page.extract_tables()[0]
            
            target_cols = []
            header_row_idx = 0
            
            # Ищем колонки, содержащие "11а" или "11a"
            for r_idx, row in enumerate(parsed_table):
                for c_idx, cell in enumerate(row):
                    if cell:
                        # Приводим к нижнему регистру, убираем пробелы
                        cell_clean = str(cell).lower().replace(" ", "", "(208)МИ", "(208)ми")
                        
                        # Заменяем латинскую 'a' на русскую 'а' для универсальности
                        cell_clean = cell_clean.replace("a", "а")
                        
                        # Проверяем, что это именно 11а (с буквой а или а в скобках/рядом), исключая просто цифры вроде 111
                        if "11а(208)МИ" in cell_clean or "11a(208)МИ" in cell_clean or (cell_clean.startswith("11") and "а" in cell_clean):
                            print(f"  [DEBUG] Найдено совпадение '{cell}' в строке {r_idx}, колонка {c_idx}")
                            target_cols.append(c_idx)
                            # Захватываем соседнюю правую колонку (кабинеты)
                            if c_idx + 1 < len(row):
                                target_cols.append(c_idx + 1)
                            header_row_idx = r_idx
                if target_cols:
                    break
                    
            if not target_cols:
                return None
                
            # Ищем последнюю заполненную строку для 11а (чтобы обрезать пустые уроки снизу)
            last_valid_row = header_row_idx
            for r_idx in range(header_row_idx + 1, len(parsed_table)):
                row = parsed_table[r_idx]
                has_content = any(row[c] and str(row[c]).strip() for c in target_cols if c < len(row))
                if has_content:
                    last_valid_row = r_idx
                else:
                    # Если пошли пустые строки подряд — прекращаем поиск
                    break 
                    
            # Координаты сетки таблицы из pdfplumber
            time_x0 = table.cells[header_row_idx][0][0]
            time_x1 = table.cells[header_row_idx][1][2]
            
            class_x0 = table.cells[header_row_idx][min(target_cols)][0]
            class_x1 = table.cells[header_row_idx][max(target_cols)][2]
            
            y0 = table.cells[header_row_idx][0][1]
            y1 = table.cells[last_valid_row][0][3]
            
    except Exception as e:
        print(f"  Ошибка при парсинге координат таблиц: {e}")
        return None

    # Конвертируем PDF в картинку высокого качества
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=dpi)
        img = pages[0]
    except Exception as e:
        print(f"  Ошибка конвертации страницы в картинку: {e}")
        return None

    # Пересчитываем координаты под DPI картинки
    t_box = (int(time_x0 * scale), int(y0 * scale), int(time_x1 * scale), int(y1 * scale))
    c_box = (int(class_x0 * scale), int(y0 * scale), int(class_x1 * scale), int(y1 * scale))

    # Вырезаем нужные части
    time_img = img.crop(t_box)
    class_img = img.crop(c_box)

    # Склеиваем их горизонтально рядом друг с другом
    final_width = time_img.width + class_img.width
    final_height = time_img.height
    
    combined = Image.new("RGB", (final_width, final_height), color=(255, 255, 255))
    combined.paste(time_img, (0, 0))
    combined.paste(class_img, (time_img.width, 0))

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
            f"📅 <b>Расписание 11А обновлено!</b>\n\n"
            f"День: <b>{day}</b>\n"
            f"Время: {now}\n\n"
            f"🔗 <a href=\"{link}\">Открыть оригинал</a>"
        )
        print(f"  → Генерация компактного расписания 11А для: {day}")
        
        # Пробуем вырезать колонку 11А + звонки
        img_bytes = generate_11a_image(file_bytes, dpi=150)

        if img_bytes:
            print(f"    Размер вырезанного фото: {len(img_bytes) // 1024} КБ, отправляю...")
            send_image(img_bytes, caption)
        else:
            print(f"    Не удалось найти 11А, отправляю всю страницу целиком...")
            img_bytes = pdf_to_single_image(file_bytes, dpi=150)
            if img_bytes:
                send_image(img_bytes, caption)
            else:
                send_telegram(caption)

    save_hashes(new_hashes)

    if not changed:
        print("Изменений нет.")
    else:
        print(f"Итого обновлений: {len(changed)}")

if __name__ == "__main__":
    main()
