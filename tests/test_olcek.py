"""Ölçek testi: sentetik kasa geçici klasörde üretilir, gerçek kasaya dokunulmaz, ağ yoktur."""
import json

import pytest

from hatir.olcek import olc, uret


def test_olc_n_not_uretir_ve_metrik_dondurur(tmp_path):
    kasa = tmp_path / 'kasa'
    rapor = olc.olc(adet=80, soru=8, tohum='3', klasor=kasa)
    assert rapor['adet'] == 80 and rapor['md_dosya'] == 80
    assert sum(1 for _ in kasa.rglob('*.md')) == 80
    assert rapor['tur_dagilimi']['altin'] == 8 and sum(rapor['tur_dagilimi'].values()) == 80
    assert [s['not'] for s in rapor['seviyeler']] == [8, 20, 40, 80]
    for seviye in rapor['seviyeler']:
        for mod in ('kapsamli', 'kapsamsiz'):
            assert set(seviye[mod]) >= {'hit@1', 'hit@5', 'MRR', 'sorgu_ms'}
            assert 0.0 <= seviye[mod]['hit@1'] <= seviye[mod]['hit@5'] <= 1.0
        assert seviye['kapsamli']['kapsam_ihlal'] == 0  # proje süzgeci başka projeden not getirmez
    # Yalnız altın notlar varken her soru kendi notunu bulur.
    assert rapor['seviyeler'][0]['kapsamli']['hit@1'] == 1.0
    on_bilgi = (kasa / next(iter(sorted(p.relative_to(kasa).as_posix() for p in kasa.rglob('*.md'))))).read_text(
        encoding='utf-8')
    assert '"olcek_sentetik"' in on_bilgi


def test_varsayilan_gecici_klasor_silinir(monkeypatch, tmp_path):
    gorulen = []
    gercek = olc.kasa_hazirla

    def izle(klasor):
        gorulen.append(klasor)
        return gercek(klasor)
    monkeypatch.setattr(olc, 'kasa_hazirla', izle)
    rapor = olc.olc(adet=30, soru=5)
    assert rapor['md_dosya'] == 30 and gorulen and not gorulen[0].exists()


def test_bos_olmayan_klasore_yazmaz(tmp_path):
    (tmp_path / 'not.md').write_text('gerçek not', encoding='utf-8')
    with pytest.raises(ValueError, match='boş değil'):
        olc.olc(adet=10, soru=2, klasor=tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ['not.md']


def test_uretim_belirlenimli_ve_ic_ice(tmp_path):
    kucuk, _, rk = uret.uret(tmp_path / 'a', 40, soru=5, tohum='7')
    buyuk, sorular, _ = uret.uret(tmp_path / 'b', 120, soru=5, tohum='7')
    _, _, rt = uret.uret(tmp_path / 'c', 40, soru=5, tohum='7')
    assert [n['yol'] for n in kucuk] == [n['yol'] for n in buyuk[:40]]
    assert rk['dizi_sha256'] == rt['dizi_sha256']
    assert [q['beklenen'][0] for q in sorular] == [n['yol'] for n in buyuk[:5]]
    assert all(q['proje'] and q['tur'] == 'kelime' for q in sorular)
    _, _, rb = uret.uret(tmp_path / 'd', 40, soru=5, tohum='8')
    assert rb['dizi_sha256'] != rk['dizi_sha256']


def test_cli_json_ve_cikti(tmp_path, capsys):
    cikti = tmp_path / 'r' / 'rapor.json'
    assert olc.main(['--adet', '40', '--soru', '4', '--seviyeler', '4,20', '--json', '--cikti', str(cikti)]) == 0
    veri = json.loads(capsys.readouterr().out)
    assert [s['not'] for s in veri['seviyeler']] == [4, 20, 40]
    assert json.loads(cikti.read_text(encoding='utf-8'))['adet'] == 40
    assert olc.main(['--adet', '30', '--soru', '3']) == 0
    assert 'kapsamlı hit@1' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        olc.main(['--buyuk', '--adet', '10'])
    with pytest.raises(SystemExit):
        olc.main(['--seviyeler', 'x'])


def test_buyuk_bayragi_on_bin(monkeypatch):
    gorulen = {}
    monkeypatch.setattr(olc, 'olc', lambda adet, *a: gorulen.setdefault('adet', adet) and {
        'k': 5, 'seviyeler': [], 'adet': adet, 'soru': 30, 'tohum': '1', 'tur_dagilimi': {}, 'dizi_sha256': ''})
    assert olc.main(['--buyuk']) == 0
    assert gorulen['adet'] == olc.BUYUK_ADET == 10_000


def test_bm25_proje_suzgeci_ve_turkce_katlama():
    motor = olc.BM25([('a.md', 'İzmir LİMAN kararı', 'p1'), ('b.md', 'izmir liman', 'p2'), ('c.md', 'liman', None)])
    assert motor.ara('izmir liman', 'p1') == ['a.md', 'c.md']
    assert set(motor.ara('izmir liman')) == {'a.md', 'b.md', 'c.md'}
