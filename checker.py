import hashlib
import json
import os
import io
import requests
from datetime import datetime, timezone, timedelta
from pdf2image import convert_from_bytes
from PIL import Image
import pdfplumber  # <-- Добавлена библиотека для работы с PDF-таблицами

# ──────────────────────────────────────────────
# Извлечение расписания конкретного класса из PDF
# ──────────────────────────────────────────────
def extract_class_schedule(pdf_bytes: bytes, target_class: str = "11А") -> str | None:
    """
    Извлекает расписание для указанного класса из PDF.
    """
    schedule_text = []
    
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    
                    # Ищем индекс колонки / строки с нашим классом
                    # Очищаем заголовки от пробелов и переносов
                    target_col_idx = -1
                    header_row_idx = -1
                    
                    for r_idx, row in enumerate(table):
                        for c_idx, cell in enumerate(row):
                            if cell and target_class.lower() in str(cell).replace(" ", "").lower():
                                header_row_idx = r_idx
                                target_col_idx = c_idx
                                break
                        if target_col_idx != -1:
                            break
                    
                    # Если нашли колоночный заголовок класса
                    if target_col_idx != -1:
                        for row in table[header_row_idx + 1:]:
                            lesson_num = row[0] if len(row) > 0 and row[0] else ""
                            lesson_name = row[target_col_idx] if len(row) > target_col_idx and row[target_col_idx] else ""
                            
                            if lesson_name and lesson_name.strip():
                                # Чистим от лишних переносов строк
                                clean_lesson = " ".join(lesson_name.split())
                                clean_num = " ".join(str(lesson_num).split())
                                
                                if clean_num:
                                    schedule_text.append(f"<b>{clean_num}.</b> {clean_lesson}")
                                else:
                                    schedule_text.append(f"• {clean_lesson}")
                                    
        if schedule_text:
            return "\n".join(schedule_text)
            
    except Exception as e:
        print(f"Ошибка при парсинге PDF: {e}")
        
    return None

# ──────────────────────────────────────────────
# Изменения в функции main()
# ──────────────────────────────────────────────
# В цикле отправки `for day, file_id, file_bytes in changed:` замените блок на такой:

    for day, file_id, file_bytes in changed:
        link = DRIVE_VIEW.format(file_id)
        
        # 1. Пробуем вытащить уроки 11А класса
        class_schedule = extract_class_schedule(file_bytes, target_class="11А")
        
        if class_schedule:
            text_caption = (
                f"📅 <b>Расписание для 11А ({day})</b>\n"
                f"🕒 Время обновления: {now}\n\n"
                f"{class_schedule}\n\n"
                f"🔗 <a href=\"{link}\">Открыть весь файл</a>"
            )
        else:
            text_caption = (
                f"📅 <b>Расписание обновлено!</b>\n\n"
                f"День: <b>{day}</b>\n"
                f"Время: {now}\n\n"
                f"<i>(Не удалось автоматически извлечь 11А)</i>\n"
                f"🔗 <a href=\"{link}\">Открыть оригинал</a>"
            )
            
        print(f"  → Конвертирую PDF в изображение: {day}")
        img_bytes = pdf_to_single_image(file_bytes, dpi=150)

        if img_bytes:
            print(f"      Отправляю фото + текст...")
            send_image(img_bytes, text_caption)
        else:
            print(f"      Отправляю только текст...")
            send_telegram(text_caption)
