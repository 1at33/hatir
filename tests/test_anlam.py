"""Ağsız anlam araması testleri: sahte embed_fn, geçici kasa.

Ek: vault yolunun argüman/ortamdan gelmesi, karne/ortak yardımcıları, sahte kasa.py ile karma arama.
"""
import io
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
from pathlib import Path

import pytest

from hatir import ortak
from hatir.anlam import anlam, nebius
from hatir.puanla import karne


class Sahte:
    """Metindeki anahtar kelimelere göre 3 boyutlu vektör üretir; çağrıları kaydeder."""
    def __init__(self):
        self.cagrilar = []

    def __call__(self, texts, **kw):
        self.cagrilar.append((list(texts), kw))

        def vec(t):
            t = t.casefold()
            return [float('kedi' in t) + 0.01, float('araba' in t) + 0.01, float('ev' in t) + 0.01]
        return [vec(t) for t in texts], len(texts) * 10


def yaz(vault, rel, text):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def vault_kur(tmp_path):
    yaz(tmp_path, 'knowledge/a.md', '---\ntitle: Kedi notu\nproject: p1\n---\nkedi kedi\n')
    yaz(tmp_path, 'notes/b.md', '---\nproject: p2\n---\naraba hakkında\n')
    yaz(tmp_path, 'tasks/c.md', 'ev işi\n')
    return tmp_path


def sessiz(*a):
    return None


# --- parçalama ----------------------------------------------------------------------------

def test_parcalama_kisa_ve_uzun_ve_baslik():
    assert anlam.parcala('kısa gövde', 'T') == ['Başlık: T\nkısa gövde']
    govde = '\n\n'.join(f'Paragraf {i} ' + 'x' * 400 for i in range(6))
    parcalar = anlam.parcala(govde, 'T')
    assert len(parcalar) > 1
    assert all(p.startswith('Başlık: T\n') for p in parcalar)
    assert all(len(p) <= 1200 + len('Başlık: T\n') for p in parcalar)
    assert 'Paragraf 5' in parcalar[-1]
    # Tek devasa satır sabit uzunlukla bölünür.
    assert len(anlam.parcala('y' * 3000, 'T')) == 3


def test_baslik_sinirindan_boler():
    govde = '# A\n' + 'a' * 700 + '\n# B\n' + 'b' * 700
    parcalar = anlam.parcala(govde, 'T')
    assert len(parcalar) == 2 and '# B' in parcalar[1]


def test_bos_govde_yalniz_baslik():
    assert anlam.parcala('', 'T') == ['Başlık: T\n']


# --- dizin ---------------------------------------------------------------------------------

def test_dizin_onbellek_yeniden_kullanim(tmp_path):
    vault_kur(tmp_path)
    emb = Sahte()
    anlam.dizinle(tmp_path, emb, log=sessiz)
    assert len(emb.cagrilar) == 1 and len(emb.cagrilar[0][0]) == 3
    emb2 = Sahte()
    stats = anlam.dizinle(tmp_path, emb2, log=sessiz)
    assert emb2.cagrilar == [] and stats['yeni_parca'] == 0
    # Değişen parça yeniden gönderilir.
    yaz(tmp_path, 'notes/b.md', '---\nproject: p2\n---\naraba ve ev\n')
    emb3 = Sahte()
    anlam.dizinle(tmp_path, emb3, log=sessiz)
    assert len(emb3.cagrilar[0][0]) == 1
    # Silinen not parçaları çıkar.
    (tmp_path / 'tasks/c.md').unlink()
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    cache = anlam.onbellek_oku(tmp_path)
    assert 'tasks/c.md' not in cache['notlar'] and len(cache['parcalar']) == 2
    assert cache['toplam_token'] == 30 + 10
    assert anlam.durum(tmp_path)['not'] == 2


def test_partiler_32lik(tmp_path):
    for i in range(70):
        yaz(tmp_path, f'notes/n{i}.md', f'not {i}\n')
    emb = Sahte()
    anlam.dizinle(tmp_path, emb, log=sessiz)
    assert [len(c[0]) for c in emb.cagrilar] == [32, 32, 6]


def test_emekli_ve_eskimis_atlanir(tmp_path):
    yaz(tmp_path, 'notes/eski.md', '---\nstatus: superseded\n---\neski\n')
    yaz(tmp_path, 'notes/yeni.md', '---\nid: yeni-id\nsupersedes: ["eski2"]\n---\nyeni\n')
    yaz(tmp_path, 'notes/eski2.md', 'başka eski\n')
    yaz(tmp_path, 'notes/Arşiv/x.md', 'arşiv\n')
    yaz(tmp_path, 'knowledge/v3/y.md', 'v3\n')
    yaz(tmp_path, 'notes/iyi.md', 'iyi\n')
    kaynak = {n['not'] for n in anlam.notlari_topla(tmp_path)}
    assert kaynak == {'notes/yeni.md', 'notes/iyi.md'}


