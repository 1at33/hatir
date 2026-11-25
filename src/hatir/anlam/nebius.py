"""Nebius Token Factory embedding istemcisi (yalnız standart kütüphane)."""
import json
import os
import time
import urllib.error
import urllib.request

from hatir.ayar import GOMME_ANAHTAR_ENV

URL = 'https://api.tokenfactory.nebius.com/v1/embeddings'
MODEL = 'Qwen/Qwen3-Embedding-8B'


class NebiusHata(RuntimeError):
    """Anahtar ya da istek başlığı içermeyen hata."""


def embed(texts, model=MODEL, timeout=60, deneme=3, bekle=1.0):
    """texts -> (vektörler, kullanılan token). Sıra `data[i].index`e göre kurulur."""
    ad = GOMME_ANAHTAR_ENV['nebius']
    key = os.environ.get(ad, '').strip()
    if not key:
        raise NebiusHata(f'{ad} ortam değişkeni tanımlı değil')
    body = json.dumps({'model': model, 'input': list(texts)}).encode('utf-8')
    last = 'bilinmeyen hata'
    for attempt in range(deneme):
        request = urllib.request.Request(
            URL, data=body, method='POST',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
            items = sorted(data['data'], key=lambda item: item['index'])
            vectors = [item['embedding'] for item in items]
            if len(vectors) != len(texts):
                raise NebiusHata(f'{len(texts)} metne {len(vectors)} vektör döndü')
            return vectors, int((data.get('usage') or {}).get('total_tokens')
                                or (data.get('usage') or {}).get('prompt_tokens') or 0)
        except urllib.error.HTTPError as exc:
            last = f'HTTP {exc.code}'
            if exc.code != 429 and exc.code < 500:
                raise NebiusHata(last) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f'ağ hatası: {type(exc).__name__}'
        except (ValueError, KeyError, TypeError) as exc:
            raise NebiusHata(f'geçersiz cevap: {type(exc).__name__}') from None
        if attempt + 1 < deneme:
            time.sleep(bekle * 2 ** attempt)
    raise NebiusHata(f'{deneme} denemede başarısız: {last}')
