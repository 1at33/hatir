"""OpenAI uyumlu embeddings istemcisi (standart kütüphane).

POST {taban_url}/embeddings gövdesi {"model", "input"}.
Anahtar varsa Authorization: Bearer <os.environ[anahtar_env]>.
Değer diske yazılmaz; yoksa başlık konmaz (yerel sunucu).
"""
import json
import os
import time
import urllib.error
import urllib.request


class UyumluHata(RuntimeError):
    """Anahtar ya da istek başlığı içermeyen hata."""


def embed(texts, *, taban_url, model, anahtar_env="", timeout=60, deneme=3, bekle=1.0):
    """texts -> (vektörler, kullanılan token). Sıra data[i].index'e göredir."""
    if not taban_url or not model:
        raise UyumluHata("taban_url ve model gerekli")
    headers = {"Content-Type": "application/json"}
    if anahtar_env:
        key = os.environ.get(anahtar_env)
        if not key:
            raise UyumluHata(f"{anahtar_env} ortam değişkeni tanımlı değil")
        headers["Authorization"] = f"Bearer {key}"
    url = taban_url.rstrip("/") + "/embeddings"
    body = json.dumps({"model": model, "input": list(texts)}).encode("utf-8")
    last = "bilinmeyen hata"
    for attempt in range(deneme):
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            items = sorted(data["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in items]
            if len(vectors) != len(texts):
                raise UyumluHata(f"{len(texts)} metne {len(vectors)} vektör döndü")
            usage = data.get("usage") or {}
            token = int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0)
            return vectors, token
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code != 429 and exc.code < 500:
                raise UyumluHata(last) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"ağ hatası: {type(exc).__name__}"
        except UyumluHata:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise UyumluHata(f"geçersiz cevap: {type(exc).__name__}") from None
        if attempt + 1 < deneme:
            time.sleep(bekle * 2 ** attempt)
    raise UyumluHata(f"{deneme} denemede başarısız: {last}")