def test_private_ve_remote_kapali_not_gonderilmez(tmp_path):
    yaz(tmp_path, 'notes/gizli.md', '---\n{"visibility": "private"}\n---\ngizli\n')
    yaz(tmp_path, 'notes/yaml-gizli.md', '---\nvisibility: private\n---\ngizli\n')
    yaz(tmp_path, 'notes/uzak-yok.md', '---\n{"remote_allowed": false}\n---\nyerel\n')
    yaz(tmp_path, 'notes/uzak-hayir.md', '---\nremote_allowed: hayır\n---\nyerel\n')
    yaz(tmp_path, 'notes/acik.md', '---\n{"visibility": "internal"}\n---\nacik\n')
    kaynak = {n['not'] for n in anlam.notlari_topla(tmp_path)}
    assert kaynak == {'notes/acik.md'}


def test_bozuk_on_bilgi_notu_dusurmez(tmp_path):
    yaz(tmp_path, 'notes/bozuk.md', '---\n"açılmış\n---\ngövde\n')
    yaz(tmp_path, 'notes/kapanmamis.md', '---\ntitle: x\ngövde\n')
    notlar = {n['not']: n for n in anlam.notlari_topla(tmp_path)}
    assert set(notlar) == {'notes/bozuk.md', 'notes/kapanmamis.md'}
    assert notlar['notes/bozuk.md']['govde'] == 'gövde' and notlar['notes/bozuk.md']['baslik'] == 'bozuk'


# --- arama ---------------------------------------------------------------------------------

def test_kosinus_ve_not_puani_max_parca():
    assert anlam.kosinus([1, 0], [1, 0]) == pytest.approx(1)
    assert anlam.kosinus([1, 0], [0, 1]) == pytest.approx(0)
    assert anlam.kosinus([0, 0], [1, 0]) == 0.0
    cache = {'parcalar': {'a': [1, 0], 'b': [0, 1]},
             'notlar': {'n.md': {'project': None, 'parcalar': ['a', 'b']}}}
    assert anlam.anlam_ara(cache, [1, 0]) == [('n.md', pytest.approx(1))]
    assert anlam.anlam_ara(cache, [0, 1]) == [('n.md', pytest.approx(1))]


def test_proje_filtresi_proje_ve_global(tmp_path):
    vault_kur(tmp_path)
    yaz(tmp_path, 'notes/d.md', 'kedi araba ev\n')  # global
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    sonuc = anlam.ara(tmp_path, 'kedi', project='p1', embed_fn=Sahte())
    kaynaklar = {r['source'] for r in sonuc['records']}
    assert kaynaklar == {'knowledge/a.md', 'tasks/c.md', 'notes/d.md'}
    assert 'notes/b.md' not in kaynaklar


def test_sorgu_talimat_oneki_ve_belge_talimatsiz(tmp_path, monkeypatch):
    """Nebius seçilince sorguya talimat eklenir; belge ham gider. Seçilmezse talimat yoktur."""
    monkeypatch.setenv('HATIR_SAGLAYICI', 'nebius')
    anlam.saglayici_hazirla()
    vault_kur(tmp_path)
    emb = Sahte()
    anlam.dizinle(tmp_path, emb, log=sessiz)
    assert not any('Instruct:' in t for t in emb.cagrilar[0][0])
    emb2 = Sahte()
    anlam.ara(tmp_path, 'kedi nerede?', embed_fn=emb2)
    sorgu = emb2.cagrilar[0][0][0]
    assert sorgu == ('Instruct: Given a question in Turkish, retrieve the personal note that answers it\n'
                     'Query: kedi nerede?')
    assert emb2.cagrilar[0][1]['timeout'] == 10
    monkeypatch.delenv('HATIR_SAGLAYICI', raising=False)
    anlam.saglayici_hazirla()


def test_rrf_siralamasi():
    sonuc = anlam.rrf(['a', 'b', 'c'], ['b', 'c', 'd'])
    assert [s for s, _ in sonuc] == ['b', 'c', 'a', 'd']
    assert dict(sonuc)['b'] == pytest.approx(1 / 62 + 1 / 61)


