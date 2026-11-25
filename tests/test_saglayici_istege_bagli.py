"""Yerleşik gömme sağlayıcıları isteğe bağlıdır: anahtar yoksa kullanılmaz, kayıt defterine bakılmaz."""
import sys
import types

import pytest

from hatir.anlam import anlam as anlam_mod
from hatir.anlam import gemini

# --- A: gemini.anahtar yalnız ortam değişkenine bakar -----------------------------

class _SahteAnahtar:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sahte_winreg():
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = 1
    mod.OpenKey = lambda *a, **k: _SahteAnahtar()
    mod.QueryValueEx = lambda *a, **k: ("sahte-deger", 1)
    return mod


def test_a_kayit_defteri_okunmaz(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _sahte_winreg())
    with pytest.raises(gemini.GeminiHata):
        gemini.anahtar()


def test_a_ortam_degiskeni_donerse_deger_gelir(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "ornek-deger")
    assert gemini.anahtar() == "ornek-deger"


# --- C: bağlam zenginleştirmesi varsayılan kapalı ---------------------------------

def test_c_varsayilan_kapali(monkeypatch):
    monkeypatch.delenv("HATIR_BAGLAM", raising=False)
    monkeypatch.setattr(anlam_mod, "SAGLAYICI", "gemini")
    assert anlam_mod.baglam_acik() is False


def test_c_env_bir_ve_gemini_acik(monkeypatch):
    monkeypatch.setenv("HATIR_BAGLAM", "1")
    monkeypatch.setattr(anlam_mod, "SAGLAYICI", "gemini")
    assert anlam_mod.baglam_acik() is True


def test_c_baska_saglayici_kapali(monkeypatch):
    monkeypatch.setenv("HATIR_BAGLAM", "1")
    monkeypatch.setattr(anlam_mod, "SAGLAYICI", "nebius")
    assert anlam_mod.baglam_acik() is False
