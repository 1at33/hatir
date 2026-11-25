"""Ağsız Gemini istemcisi ve sağlayıcı seçimi testleri."""
import io
import json
import urllib.error

import pytest

from hatir.anlam import anlam, gemini, nebius

SAHTE_ANAHTAR = 'AIzaSAHTE-anahtar-degeri-123'


class Cevap(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_istek_bicimi_belge_ve_sorgu(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', SAHTE_ANAHTAR)
    yakalanan = {}

    def sahte_urlopen(request, timeout):
        yakalanan['url'] = request.full_url
        yakalanan['baslik'] = dict(request.header_items())
        yakalanan['govde'] = json.loads(request.data.decode('utf-8'))
        n = len(yakalanan['govde']['requests'])
        return Cevap(json.dumps({'embeddings': [{'values': [0.1, 0.2]} for _ in range(n)]}).encode())

    monkeypatch.setattr(gemini.urllib.request, 'urlopen', sahte_urlopen)
    vectors, tokens = gemini.embed(['Başlık: Not A\ngövde metni', gemini.SORGU_ONEKI + 'soru?', 'başlıksız'])
    assert vectors == [[0.1, 0.2]] * 3 and tokens > 0
    assert 'models/gemini-embedding-2:batchEmbedContents' in yakalanan['url']
    assert yakalanan['baslik'].get('X-goog-api-key') == SAHTE_ANAHTAR
    reqs = yakalanan['govde']['requests']
    assert reqs[0]['content']['parts'][0]['text'] == 'title: Not A | text: gövde metni'
    assert reqs[1]['content']['parts'][0]['text'] == 'task: search result | query: soru?'
    assert reqs[2]['content']['parts'][0]['text'] == 'title: none | text: başlıksız'
    assert all(r['model'] == 'models/gemini-embedding-2' and r['output_dimensionality'] == gemini.BOYUT for r in reqs)


def test_anahtar_yoksa_acik_hata(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(gemini.GeminiHata, match='GEMINI_API_KEY'):
        gemini.embed(['x'])


@pytest.mark.parametrize(('istemci', 'ad', 'hata'), [
    (gemini, 'GEMINI_API_KEY', gemini.GeminiHata),
    (nebius, 'NEBIUS_API_KEY', nebius.NebiusHata),
])
def test_bosluk_anahtari_aga_cikmadan_reddedilir(monkeypatch, istemci, ad, hata):
    monkeypatch.setenv(ad, '   ')

    def ag_yasak(*a, **kw):
        raise AssertionError('ağ çağrısı')

    monkeypatch.setattr(istemci.urllib.request, 'urlopen', ag_yasak)
    with pytest.raises(hata, match=ad):
        istemci.embed(['x'])


def test_gemini_anahtari_bosluklardan_arindirilir(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', '  test-anahtari  ')
    assert gemini.anahtar() == 'test-anahtari'


def test_nebius_anahtari_bosluklardan_arindirilir(monkeypatch):
    monkeypatch.setenv('NEBIUS_API_KEY', '  test-anahtari  ')
    basliklar = []

    def sahte_urlopen(request, timeout):
        basliklar.append(request.get_header('Authorization'))
        return Cevap(b'{"data": [{"index": 0, "embedding": [1.0]}]}')

    monkeypatch.setattr(nebius.urllib.request, 'urlopen', sahte_urlopen)
    nebius.embed(['x'])
    assert basliklar == ['Bearer test-anahtari']


def test_http_hatasinda_anahtar_sizmaz_ve_4xx_tekrarlanmaz(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', SAHTE_ANAHTAR)
    cagri = []

    def sahte_urlopen(request, timeout):
        cagri.append(1)
        raise urllib.error.HTTPError(request.full_url, 400, 'bad ' + SAHTE_ANAHTAR, {}, None)

    monkeypatch.setattr(gemini.urllib.request, 'urlopen', sahte_urlopen)
    with pytest.raises(gemini.GeminiHata) as exc:
        gemini.embed(['x'], bekle=0)
    assert SAHTE_ANAHTAR not in str(exc.value) and len(cagri) == 1


def test_429_ve_5xx_yeniden_denenir_sonra_vazgecer(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', SAHTE_ANAHTAR)
    monkeypatch.setattr(gemini.time, 'sleep', lambda s: None)
    kodlar = iter([429, 503])

    def sahte_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, next(kodlar), 'x', {}, None)

    monkeypatch.setattr(gemini.urllib.request, 'urlopen', sahte_urlopen)
    with pytest.raises(gemini.GeminiHata, match='2 denemede başarısız: HTTP 503'):
        gemini.embed(['x'], deneme=2)


def test_ag_hatasi_ve_gecersiz_cevap(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', SAHTE_ANAHTAR)
    monkeypatch.setattr(gemini.time, 'sleep', lambda s: None)

    def kopuk(request, timeout):
        raise urllib.error.URLError('yok')

    monkeypatch.setattr(gemini.urllib.request, 'urlopen', kopuk)
    with pytest.raises(gemini.GeminiHata, match='ağ hatası'):
        gemini.embed(['x'], deneme=1)
    monkeypatch.setattr(gemini.urllib.request, 'urlopen', lambda request, timeout: Cevap(b'{"embeddings": [{}]}'))
    with pytest.raises(gemini.GeminiHata, match='geçersiz cevap'):
        gemini.embed(['x'])
    monkeypatch.setattr(gemini.urllib.request, 'urlopen', lambda request, timeout: Cevap(b'{"embeddings": []}'))
    with pytest.raises(gemini.GeminiHata, match='vektör döndü'):
        gemini.embed(['x'], deneme=1)


def test_parti_siniri():
    with pytest.raises(gemini.GeminiHata, match='100'):
        gemini.embed(['x'] * 101)


def test_saglayici_secimi_model_onek_onbellek_ayri(monkeypatch, tmp_path):
    monkeypatch.delenv('HATIR_SAGLAYICI', raising=False)
    anlam.saglayici_hazirla()
    assert anlam.MODEL == ''
    assert anlam.onbellek_yolu(tmp_path).name == 'vektorler.json'
    monkeypatch.setenv('HATIR_SAGLAYICI', 'gemini')
    try:
        anlam.saglayici_hazirla()
        assert anlam.MODEL == gemini.MODEL
        assert anlam.SORGU_TALIMAT == gemini.SORGU_ONEKI
        assert anlam.onbellek_yolu(tmp_path).name == 'vektorler-gemini.json'
        monkeypatch.delenv('GEMINI_API_KEY', raising=False)
        with pytest.raises(gemini.GeminiHata, match='GEMINI_API_KEY'):
            anlam.varsayilan_embed(['x'])
    finally:
        monkeypatch.delenv('HATIR_SAGLAYICI', raising=False)
        anlam.saglayici_hazirla()


def test_bilinmeyen_saglayici_reddedilir(monkeypatch):
    monkeypatch.setenv('HATIR_SAGLAYICI', 'yok-boyle')
    try:
        with pytest.raises(ValueError, match='HATIR_SAGLAYICI'):
            anlam.saglayici_hazirla()
    finally:
        monkeypatch.delenv('HATIR_SAGLAYICI', raising=False)
        anlam.saglayici_hazirla()


def test_sir_kalipli_sorgu_uzaga_gitmez():
    cagri = []

    def sahte(texts, **kw):
        cagri.append(texts)
        return [[1.0]], 1
    sorgu = 'şu anahtar ' + 'gh' + 'p_' + 'A1b2C3d4E5f6G7h8J9k0'
    with pytest.raises(RuntimeError, match='sır kalıbı'):
        anlam.sorgu_vektoru(sorgu, sahte)
    assert cagri == []


# --- uret: bağlam cümlesi üretimi ----------------------------------------------------------

def test_uret_basari_bos_cevap_ve_istek(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', SAHTE_ANAHTAR)
    yakalanan = {}

    def sahte_urlopen(request, timeout):
        yakalanan['url'] = request.full_url
        yakalanan['govde'] = json.loads(request.data.decode('utf-8'))
        return Cevap(json.dumps({'candidates': [{'content': {'parts': [{'text': '  iki\n satır  '}]}}]}).encode())

    monkeypatch.setattr(gemini.urllib.request, 'urlopen', sahte_urlopen)
    assert gemini.uret('istem') == 'iki satır'
    assert gemini.URET_MODEL + ':generateContent' in yakalanan['url']
    assert yakalanan['govde']['contents'][0]['parts'][0]['text'] == 'istem'
    assert yakalanan['govde']['generationConfig'] == {'temperature': 0.2, 'maxOutputTokens': 160}
    monkeypatch.setattr(gemini.urllib.request, 'urlopen', lambda r, timeout: Cevap(b'{"candidates": []}'))
    assert gemini.uret('istem') == ''  # filtrelenmiş/boş cevap hata değil


def test_uret_hatalar_anahtar_sizmaz(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', SAHTE_ANAHTAR)
    bekleme = []
    monkeypatch.setattr(gemini.time, 'sleep', bekleme.append)

    def kotu(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, SAHTE_ANAHTAR, {}, None)
    monkeypatch.setattr(gemini.urllib.request, 'urlopen', kotu)
    with pytest.raises(gemini.GeminiHata) as exc:
        gemini.uret('x')
    assert 'HTTP 403' in str(exc.value) and SAHTE_ANAHTAR not in str(exc.value)

    kodlar = iter([(429, {'retry-after': '3'}), (503, {})])

    def sunucu(request, timeout):
        kod, basliklar = next(kodlar)
        raise urllib.error.HTTPError(request.full_url, kod, 'x', basliklar, None)
    monkeypatch.setattr(gemini.urllib.request, 'urlopen', sunucu)
    with pytest.raises(gemini.GeminiHata, match='2 denemede başarısız: HTTP 503'):
        gemini.uret('x', deneme=2)
    assert bekleme[0] == 3.0  # retry-after başlığına uyar

    def kopuk(request, timeout):
        raise urllib.error.URLError('yok')
    monkeypatch.setattr(gemini.urllib.request, 'urlopen', kopuk)
    with pytest.raises(gemini.GeminiHata, match='ağ hatası'):
        gemini.uret('x', deneme=1)
    monkeypatch.setattr(gemini.urllib.request, 'urlopen', lambda r, timeout: Cevap(b'{bozuk'))
    with pytest.raises(gemini.GeminiHata, match='geçersiz cevap'):
        gemini.uret('x')