def test_karma_dense_listesi_k_ile_kesilir(tmp_path, monkeypatch):
    """Lexical liste ~5 kayıt, dense liste tüm notlar: kesilmezse lexical'daki her not iki puan toplar ve
    dense'in tek başına bulduğu not ilk k'ye giremez (füzyon deneyi 2026-11-04: yetenek 0.36 -> 0.56)."""
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    monkeypatch.setattr(anlam, 'beyin_calistir',
                        lambda vault, python, args: ({'records': [{'source': 'tasks/c.md'}]}, None))
    sonuc = anlam.ara(tmp_path, 'kedi', k=1, karma=True, embed_fn=Sahte())
    # dense[:1] = knowledge/a.md (kedi); tasks/c.md dense'te 3. sırada olsa da kesildiği için ek puan almaz.
    assert sonuc['records'][0]['source'] == 'knowledge/a.md'
    assert dict(anlam.rrf(['x'], ['y'])) == {'x': pytest.approx(1 / 61), 'y': pytest.approx(1 / 61)}


def test_json_sekli_ve_karma(tmp_path, monkeypatch, capsys):
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    monkeypatch.setattr(anlam, 'beyin_calistir',
                        lambda vault, python, args: ({'records': [{'source': 'notes/b.md'}, {'source': 'knowledge/a.md'}]}, None))
    assert anlam.main(['ara', 'kedi', '--vault', str(tmp_path), '--json', '--karma'], embed_fn=Sahte()) == 0
    veri = json.loads(capsys.readouterr().out)
    assert set(veri) == {'records', 'yontem'} and veri['yontem'] == 'karma'
    assert veri['records'][0]['source'] == 'knowledge/a.md'  # iki listede de üstte
    assert all(set(r) == {'source', 'score'} and isinstance(r['score'], float) for r in veri['records'])


def test_hata_karma_degraded_anlam_cikis1(tmp_path, monkeypatch, capsys):
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    cagri = []

    def bozuk(texts, **kw):
        cagri.append(1)
        raise OSError('zaman aşımı')

    monkeypatch.setattr(anlam, 'beyin_calistir',
                        lambda vault, python, args: ({'records': [{'source': 'notes/b.md'}]}, None))
    assert anlam.main(['ara', 'kedi', '--vault', str(tmp_path), '--json', '--karma'], embed_fn=bozuk) == 0
    veri = json.loads(capsys.readouterr().out)
    assert veri['degraded'] is True and [r['source'] for r in veri['records']] == ['notes/b.md']
    assert len(cagri) == 2  # 1 yeniden deneme
    assert anlam.main(['ara', 'kedi', '--vault', str(tmp_path), '--json'], embed_fn=bozuk) == 1


def test_karma_beyin_bozuk_yalniz_dense_degraded(tmp_path, monkeypatch, capsys):
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    monkeypatch.setattr(anlam, 'beyin_calistir', lambda vault, python, args: (None, 'Çıkış 1: patladı'))
    assert anlam.main(['ara', 'kedi', '--vault', str(tmp_path), '--karma'], embed_fn=Sahte()) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].endswith('knowledge/a.md') and 'degraded' in out
    # İki yöntem de düşerse hata.
    with pytest.raises(RuntimeError, match='Çıkış 1'):
        anlam.ara(tmp_path, 'kedi', karma=True, embed_fn=lambda t, **k: (_ for _ in ()).throw(OSError('x')))


def test_eski_model_ara_hata_ve_degraded(tmp_path, monkeypatch):
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    with pytest.raises(RuntimeError, match='eski modelle'):
        anlam.ara(tmp_path, 'kedi', embed_fn=Sahte(), model='baska/model')
    monkeypatch.setattr(anlam, 'beyin_calistir',
                        lambda vault, python, args: ({'records': [{'source': 'notes/b.md'}]}, None))
    sonuc = anlam.ara(tmp_path, 'kedi', karma=True, embed_fn=Sahte(), model='baska/model')
    assert sonuc['degraded'] is True


def test_yarim_dizin_vektorler_kalir_ikinci_kosu_eksikleri_ister(tmp_path):
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    eski_notlar = anlam.onbellek_oku(tmp_path)['notlar']
    for i in range(70):
        yaz(tmp_path, f'notes/n{i}.md', f'not {i}\n')
    sahte = Sahte()

    def ikinci_bozuk(texts, **kw):
        if sahte.cagrilar:
            raise OSError('kesildi')
        return sahte(texts, **kw)

    with pytest.raises(OSError):
        anlam.dizinle(tmp_path, ikinci_bozuk, log=sessiz)
    cache = anlam.onbellek_oku(tmp_path)
    assert cache['notlar'] == eski_notlar
    assert len(cache['parcalar']) == 3 + 32
    emb = Sahte()
    anlam.dizinle(tmp_path, emb, log=sessiz)
    assert sum(len(c[0]) for c in emb.cagrilar) == 70 - 32


def test_vektor_sayisi_uyusmazligi(tmp_path):
    vault_kur(tmp_path)
    with pytest.raises(RuntimeError, match='vektör döndü'):
        anlam.dizinle(tmp_path, lambda texts, **kw: ([[1.0, 0.0, 0.0]], 5), log=sessiz)


