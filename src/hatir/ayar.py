"""Gömme (embedding) sağlayıcısı: yalnız kullanıcının ayar.toml dosyasından gelir.

Dosya: HATIR_AYAR (dosya yolu) > $XDG_CONFIG_HOME/hatir/ayar.toml > ~/.config/hatir/ayar.toml.
Windows: %APPDATA%\\hatir\\ayar.toml. Standart kütüphane (tomllib); bağımlılık yok.

Dosyada yalnız [gomme] ve [yeniden_siralama] tabloları olur; bilinmeyen anahtar ValueError'dur.
"yok" o özelliği kapatır. gemini ve nebius hazır gömme istemcisidir; başka ad + taban_url OpenAI uyumlu
/embeddings ucunu kullanır. [yeniden_siralama] her zaman OpenAI uyumlu /chat/completions ucudur (hazır sağlayıcı,
varsayılan model yoktur). Anahtar değeri yazılmaz, yalnız ortam değişkeninin adı (anahtar_env).
"""
import math
import os
import re
import sys
import tomllib
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

IZINLI = frozenset({"gomme", "yeniden_siralama"})
GOMME_ANAHTAR_ENV = {"gemini": "GEMINI_API_KEY", "nebius": "NEBIUS_API_KEY"}
_GOMME_ALAN = frozenset({"saglayici", "model", "taban_url", "anahtar_env"})
_ENV_AD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KALIP = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-|ghp_|gho_|ghs_|ghu_|github_pat_|xoxb-|xoxp-|xoxa-|glpat-)[A-Za-z0-9_-]{16,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [^\r\n]*PRIVATE KEY-----"
)


def _sir_gibi(deger):
    """Bilinen anahtar kalıbı ya da 32+ karakterlik yüksek entropili harf-rakam dizesi."""
    if _KALIP.search(deger):
        return True
    if len(deger) < 32 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", deger):
        return False
    if not re.search(r"[A-Za-z]", deger) or not re.search(r"[0-9]", deger):
        return False
    n = len(deger)
    return -sum((a / n) * math.log2(a / n) for a in Counter(deger).values()) >= 4.0


@dataclass(frozen=True)
class Gomme:
    """saglayici 'yok' ise gömme yok. gemini/nebius seçilince o istemci; başka ad + taban_url OpenAI uyumlu."""

    saglayici: str
    model: str
    taban_url: str
    anahtar_env: str

    def anahtar_adi(self):
        """Açık ad önceliklidir; uyumlu uçta varsayılan anahtar yoktur."""
        return self.anahtar_env or (GOMME_ANAHTAR_ENV.get(self.saglayici, "") if not self.taban_url else "")


YEREL_SUNUCU = frozenset({"localhost", "127.0.0.1", "::1"})


def url_dogrula(url, alan="taban_url"):
    """Anahtar taşıyan istek düz HTTP'ye gitmesin: https, ya da yalnız yerel sunucuda http."""
    parca = urllib.parse.urlsplit(url)
    if parca.scheme == "https" and parca.hostname:
        return url
    if parca.scheme == "http" and (parca.hostname or "").casefold() in YEREL_SUNUCU:
        return url
    raise ValueError(f"{alan} https olmalı (http yalnız localhost/127.0.0.1/::1 için)")


def varsayilan_dosya(ev, env):
    """Yapılandırma dosyasının varsayılan yolu. Dosyanın varlığı ayrı denetlenir."""
    if sys.platform == "win32":
        kok = Path(env["APPDATA"]) if env.get("APPDATA") else ev / "AppData" / "Roaming"
        return kok / "hatir" / "ayar.toml"
    kok = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else ev / ".config"
    return kok / "hatir" / "ayar.toml"


def _metin(ham, alan, tablo="gomme"):
    if alan not in ham or ham[alan] is None:
        return ""
    deger = ham[alan]
    if not isinstance(deger, str) or not deger.strip():
        raise ValueError(f"{tablo}.{alan} metin olmalı")
    metin = deger.strip()
    if _sir_gibi(metin):
        raise ValueError(f"{tablo}.{alan} anahtar değeri olamaz; yalnız değişkenin adı yazılır")
    return metin


def _ust_denetle(veri):
    if not isinstance(veri, dict):
        raise ValueError("ayar tablo olmalı")  # noqa: TRY004 - yapılandırma sözleşmesi
    ekstra = sorted(set(veri) - IZINLI)
    if ekstra:
        raise ValueError("bilinmeyen ayar anahtarı: " + ", ".join(ekstra))


