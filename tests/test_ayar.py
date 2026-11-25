"""ayar.toml [gomme]: sağlayıcı yalnız kullanıcının dosyasından gelir, anahtar değeri reddedilir."""
import json
import sys
from pathlib import Path

import pytest

from hatir.anlam import anlam, uyumlu
from hatir.ayar import gomme_adi, gomme_coz, varsayilan_dosya


def _yaz(tmp_path, metin):
    dosya = tmp_path / "ayar.toml"
    dosya.write_text(metin, encoding="utf-8")
    return dosya


def test_dosya_yoksa_gomme_yok(tmp_path):
    assert gomme_coz(env={}, ev=tmp_path) is None
    assert gomme_adi(env={}, ev=tmp_path) is None


def test_varsayilan_yol(tmp_path):
    yol = varsayilan_dosya(tmp_path, {"XDG_CONFIG_HOME": str(tmp_path / "x"), "APPDATA": str(tmp_path / "a")})
    beklenen = (tmp_path / "a") if sys.platform == "win32" else (tmp_path / "x")
    assert yol == beklenen / "hatir" / "ayar.toml"


def test_env_dosyasi_ve_bos_deger(tmp_path):
    dosya = _yaz(tmp_path, '[gomme]\nsaglayici = "gemini"\n')
    assert gomme_adi(env={"HATIR_AYAR": str(dosya)}, ev=tmp_path) == "gemini"
    with pytest.raises(ValueError, match="HATIR_AYAR"):
        gomme_coz(env={"HATIR_AYAR": "  "}, ev=tmp_path)


@pytest.mark.parametrize(("gomme", "ad"), [
    ('saglayici = "gemini"\n', "GEMINI_API_KEY"),
    ('saglayici = "nebius"\n', "NEBIUS_API_KEY"),
    ('saglayici = "uyumlu"\nmodel = "m"\ntaban_url = "https://ornek.example/v1"\nanahtar_env = "ORNEK_KEY"\n', "ORNEK_KEY"),
    ('saglayici = "uyumlu"\nmodel = "m"\ntaban_url = "https://ornek.example/v1"\n', ""),
    ('saglayici = "nebius"\nmodel = "m"\ntaban_url = "http://127.0.0.1:1/v1"\n', ""),
])
def test_anahtar_adi(tmp_path, gomme, ad):
    assert gomme_coz(dosya=_yaz(tmp_path, "[gomme]\n" + gomme), env={}, ev=tmp_path).anahtar_adi() == ad


def test_yok_gomme_kapali(tmp_path):
    assert gomme_adi(dosya=_yaz(tmp_path, '[gomme]\nsaglayici = "yok"\n'), env={}, ev=tmp_path) is None


@pytest.mark.parametrize(("metin", "mesaj"), [
    ('[[saglayici]]\nad = "a"\n', "bilinmeyen ayar anahtarı"),
    ('[gomme]\nsaglayici = "uyumlu"\nmodel = "m"\n', "taban_url gerekli"),
    ('[gomme]\nsaglayici = "uyumlu"\ntaban_url = "https://ornek.example/v1"\n', "model gerekli"),
    ('[gomme]\nsaglayici = "yok"\nmodel = "m"\n', "başka alan"),
    ('[gomme]\nsaglayici = "gemini"\ntaban_url = "https://ornek.example/v1"\n', "taban_url almaz"),
    ('[gomme]\nsaglayici = "gemini"\nanahtar_env = "BASKA"\n', "GEMINI_API_KEY"),
    ('[gomme]\nsaglayici = "gemini"\nsifre = "x"\n', "bilinmeyen gömme"),
    (('[gomme]\nsaglayici = "uyumlu"\nmodel = "m"\ntaban_url = "https://ornek.example/v1"\n'
      'anahtar_env = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"\n'), "anahtar"),
    ('[gomme\n', "bozuk"),
])
def test_gecersiz_ayar_reddedilir(tmp_path, metin, mesaj):
    with pytest.raises(ValueError, match=mesaj):
        gomme_coz(dosya=_yaz(tmp_path, metin), env={}, ev=tmp_path)


def test_gomme_ayardan_secilir(monkeypatch, tmp_path):
    dosya = _yaz(tmp_path, '[gomme]\nsaglayici = "uyumlu"\nmodel = "embed-ornek"\n'
                 'taban_url = "https://ornek.example/v1"\nanahtar_env = "ORNEK_API_KEY"\n')
    monkeypatch.setenv("HATIR_AYAR", str(dosya))
    anlam.saglayici_hazirla(zorunlu=True)
    assert (anlam.SAGLAYICI, anlam.MODEL, anlam.SORGU_TALIMAT) == ("uyumlu", "embed-ornek", "")


def test_saglayici_yoksa_zorunlu_hata():
    with pytest.raises(ValueError, match="gömme sağlayıcısı yok"):
        anlam.saglayici_hazirla(zorunlu=True)


def test_uyumlu_embed_anahtari_okur_degeri_yazmaz(monkeypatch):
    yakalanan = {}

    class Cevap:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": [0.5]}],
                               "usage": {"total_tokens": 3}}).encode()

    def urlopen(request, timeout):
        yakalanan["url"] = request.full_url
        yakalanan["auth"] = request.get_header("Authorization")
        return Cevap()

    monkeypatch.setenv("ORNEK_API_KEY", "sahte-deger")
    monkeypatch.setattr(uyumlu.urllib.request, "urlopen", urlopen)
    vektor, token = uyumlu.embed(["metin"], taban_url="https://ornek.example/v1",
                                 model="embed-ornek", anahtar_env="ORNEK_API_KEY", deneme=1)
    assert vektor == [[0.5]] and token == 3
    assert yakalanan["url"] == "https://ornek.example/v1/embeddings"
    assert yakalanan["auth"] == "Bearer sahte-deger"
    assert "sahte-deger" not in Path(uyumlu.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize(("url", "gecerli"), [
    ("https://ornek.example/v1", True),
    ("http://127.0.0.1:11434/v1", True),
    ("http://localhost/v1", True),
    ("http://ornek.example/v1", False),
    ("ftp://ornek.example/v1", False),
    ("https:///yol", False),
])
def test_taban_url_https_ister(tmp_path, url, gecerli):
    metin = f'[gomme]\nsaglayici = "uyumlu"\nmodel = "m"\ntaban_url = "{url}"\n'
    if gecerli:
        assert gomme_coz(dosya=_yaz(tmp_path, metin), env={}, ev=tmp_path).taban_url == url
    else:
        with pytest.raises(ValueError, match="https"):
            gomme_coz(dosya=_yaz(tmp_path, metin), env={}, ev=tmp_path)


def test_ortamdan_gelen_url_de_denetlenir(monkeypatch):
    monkeypatch.setenv("HATIR_SAGLAYICI", "uyumlu")
    monkeypatch.setenv("HATIR_MODEL", "m")
    monkeypatch.setenv("HATIR_URL", "http://ornek.example/v1")
    with pytest.raises(ValueError, match="https"):
        anlam.saglayici_hazirla(zorunlu=True)