def test_bozuk_onbellek_parcasi_atilir_ve_ara_temiz_hata(tmp_path, capsys):
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    veri = json.loads(anlam.onbellek_yolu(tmp_path).read_text(encoding='utf-8'))
    anahtar = next(iter(veri['parcalar']))
    veri['parcalar'][anahtar] = 'bozuk'
    anlam.onbellek_yolu(tmp_path).write_text(json.dumps(veri), encoding='utf-8')
    assert anahtar not in anlam.onbellek_oku(tmp_path)['parcalar']
    bozuk = {'model': anlam.MODEL, 'son_dizin': None, 'toplam_token': 0, 'parcalar': {},
             'notlar': {'x.md': {'project': None}}}
    anlam.onbellek_yolu(tmp_path).write_text(json.dumps(bozuk), encoding='utf-8')
    assert anlam.main(['ara', 'kedi', '--vault', str(tmp_path)], embed_fn=Sahte()) == 1
    assert capsys.readouterr().err.startswith('Hata:')


def test_dizin_yoksa_ara_hata(tmp_path, capsys):
    vault_kur(tmp_path)
    assert anlam.main(['ara', 'kedi', '--vault', str(tmp_path)], embed_fn=Sahte()) == 1
    assert 'Dizin yok' in capsys.readouterr().err


# --- komut satırı: vault argüman/ortamdan -------------------------------------------------

def test_vault_ortam_degiskeninden(tmp_path, monkeypatch, capsys):
    vault_kur(tmp_path)
    monkeypatch.setenv('HATIR_KASA', str(tmp_path))
    assert anlam.main(['dizin'], embed_fn=Sahte()) == 0
    assert 'Dizin: 3 not' in capsys.readouterr().out
    assert anlam.main(['durum']) == 0
    assert 'not: 3' in capsys.readouterr().out
    assert anlam.main(['ara', 'kedi', '--k', '1'], embed_fn=Sahte()) == 0
    assert capsys.readouterr().out.strip().endswith('knowledge/a.md')


def test_vault_verilmezse_acik_hata(monkeypatch, capsys):
    monkeypatch.delenv('HATIR_KASA', raising=False)
    with pytest.raises(SystemExit) as exc:
        anlam.main(['durum'])
    assert exc.value.code == 2
    assert 'HATIR_KASA' in capsys.readouterr().err


def test_k_sifir_ve_dizin_hatasi_cikis1(tmp_path, capsys):
    vault_kur(tmp_path)
    with pytest.raises(SystemExit):
        anlam.main(['ara', 'kedi', '--vault', str(tmp_path), '--k', '0'])
    assert 'en az 1' in capsys.readouterr().err
    assert anlam.main(['dizin', '--vault', str(tmp_path)], embed_fn=lambda t, **k: ([], 0)) == 1
    assert capsys.readouterr().err.startswith('Hata:')