def gomme_ayikla(veri):
    """[gomme] tablosunu doğrula. Tablo yoksa None."""
    _ust_denetle(veri)
    if "gomme" not in veri:
        return None
    ham = veri["gomme"]
    if not isinstance(ham, dict):
        raise ValueError("gomme tablo olmalı")  # noqa: TRY004 - yapılandırma sözleşmesi
    ekstra = sorted(set(ham) - _GOMME_ALAN)
    if ekstra:
        raise ValueError("bilinmeyen gömme anahtarı: " + ", ".join(ekstra))
    ad = _metin(ham, "saglayici").casefold()
    if not ad:
        raise ValueError("gomme.saglayici metin olmalı")
    model, url, anahtar = _metin(ham, "model"), _metin(ham, "taban_url"), _metin(ham, "anahtar_env")
    if anahtar and not _ENV_AD.fullmatch(anahtar):
        raise ValueError("anahtar_env ortam değişkeninin adı olmalı, değeri değil")
    if ad == "yok":
        if model or url or anahtar:
            raise ValueError("gomme yok ise başka alan yazılmaz")
        return Gomme("yok", "", "", "")
    if ad == "gemini":
        if url:
            raise ValueError("gemini taban_url almaz; başka adres için saglayici ve taban_url yazın")
        if anahtar and anahtar != GOMME_ANAHTAR_ENV[ad]:
            raise ValueError(f"gemini anahtar_env {GOMME_ANAHTAR_ENV[ad]} olmalı")
        return Gomme("gemini", model, "", anahtar)
    if ad == "nebius" and not url:
        if anahtar and anahtar != GOMME_ANAHTAR_ENV[ad]:
            raise ValueError(f"nebius anahtar_env {GOMME_ANAHTAR_ENV[ad]} olmalı")
        return Gomme("nebius", model, "", anahtar)
    if not url:
        raise ValueError(f"gomme {ad}: taban_url gerekli")
    if not model:
        raise ValueError(f"gomme {ad}: model gerekli")
    return Gomme(ad, model, url_dogrula(url), anahtar)


def toml_oku(yol):
    """Ayar dosyasını oku. Okunamayan ya da bozuk dosya ValueError."""
    yol = Path(yol)
    try:
        with yol.open("rb") as dosya:
            return tomllib.load(dosya)
    except FileNotFoundError as exc:
        raise ValueError(f"ayar dosyası yok: {yol}") from exc
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise ValueError(f"ayar.toml bozuk: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"ayar.toml okunamadı: {exc.strerror or type(exc).__name__}") from exc


def _ayar_verisi(dosya=None, env=None, ev=None):
    """Ayar dosyasının içeriği: dosya > HATIR_AYAR > varsayılan. Varsayılan dosya yoksa None."""
    env = os.environ if env is None else env
    ev = Path.home() if ev is None else Path(ev)
    if dosya is None:
        env_dosya = env.get("HATIR_AYAR")
        if env_dosya is not None:
            metin = env_dosya.strip() if isinstance(env_dosya, str) else ""
            if not metin:
                raise ValueError("HATIR_AYAR boş olamaz")
            dosya = Path(metin).expanduser()
        else:
            dosya = varsayilan_dosya(ev, env)
            if not dosya.is_file():
                return None
    return toml_oku(dosya)


def gomme_coz(dosya=None, env=None, ev=None):
    """Gömme ayarını çöz: dosya > HATIR_AYAR > varsayılan. Dosya ya da [gomme] yoksa None."""
    veri = _ayar_verisi(dosya, env, ev)
    return None if veri is None else gomme_ayikla(veri)


def gomme_adi(dosya=None, env=None, ev=None):
    """Seçili gömme sağlayıcısının adı; yoksa ya da 'yok' ise None."""
    gomme = gomme_coz(dosya, env, ev)
    if gomme is None or gomme.saglayici in ("", "yok"):
        return None
    return gomme.saglayici


# --- Yeniden sıralama (rerank) ------------------------------------------------------------

_SIRALAMA_ALAN = frozenset({"saglayici", "model", "taban_url", "anahtar_env"})


@dataclass(frozen=True)
class YenidenSiralama:
    """OpenAI uyumlu sohbet ucu: POST {taban_url}/chat/completions. saglayici yalnız etikettir."""

    saglayici: str
    model: str
    taban_url: str
    anahtar_env: str

    @property
    def kapali(self):
        return self.saglayici == "yok"


def yeniden_siralama_ayikla(veri):
    """[yeniden_siralama] tablosunu doğrula. Tablo yoksa None; saglayici "yok" ise kapalı kayıt."""
    _ust_denetle(veri)
    if "yeniden_siralama" not in veri:
        return None
    ham = veri["yeniden_siralama"]
    tablo = "yeniden_siralama"
    if not isinstance(ham, dict):
        raise ValueError(f"{tablo} tablo olmalı")  # noqa: TRY004 - yapılandırma sözleşmesi
    ekstra = sorted(set(ham) - _SIRALAMA_ALAN)
    if ekstra:
        raise ValueError("bilinmeyen yeniden sıralama anahtarı: " + ", ".join(ekstra))
    ad = _metin(ham, "saglayici", tablo).casefold()
    if not ad:
        raise ValueError(f"{tablo}.saglayici metin olmalı")
    model = _metin(ham, "model", tablo)
    url = _metin(ham, "taban_url", tablo)
    anahtar = _metin(ham, "anahtar_env", tablo)
    if anahtar and not _ENV_AD.fullmatch(anahtar):
        raise ValueError("anahtar_env ortam değişkeninin adı olmalı, değeri değil")
    if ad == "yok":
        if model or url or anahtar:
            raise ValueError("yeniden_siralama yok ise başka alan yazılmaz")
        return YenidenSiralama("yok", "", "", "")
    if not url:
        raise ValueError(f"yeniden_siralama {ad}: taban_url gerekli")
    if not model:
        raise ValueError(f"yeniden_siralama {ad}: model gerekli")
    return YenidenSiralama(ad, model, url_dogrula(url, f"{tablo}.taban_url"), anahtar)


def yeniden_siralama_coz(dosya=None, env=None, ev=None):
    """Yeniden sıralama ayarı: dosya > HATIR_AYAR > varsayılan. Dosya ya da tablo yoksa None."""
    veri = _ayar_verisi(dosya, env, ev)
    return None if veri is None else yeniden_siralama_ayikla(veri)
