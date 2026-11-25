"""Paket modüllerinin taşınabilirlik denetimleri."""

import re
from pathlib import Path

from hatir import ortak
from hatir.anlam import anlam, gemini, nebius, rerank, uyumlu
from hatir.olcek import olc, uret
from hatir.puanla import ayir, gecerlilik, karne, puanla


def oku(path):
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_anlam_yerel_yol_ve_sys_path_yok():
    assert "sys.path" not in oku(anlam.__file__)
    for modul in (anlam, gemini, nebius, rerank, uyumlu, olc, uret):
        assert "sys.path" not in oku(modul.__file__), modul.__name__
        metin = oku(modul.__file__)
        assert not re.search(r"[A-Za-z]:[\\/]Users|/home/|_BURASI", metin), modul.__name__


def test_puanla_yerel_yol_ve_sys_path_yok():
    for modul in (ortak, puanla, ayir, gecerlilik):
        assert "sys.path" not in oku(modul.__file__), modul.__name__
    for modul in (ortak, puanla, ayir, gecerlilik, karne):
        metin = oku(modul.__file__)
        assert not re.search(r"[A-Za-z]:[\\/]Users|/home/|_BURASI|Path\(__file__\)\.resolve\(\)\.parents", metin), modul.__name__


def test_yeniden_siralama_varsayilan_saglayici_ya_da_model_yok():
    """Sağlayıcı/model adı kodda politika olarak yazılmaz; yalnız kullanıcının [yeniden_siralama] ayarı."""
    metin = oku(rerank.__file__)
    assert not re.search(r"(?i)nebius|qwen|openai\.com|tokenfactory", metin)
