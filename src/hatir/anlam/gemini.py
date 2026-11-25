"""Gemini API embedding istemcisi (yalnız standart kütüphane).

Anahtar yalnız `GEMINI_API_KEY` ortam değişkeninden okunur;
hiçbir hata metnine, loga ya da çıktıya yazılmaz.
"""
import json
import os
import time
import urllib.error
import urllib.request

from hatir.ayar import GOMME_ANAHTAR_ENV

MODEL = 'gemini-embedding-2'
URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents'
BOYUT = 1536          # 768/1536/3072 önerilir; gemini-embedding-2 kesilen boyutu kendisi normalize eder
PARTI_SINIRI = 100    # batchEmbedContents tek istekte en çok 100 metin
SORGU_ONEKI = 'task: search result | query: '


class GeminiHata(RuntimeError):
    """Anahtar ya da istek başlığı içermeyen hata."""


def anahtar():
    ad = GOMME_ANAHTAR_ENV['gemini']
    key = os.environ.get(ad, '').strip()
    if key:
        return key
    raise GeminiHata(f'{ad} ortam değişkeni tanımlı değil')


def belge_metni(text):
    """Parça `Başlık: X\\n...` biçimindeyse Gemini belge kalıbına çevrilir."""
    first, _, rest = text.partition('\n')
    if first.startswith('Başlık: ') and rest:
        return f'title: {first[len("Başlık: "):].strip()} | text: {rest}'
    return f'title: none | text: {text}'


def tahmini_token(texts):
    # batchEmbedContents kullanım sayısı döndürmez; ~4 karakter = 1 token kaba tahmini.
    return sum(len(t) for t in texts) // 4


def embed(texts, model=MODEL, timeout=60, deneme=5, bekle=1.0, boyut=BOYUT, sinir_bekle=20.0):
    """texts -> (vektörler, tahmini token). Sorgu metni SORGU_ONEKI ile gelir, belge ham gelir."""
    texts = list(texts)
    if len(texts) > PARTI_SINIRI:
        raise GeminiHata(f'tek istekte en çok {PARTI_SINIRI} metin')
    key = anahtar()
    body = json.dumps({'requests': [
        {'model': f'models/{model}',
         'content': {'parts': [{'text': t if t.startswith(SORGU_ONEKI) else belge_metni(t)}]},
         'output_dimensionality': boyut}
        for t in texts]}).encode('utf-8')
    last = 'bilinmeyen hata'
    for attempt in range(deneme):
        request = urllib.request.Request(
            URL.format(model=model), data=body, method='POST',
            headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
            vectors = [item['values'] for item in data['embeddings']]
            if len(vectors) != len(texts):
                raise GeminiHata(f'{len(texts)} metne {len(vectors)} vektör döndü')
            return vectors, tahmini_token(texts)
        except urllib.error.HTTPError as exc:
            last = f'HTTP {exc.code}'
            if exc.code != 429 and exc.code < 500:
                raise GeminiHata(last) from None
            if exc.code == 429 and attempt + 1 < deneme:
                time.sleep(sinir_bekle * (attempt + 1))  # dakikalık kota dolunca bekle
                continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f'ağ hatası: {type(exc).__name__}'
        except (ValueError, KeyError, TypeError) as exc:
            raise GeminiHata(f'geçersiz cevap: {type(exc).__name__}') from None
        if attempt + 1 < deneme:
            time.sleep(bekle * 2 ** attempt)
    raise GeminiHata(f'{deneme} denemede başarısız: {last}')


URET_MODEL = 'gemini-3.5-flash-lite'   # 2.5-flash-lite yeni kullanıcılara kapalı (404, 2026-11-07)
URET_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def uret(istem, model=URET_MODEL, timeout=60, deneme=4, max_token=160, sicaklik=0.2):
    """Kısa metin üretimi (parça bağlam cümlesi). Boş/filtrelenmiş cevapta '' döner; anahtar hiçbir hataya yazılmaz."""
    key = anahtar()
    body = json.dumps({'contents': [{'parts': [{'text': istem}]}],
                       'generationConfig': {'temperature': sicaklik, 'maxOutputTokens': max_token}}).encode('utf-8')
    last = 'bilinmeyen hata'
    for attempt in range(deneme):
        request = urllib.request.Request(URET_URL.format(model=model), data=body, method='POST',
                                         headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
            try:
                return ' '.join(data['candidates'][0]['content']['parts'][0]['text'].split())
            except (KeyError, IndexError, TypeError):
                return ''
        except urllib.error.HTTPError as exc:
            last = f'HTTP {exc.code}'
            if exc.code != 429 and exc.code < 500:
                raise GeminiHata(last) from None
            ra = exc.headers.get('retry-after') if exc.headers else None
            # Sunucunun istediği bekleme 60 saniyeyle sınırlı: kötü bir başlık işlemi saatlerce kilitlemesin.
            time.sleep(min(float(ra), 60.0) if ra and ra.isdigit() else 2 ** attempt + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f'ağ hatası: {type(exc).__name__}'
            time.sleep(2 ** attempt)
        except ValueError as exc:
            raise GeminiHata(f'geçersiz cevap: {type(exc).__name__}') from None
    raise GeminiHata(f'{deneme} denemede başarısız: {last}')