def test_komut_satiri_girisi_calisir(tmp_path):
    """`python -m hatir.anlam.anlam` (hatir-anlam ile aynı main) anahtar yokken ağa çıkmadan açık hata verir."""
    vault_kur(tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in ('NEBIUS_API_KEY', 'HATIR_SAGLAYICI')}
    env['PYTHONIOENCODING'] = 'utf-8'
    src = str(Path(__file__).resolve().parents[1] / 'src')
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [src, env.get('PYTHONPATH')]))
    r = subprocess.run([sys.executable, '-m', 'hatir.anlam.anlam', 'dizin', '--vault', str(tmp_path)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, check=False)
    assert r.returncode == 1 and 'gömme sağlayıcısı yok' in r.stderr


# --- beyin köprüsü: sahte kasa.py ve .claude/scripts -------------------------------------

def test_sahte_beyin_ile_karma_arama(tmp_path, capsys):
    vault_kur(tmp_path)
    anlam.dizinle(tmp_path, Sahte(), log=sessiz)
    (tmp_path / 'kasa.py').write_text(textwrap.dedent('''
        import json, sys
        assert sys.argv[1] == 'context' and '--json' in sys.argv and '--no-sync' in sys.argv
        print(json.dumps({'records': [{'source': 'notes\\\\b.md'}, {'source': './notes/b.md'}, {'x': 1}]}))
    '''), encoding='utf-8')
    assert anlam.beyin_sirasi(tmp_path, 'araba', 'p2', sys.executable) == ['notes/b.md']
    sonuc = anlam.ara(tmp_path, 'araba', project='p2', karma=True, embed_fn=Sahte())
    assert sonuc['records'][0]['source'] == 'notes/b.md' and 'degraded' not in sonuc


def test_beyin_calistir_hatalari(tmp_path):
    (tmp_path / 'kasa.py').write_text('import sys; print("metin"); sys.exit(3)', encoding='utf-8')
    data, hata = ortak.beyin_calistir(tmp_path, sys.executable, ['context', 'x'])
    assert data is None and hata.startswith('Çıkış 3')
    assert ortak.beyin_calistir(tmp_path, sys.executable, ['x'], json_bekle=False) == ({}, hata)
    (tmp_path / 'kasa.py').write_text('print("[1]")', encoding='utf-8')
    data, hata = ortak.beyin_calistir(tmp_path, sys.executable, ['x'])
    assert data is None and 'JSON nesnesi' in hata
    assert ortak.beyin_calistir(tmp_path, 'yok-boyle-python-xyz', ['x'])[0] is None
    with pytest.raises(RuntimeError, match='JSON'):
        anlam.beyin_sirasi(tmp_path, 'x', None, sys.executable)


def test_beyin_islevleri_vaultun_kendi_ayristiricisi(tmp_path, monkeypatch):
    monkeypatch.setenv('HATIR_KASA_KODU', '1')
    scripts = tmp_path / '.claude' / 'scripts'
    scripts.mkdir(parents=True)
    (scripts / 'beyin_v3_sync.py').write_text(
        'def parse(text):\n    return {"title": "VAULT", "status": "aktif"}, text\n', encoding='utf-8')
    (scripts / 'beyin_v3.py').write_text(textwrap.dedent('''
        RETIRED_STATUSES = frozenset({'kapali'})
        def _status_word(record):
            return str(record.get('status', ''))
        def resolve_supersedes(records):
            return set(), []
    '''), encoding='utf-8')
    sys.modules.pop('beyin_v3', None)
    sys.modules.pop('beyin_v3_sync', None)
    try:
        f = karne.beyin_islevleri(tmp_path)
        assert f['parse']('x') == ({'title': 'VAULT', 'status': 'aktif'}, 'x')
        assert f['retired'] == {'kapali'} and f['status_word']({'status': 'kapali'}) == 'kapali'
        yaz(tmp_path, 'notes/n.md', 'gövde\n')
        yaz(tmp_path, 'notes/k.md', '---\nstatus: kapali\n---\nx\n')
        notlar = anlam.notlari_topla(tmp_path)
        assert [n['baslik'] for n in notlar] == ['VAULT', 'VAULT']  # vault parser'ı status'u sabit veriyor
        # Başka vault'tan yüklenmiş modül kullanılmaz: ikinci vault'ta yedekler döner.
        baska = tmp_path / 'baska'
        (baska / '.claude' / 'scripts').mkdir(parents=True)
        g = karne.beyin_islevleri(baska)
        assert g['parse'] is None and g['retired'] is karne.RETIRED_STATUSES
    finally:
        sys.modules.pop('beyin_v3', None)
        sys.modules.pop('beyin_v3_sync', None)
    assert karne.beyin_islevleri(tmp_path / 'yok')['status_word'] is karne.status_word


# --- karne yedekleri ------------------------------------------------------------------------

@pytest.mark.parametrize('metin,beklenen', [
    ('---\ntitle: a\nlist:\n  - x\n  - "y"\nmap:\n  k: 1\n---\ng', {'title': 'a', 'list': ['x', 'y'], 'map': {'k': 1}}),
    ("---\nt: 'it''s'\n---\n", {'t': "it's"}),
    ('---\n# yorum\nbos:\n---\n', {'bos': None}),
    ('---\n{"a": 1}\n---\n', {'a': 1}),
    ('﻿---\nx: 1\n---\n', {'x': 1}),
    ('gövde yok ön bilgi', {}),
])
def test_yedek_parse(metin, beklenen):
    assert karne.yedek_parse(metin)[0] == beklenen


@pytest.mark.parametrize('metin', [
    '---\nx: 1\n', '---\n[1]\n---\n', '---\nx: 1\nx: 2\n---\n', '---\n  - a\n---\n', '---\nm:\n  k: 1\n  k: 2\n---\n',
    '---\n?? bozuk\n---\n', '---\nx: "açık\n---\n', '---\nx:\n  k: 1\n  - a\n---\n',
])
def test_yedek_parse_hatalar(metin):
    with pytest.raises(ValueError):
        karne.yedek_parse(metin)


def test_on_bilgi_hatada_govde_korunur():
    assert karne.on_bilgi('---\nx: 1\nx: 2\n---\ngövde') == ({}, 'gövde')
    assert karne.on_bilgi('---\nx: 1\n') == ({}, '---\nx: 1\n')
    assert karne.on_bilgi('x', parser=lambda t: ('liste değil', t)) == ({}, 'x')


def test_status_word_ve_resolve_supersedes():
    assert karne.status_word({'status': ' Arşivlendi (2026)'}) == 'arsivlendi'
    assert karne.status_word({'kind': 'task'}) == '' and karne.status_word({}) == 'active'
    assert karne.status_word({'status': '!!!'}) == ''
    kayitlar = [
        {'id': 'a', 'source': 'notes/a.md'},
        {'id': 'b', 'source': 'notes/b.md', 'supersedes': 'a'},
        {'id': 'c', 'source': 'notes/c.md', 'supersedes': ['[[a|takma]]', 'yok', 'c', '[[B]]']},
        {'id': 'd', 'source': 'x/d.md'}, {'id': 'd2', 'source': 'y/d.md'},
        {'id': 'e', 'source': 'notes/e.md', 'supersedes': ['d', 'd.md', 12]},
        {'id': 'u', 'source': 'notes/u.md', 'supersedes': ['b'], 'trust': 'untrusted'},
        {'id': 'r', 'kind': 'inference', 'validity': 'rejected', 'supersedes': ['b']},
        {'id': 'n', 'supersedes': {'bozuk': 1}},
    ]
    retired, dead = karne.resolve_supersedes(kayitlar)
    assert retired == {'a', 'b', 'd'}
    nedenler = {(d['id'], d['reason']) for d in dead}
    assert ('c', 'unresolved supersedes reference') in nedenler
    assert ('c', 'supersedes reference points to the note itself') in nedenler
    assert ('e', 'ambiguous supersedes reference') in nedenler


def test_yerine_gecenler_kaynak_atfi():
    notlar = [
        {'not': 'notes/eski.md', 'metadata': {}},
        {'not': 'notes/yeni.md', 'metadata': {'id': 'yeni', 'supersedes': 'eski'}},
        {'not': 'notes/yeni2.md', 'metadata': {'supersedes': ['notes/eski.md']}},
    ]
    r = karne.yerine_gecenler(notlar)
    assert r == {'notes/eski.md': {'notes/yeni.md', 'notes/yeni2.md'}}
    assert karne.not_id(notlar[0]).startswith('md-') and len(karne.not_id(notlar[0])) == 27


def test_ortak_yol_ve_kaynaklar():
    assert ortak.yol('.\\Notes\\A.md') == 'notes/a.md'
    assert ortak.kaynaklar([{'source': 'a\\b.md'}, {'source': './A/B.md'}, {'source': ''}, 'x', {'source': 'c.md'}]) == ['a/b.md', 'c.md']
    assert ortak.gizli_pencere(stdin=False).get('stdin') is None
    assert ortak.gizli_pencere()['stdin'] is not None


# --- nebius ----------------------------------------------------------------------------------

def test_nebius_anahtar_yoksa_acik_hata(monkeypatch):
    monkeypatch.delenv('NEBIUS_API_KEY', raising=False)
    with pytest.raises(nebius.NebiusHata, match='NEBIUS_API_KEY'):
        nebius.embed(['x'])


def test_nebius_hata_metninde_anahtar_yok(monkeypatch):
    gizli = 'sahte-nebius-anahtari'
    monkeypatch.setenv('NEBIUS_API_KEY', gizli)
    monkeypatch.setattr(nebius.time, 'sleep', lambda s: None)
    for hata in (urllib.error.HTTPError(nebius.URL, 401, 'Unauthorized', {}, io.BytesIO(b'')),
                 urllib.error.URLError('baglanti yok')):
        def acik(request, timeout=None, hata=hata):
            raise hata
        monkeypatch.setattr(nebius.urllib.request, 'urlopen', acik)
        with pytest.raises(nebius.NebiusHata) as exc:
            nebius.embed(['x'])
        assert gizli not in str(exc.value) and gizli not in repr(exc.value)


class Cevap(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_nebius_siralama_token_ve_gecersiz_cevap(monkeypatch):
    monkeypatch.setenv('NEBIUS_API_KEY', 'sahte-nebius-anahtari')
    monkeypatch.setattr(nebius.time, 'sleep', lambda s: None)
    cevap = {'data': [{'index': 1, 'embedding': [2.0]}, {'index': 0, 'embedding': [1.0]}], 'usage': {'total_tokens': 7}}
    monkeypatch.setattr(nebius.urllib.request, 'urlopen', lambda r, timeout: Cevap(json.dumps(cevap).encode()))
    assert nebius.embed(['a', 'b']) == ([[1.0], [2.0]], 7)
    monkeypatch.setattr(nebius.urllib.request, 'urlopen', lambda r, timeout: Cevap(b'{"data": []}'))
    with pytest.raises(nebius.NebiusHata, match='vektör döndü'):
        nebius.embed(['a'], deneme=1)
    monkeypatch.setattr(nebius.urllib.request, 'urlopen', lambda r, timeout: Cevap(b'{"yok": 1}'))
    with pytest.raises(nebius.NebiusHata, match='geçersiz cevap'):
        nebius.embed(['a'])
    kodlar = iter([429, 500])

    def sunucu(r, timeout):
        raise urllib.error.HTTPError(nebius.URL, next(kodlar), 'x', {}, None)
    monkeypatch.setattr(nebius.urllib.request, 'urlopen', sunucu)
    with pytest.raises(nebius.NebiusHata, match='2 denemede başarısız: HTTP 500'):
        nebius.embed(['a'], deneme=2)


# --- bağlam cümlesi (contextual retrieval; kaynak testleri + sağlayıcı kapısı) -----------------

def test_baglam_cumlesi_parcaya_eklenir_ve_onbellekte_kalir(tmp_path):
    vault_kur(tmp_path)
    cagrilar = []

    def baglam(belge, parca):
        cagrilar.append((belge, parca))
        return 'Bu parça kedi notundan.' if 'kedi' in parca else ''
    emb = Sahte()
    anlam.dizinle(tmp_path, emb, log=sessiz, baglam_fn=baglam)
    assert len(cagrilar) == 3 and all(b.startswith('Başlık: ') and '\nNot: ' in b for b, _ in cagrilar)
    metinler = emb.cagrilar[0][0]
    assert any(m.startswith('Başlık: Kedi notu\nBağlam: Bu parça kedi notundan.\n') for m in metinler)
    assert sum('Bağlam:' in m for m in metinler) == 1  # boş bağlam satır eklemez
    assert len(anlam.baglam_oku(tmp_path)) == 3
    # İkinci dizinleme: bağlam yeniden üretilmez, vektör yeniden istenmez.
    cagrilar.clear()
    emb2 = Sahte()
    anlam.dizinle(tmp_path, emb2, log=sessiz, baglam_fn=baglam)
    assert cagrilar == [] and emb2.cagrilar == []
    assert anlam.baglamli_metin('Başlık: T\ngövde', '') == 'Başlık: T\ngövde'
    assert anlam.baglamli_metin('Başlık: T\ngövde', 'c') == 'Başlık: T\nBağlam: c\ngövde'


def test_baglam_hatasi_parcayi_baglamsiz_birakir(tmp_path, capsys):
    vault_kur(tmp_path)

    def bozuk(belge, parca):
        raise RuntimeError('ağ yok')
    emb = Sahte()
    anlam.dizinle(tmp_path, emb, baglam_fn=bozuk)
    assert len(emb.cagrilar[0][0]) == 3 and not any('Bağlam:' in m for m in emb.cagrilar[0][0])
    assert set(anlam.baglam_oku(tmp_path).values()) == {''}
    assert 'Bağlam üretilemedi (RuntimeError)' in capsys.readouterr().out


def test_baglam_onbellegi_50de_ara_yazilir_ve_bozuk_dosya_bos(tmp_path):
    for i in range(55):
        yaz(tmp_path, f'notes/n{i}.md', f'not {i}\n')
    yazimlar = []

    def baglam(belge, parca):
        if not yazimlar and anlam.baglam_yolu(tmp_path).exists():
            yazimlar.append(len(anlam.baglam_oku(tmp_path)))
        return 'c'
    anlam.dizinle(tmp_path, Sahte(), log=sessiz, baglam_fn=baglam)
    assert yazimlar == [50]  # 50. cümleden sonra ara yazım
    assert len(anlam.baglam_oku(tmp_path)) == 55
    anlam.baglam_yolu(tmp_path).write_text('[1, 2]', encoding='utf-8')
    assert anlam.baglam_oku(tmp_path) == {}
    anlam.baglam_yolu(tmp_path).write_text('{bozuk', encoding='utf-8')
    assert anlam.baglam_oku(tmp_path) == {}


def test_baglam_nebiusta_kapali_geminide_ortamla(monkeypatch):
    monkeypatch.setattr(anlam, 'SAGLAYICI', 'nebius')
    monkeypatch.setenv('HATIR_BAGLAM', '1')
    assert anlam.baglam_acik() is False
    monkeypatch.setattr(anlam, 'SAGLAYICI', 'gemini')
    assert anlam.baglam_acik() is True
    monkeypatch.setenv('HATIR_BAGLAM', '0')
    assert anlam.baglam_acik() is False


@pytest.mark.parametrize('deger', [None, '', '0', 'true', '2'])
def test_baglam_geminide_acik_onay_olmadan_kapali(monkeypatch, deger):
    monkeypatch.setattr(anlam, 'SAGLAYICI', 'gemini')
    if deger is None:
        monkeypatch.delenv('HATIR_BAGLAM', raising=False)
    else:
        monkeypatch.setenv('HATIR_BAGLAM', deger)
    assert anlam.baglam_acik() is False


def test_varsayilan_baglam_sir_kalibinda_aga_cikmaz(monkeypatch):
    from hatir.anlam import gemini
    cagri = []
    monkeypatch.setattr(anlam, '_gemini', gemini, raising=False)
    monkeypatch.setattr(gemini, 'uret', lambda istem, **kw: cagri.append(istem) or 'bağlam cümlesi')
    sir = 'gh' + 'p_' + 'A1b2C3d4E5f6G7h8J9k0'
    assert anlam.varsayilan_baglam('belge', 'parça ' + sir) == ''
    assert anlam.varsayilan_baglam('belge ' + sir, 'parça') == ''
    assert cagri == []
    assert anlam.varsayilan_baglam('belge metni', 'parça metni') == 'bağlam cümlesi'
    assert '<not>\nbelge metni\n</not>' in cagri[0] and '<parca>\nparça metni\n</parca>' in cagri[0]


def test_dizin_geminide_varsayilan_baglam_uretici(tmp_path, monkeypatch):
    vault_kur(tmp_path)
    monkeypatch.setattr(anlam, 'SAGLAYICI', 'gemini')
    monkeypatch.setenv('HATIR_BAGLAM', '1')
    monkeypatch.setattr(anlam, 'varsayilan_baglam', lambda belge, parca: 'VB')
    emb = Sahte()
    anlam.dizinle(tmp_path, emb, log=sessiz)
    assert all('\nBağlam: VB\n' in m for m in emb.cagrilar[0][0])

def test_kasa_betigi_ortamdan_gelir(tmp_path, monkeypatch):
    (tmp_path / 'ozel.py').write_text('print("{}")', encoding='utf-8')
    monkeypatch.setenv('HATIR_KASA_BETIGI', 'alt/ozel.py')
    assert ortak.kasa_betigi() == 'ozel.py'
    assert ortak.beyin_calistir(tmp_path, sys.executable, ['x']) == ({}, None)


def test_klasorler_ortamdan_secilir(tmp_path, monkeypatch):
    (tmp_path / 'notlar').mkdir()
    (tmp_path / 'notlar' / 'A.md').write_text('---\ntitle: A\n---\nkedi\n', encoding='utf-8')
    (tmp_path / 'knowledge').mkdir()
    (tmp_path / 'knowledge' / 'B.md').write_text('---\ntitle: B\n---\nköpek\n', encoding='utf-8')
    monkeypatch.delenv('HATIR_KLASORLER', raising=False)
    assert anlam.klasorler() == ('knowledge', 'notes', 'tasks')
    monkeypatch.setenv('HATIR_KLASORLER', ' notlar/ , ,')
    assert anlam.klasorler() == ('notlar',)
    assert [n['not'] for n in anlam.notlari_topla(tmp_path)] == ['notlar/A.md']


def test_sirli_parca_servise_gonderilmez(tmp_path):
    (tmp_path / 'notes').mkdir()
    sir = 'gh' + 'p_' + 'Q7w9E2r4T6y8U1i3O5p7A9s2D4f6G8h1J3k5'
    (tmp_path / 'notes' / 'A.md').write_text(f'---\ntitle: A\n---\nanahtar {sir}\n', encoding='utf-8')
    (tmp_path / 'notes' / 'B.md').write_text('---\ntitle: B\n---\nparola = cokgizlibirdeger\n', encoding='utf-8')
    (tmp_path / 'notes' / 'C.md').write_text('---\ntitle: C\n---\nkedi köpek\n', encoding='utf-8')
    giden, kayit = [], []

    def embed(texts, **kw):
        giden.extend(texts)
        return [[1.0, 0.0, 0.0] for _ in texts], 1

    stats = anlam.dizinle(tmp_path, embed_fn=embed, model='m', log=kayit.append)
    assert stats['sirli_atlanan'] == 2
    assert not any(sir in t or 'cokgizlibirdeger' in t for t in giden)
    assert any('kedi' in t for t in giden)
    assert any('sır kalıbı' in m for m in kayit)


def test_kasa_kodu_varsayilan_kapali(tmp_path, monkeypatch):
    scripts = tmp_path / '.claude' / 'scripts'
    scripts.mkdir(parents=True)
    isaret = tmp_path / 'calisti.txt'
    (scripts / 'beyin_v3_sync.py').write_text(
        f'open({str(isaret)!r}, "w").write("x")\ndef parse(text):\n    return {{}}, text\n', encoding='utf-8')
    sys.modules.pop('beyin_v3_sync', None)
    monkeypatch.delenv('HATIR_KASA_KODU', raising=False)
    assert karne.beyin_islevleri(tmp_path)['parse'] is None
    assert not isaret.exists()
