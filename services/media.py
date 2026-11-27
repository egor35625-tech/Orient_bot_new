import asyncio
import io
import os
import uuid
from fastapi import HTTPException, UploadFile

from config import UPLOAD_DIR

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _looks_like_image(head: bytes, ext: str) -> bool:
    if head.startswith(b"\xff\xd8\xff"):
        return ext in (".jpg", ".jpeg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ext == ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ext == ".webp"
    return False


async def save_image(file: UploadFile, subdir: str, max_bytes: int = 8 * 1024 * 1024, db=None) -> str:
    """Сохраняет картинку в БД и возвращает путь вида 'uploads/<subdir>/<имя>' (для БД и URL).
    Если передан db (сессия запроса), запись идёт в ту же транзакцию: откат запроса откатывает и фото,
    а отдельная сессия не конфликтует с открытой записью (SQLite: «database is locked»)."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Допустимы только фото JPG, PNG или WEBP")
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(400, f"Фото слишком большое (максимум {max_bytes // (1024 * 1024)} МБ)")
    if not _looks_like_image(content[:16], ext):
        raise HTTPException(400, "Файл не похож на изображение")
    content, ext = await asyncio.to_thread(_compress, content, ext)
    path = f"uploads/{subdir}/{uuid.uuid4().hex}{ext}"
    from models import MediaFile
    row = MediaFile(path=path, content=content, content_type=CONTENT_TYPES[ext])
    if db is not None:
        db.add(row)
        return path
    from database import async_session
    async with async_session() as s:
        s.add(row)
        await s.commit()
    return path


CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _norm(path: str) -> str:
    rel = path.lstrip("/")
    return rel if rel.startswith("uploads/") else f"uploads/{rel}"


async def read_media(path: str | None) -> tuple[bytes, str] | None:
    """Возвращает (байты, content-type) из БД; для старых файлов — с диска."""
    if not path:
        return None
    from database import async_session
    from models import MediaFile
    async with async_session() as s:
        row = await s.get(MediaFile, _norm(path))
        if row:
            return row.content, row.content_type
    full = _disk_path(path)
    if full and os.path.isfile(full):
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            return f.read(), CONTENT_TYPES.get(ext, "application/octet-stream")
    return None


MAX_SIDE = 1600


def _compress(content: bytes, ext: str) -> tuple[bytes, str]:
    """Уменьшает фото до 1600 px по длинной стороне и пережимает (JPEG, а с прозрачностью — WebP).
    Если Pillow не установлен, возвращает исходный файл без изменений."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return content, ext
    Image.MAX_IMAGE_PIXELS = 40_000_000  # защита от «бомб»: больший размер -> ошибка -> 400
    try:
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img.convert("RGBA").save(out, "WEBP", quality=82, method=4)
            return out.getvalue(), ".webp"
        img.convert("RGB").save(out, "JPEG", quality=82, optimize=True, progressive=True)
        return out.getvalue(), ".jpg"
    except Exception:
        raise HTTPException(400, "Файл не похож на изображение")


def _disk_path(path: str) -> str | None:
    """Путь к старому файлу на диске (до перехода на хранение в БД); выход за UPLOAD_DIR запрещён."""
    rel = _norm(path)[len("uploads/"):]
    full = os.path.realpath(os.path.join(UPLOAD_DIR, rel))
    root = os.path.realpath(UPLOAD_DIR)
    return full if full.startswith(root + os.sep) else None


_pending: set = set()


async def _delete_async(path: str) -> None:
    from database import async_session
    from models import MediaFile
    try:
        async with async_session() as s:
            row = await s.get(MediaFile, _norm(path))
            if row:
                await s.delete(row)
                await s.commit()
    except Exception:
        pass
    full = _disk_path(path)
    if full:
        try:
            os.remove(full)
        except OSError:
            pass


def delete_media(path: str | None) -> None:
    """Удаляет изображение (из БД и, если осталось от старой версии, с диска). Вызывать из async-кода."""
    if not path:
        return
    task = asyncio.get_running_loop().create_task(_delete_async(path))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
