"""Перевод субтитров через Google Translate (бесплатный веб-доступ, нужен интернет)."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

LANGUAGES = [
    ("en", "Английский"), ("ru", "Русский"), ("uk", "Украинский"), ("de", "Немецкий"), ("fr", "Французский"),
    ("es", "Испанский"), ("it", "Итальянский"), ("pt", "Португальский"), ("pl", "Польский"), ("tr", "Турецкий"),
    ("kk", "Казахский"), ("uz", "Узбекский"), ("be", "Белорусский"), ("ar", "Арабский"), ("hi", "Хинди"),
    ("zh-CN", "Китайский"), ("ja", "Японский"), ("ko", "Корейский"), ("id", "Индонезийский"), ("vi", "Вьетнамский"),
]

URL = "https://translate.googleapis.com/translate_a/single"
MAX_CHARS = 3500  # длина одного запроса


class TranslateError(Exception):
    pass


def _request(text: str, target: str, source: str = "auto") -> str:
    data = urllib.parse.urlencode({"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text}).encode()
    req = urllib.request.Request(URL, data=data, headers={
        "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8"))
            return "".join(part[0] for part in payload[0] if part and part[0])
        except Exception as e:  # noqa: BLE001 — сеть, лимиты, неожиданный ответ
            last_error = e
            time.sleep(1.5 * (attempt + 1))
    raise TranslateError(f"Google Translate не ответил: {last_error}")


def translate_lines(lines: list[str], target: str, source: str = "auto", progress=None) -> list[str]:
    """Переводит список строк. Строки склеиваются в запросы по переносу строки;
    если Google вернул другое число строк — эта пачка переводится построчно."""
    flat = [l.replace("\n", " ").strip() for l in lines]
    out: list[str] = [""] * len(flat)
    batches, cur, size = [], [], 0
    for i, line in enumerate(flat):
        if cur and size + len(line) + 1 > MAX_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(line) + 1
    if cur:
        batches.append(cur)
    done = 0
    for batch in batches:
        texts = [flat[i] for i in batch]
        result = _request("\n".join(texts), target, source).split("\n")
        if len(result) != len(batch):
            result = [_request(t, target, source) if t else "" for t in texts]
        for i, r in zip(batch, result):
            out[i] = r.strip()
        done += len(batch)
        if progress:
            progress(done, len(flat))
    return out
