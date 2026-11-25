"""Gerçek vault ve alt süreç kullanmayan puanla / karne / ayir / gecerlilik testleri.

Ek: vault'un argüman/ortamdan gelmesi, varsayılan sonuç klasörü, anlam betiğinin paket içinden çözülmesi.
"""
import hashlib
import json
import sys
import tempfile
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from hatir.ortak import json_oku, json_yaz, kaynaklar, puan_dosyalari, yol
from hatir.puanla import ayir, karne, puanla
from hatir.puanla.gecerlilik import donguleri_bul, valid_to_oku
from hatir.puanla.karne import (
    AGIRLIKLAR,
    agirliklar,
    anilma_sayisi,
    bulunabilirlik_sinyalleri,
    dogrulama_sinyali,
    karne_hesapla,
    markdown,
    notlari_oku,
    on_bilgi,
    proje_ozeti,
    puan_ve_oneriler,
    sayi_sinyali,
    tazelik_sinyali,
    tekrarlar,
)
from hatir.puanla.puanla import (
    context_argumanlari,
    karsilastirma,
    ozetle,
    soru_metrikleri,
    sorulari_oku,
)


@pytest.fixture
def tmp_path():
    # Paylaşılan pytest-of-<kullanıcı> dizininin eski ACL'lerine bağımlı olma.
    with tempfile.TemporaryDirectory(prefix='hatir-') as folder:
        yield Path(folder)


@pytest.mark.parametrize('rank,k,h1,h3,hk,mrr', [
    (1, 5, 1, 1, 1, 1), (2, 5, 0, 1, 1, 0.5),
    (3, 2, 0, 1, 0, 1 / 3), (5, 5, 0, 0, 1, 0.2),
    (6, 5, 0, 0, 0, 1 / 6), (None, 5, 0, 0, 0, 0)])
def test_metrikler(rank, k, h1, h3, hk, mrr):
    sources = [f'notes/{i}.md' for i in range(1, 8)]
    expected = [f'notes/{rank}.md'] if rank else ['notes/yok.md']
    result = soru_metrikleri(expected, sources, k)
    assert result['sira'] == rank
    assert result['hit@1'] == h1
    assert result['hit@3'] == h3
    assert result[f'hit@{k}'] == hk
    assert result['MRR'] == pytest.approx(mrr)


def test_tekrarli_kaynak_ters_cizgi_ilk_beklenen():
    records = [{'source': 'notes\\A.md'}, {'source': 'notes/A.md'},
               {'source': 'notes\\B.md'}, {}, {'source': None}]
    assert kaynaklar(records) == ['notes/A.md', 'notes/B.md']
    result = soru_metrikleri(['notes/C.md', 'notes/B.md'],
                            ['notes\\A.md', 'notes/A.md', 'notes\\B.md'])
    assert result['sira'] == 2
    assert result['MRR'] == 0.5
    with pytest.raises(ValueError):
        soru_metrikleri([], [], 0)


def test_ozet_turler_ve_bos_grup():
    rows = [{'tur': 'kelime', 'metrikler': soru_metrikleri(['a'], ['a'])},
            {'tur': 'anlam', 'metrikler': soru_metrikleri(['a'], [])}]
    summary = ozetle(rows)
    assert summary['genel']['hit@5'] == 0.5
    assert summary['kelime']['MRR'] == 1
    assert summary['anlam']['MRR'] == 0
    assert ozetle(rows[:1])['anlam'] == {'adet': 0, 'hatali': 0, 'hit@1': 0, 'hit@3': 0, 'hit@5': 0, 'MRR': 0}


def test_context_her_zaman_no_sync_liste_argumani():
    question = {'soru': 'boşluk "tırnak" & $ metni', 'proje': 'örnek'}
    assert context_argumanlari(question) == [
        'context', question['soru'], '--project', 'örnek', '--no-sync', '--json']
    assert context_argumanlari({'soru': 'x', 'proje': None}) == ['context', 'x', '--no-sync', '--json']


@pytest.mark.parametrize('text,metadata,body', [
    ('---\n{"title":"Türkçe", "facts":{"dogrulama":"test"}}\n---\nGövde',
     {'title': 'Türkçe', 'facts': {'dogrulama': 'test'}}, 'Gövde'),
    ('Ön bilgisiz', {}, 'Ön bilgisiz'),
    ('---\n{bozuk}\n---\nGövde', {}, 'Gövde'),
    ('---\ntitle: YAML\n---\nGövde', {'title': 'YAML'}, 'Gövde'),
    ('---\n[]\n---\nGövde', {}, 'Gövde'),
    ('---\nkapanmamış', {}, '---\nkapanmamış')])
def test_on_bilgi(text, metadata, body):
    assert on_bilgi(text) == (metadata, body)


@pytest.mark.parametrize('value,expected', [
    ('yerel test', 1), ('ölçüm', 1), ('olcum', 1), ('DENEY', 1),
    ('kullanıcı kararı', 0.9), ('kaynak', 0.6), ('okuma', 0.6),
    ('doğrulandı', 0.6), ('Dogrulandi', 0.6), ('gözlendi', 0.3), ('gozlendi', 0.3),
    ('doğrulandı, test', 1), ('gözlendi kaynak', 0.6),
    (None, 0.1), ('', 0.1), ('  ', 0.1), ('bilinmiyor', 0.3)])
def test_dogrulama(value, expected):
    assert dogrulama_sinyali(value) == expected


@pytest.mark.parametrize('count,expected', [(0, 0), (1, 0.5), (2, 0.75), (3, 1), (8, 1)])
def test_sayi_normalizasyonu(count, expected):
    assert sayi_sinyali(count) == expected


@pytest.mark.parametrize('days,expected', [(0, 1), (30, 1), (30.1, 0.6), (90, 0.6), (90.1, 0.3)])
def test_tazelik(days, expected):
    assert tazelik_sinyali(days) == expected


def test_puan_agirlik_ve_tum_oneriler():
    signals = {'dogrulama': 0.3, 'kullanim': 0, 'baglanti': 0, 'tazelik': 1, 'bulunabilirlik': 0}
    assert puan_ve_oneriler(signals)[0] == 24
    score, suggestions = puan_ve_oneriler(signals, True, ['notes/B.md'])
    assert score == 20
    assert suggestions == ['arşivle', 'birleştir: notes/B.md', 'doğrula',
                           'bağla ya da arşivle', 'başlık/anahtar kelime güçlendir']
    perfect = dict.fromkeys(AGIRLIKLAR, 1)
    assert puan_ve_oneriler(perfect) == (100, [])
    assert puan_ve_oneriler(perfect, True)[0] == 20


def test_bulunabilirlik_yokken_agirlik_dagitimi():
    signals = dict.fromkeys(AGIRLIKLAR, 1)
    signals['bulunabilirlik'] = None
    weights = agirliklar(signals)
    assert sum(weights.values()) == pytest.approx(1)
    assert 'bulunabilirlik' not in weights
    assert weights['dogrulama'] == pytest.approx(0.3 / 0.8)
    assert puan_ve_oneriler(signals) == (100, [])
    assert AGIRLIKLAR['bulunabilirlik'] == 0.2


def test_bulunabilirlik_sira_ve_k():
    result = {'k': 2, 'sorular': [
        {'beklenen': ['notes/A.md'], 'sira': 2, 'ilk_5': []},
        {'beklenen': ['notes/A.md'], 'sira': 3, 'ilk_5': ['notes/A.md']},
        {'beklenen': ['notes/B.md'], 'sira': None}]}
    assert bulunabilirlik_sinyalleri(result) == {'notes/a.md': 0.5, 'notes/b.md': 0}


def test_anilma_yol_ad_cift_sayilmaz():
    assert anilma_sayisi('NOTES/a.md a notes\\A.md', 'notes/A.md') == 3
    assert anilma_sayisi('[[A#başlık|takma]] notes/A.md A', 'notes/A.md', wiki=True) == 2
    assert anilma_sayisi('[[notes/A]] [[notes/A#başlık|takma]]', 'notes/A.md') == 2


def not_yaz(root, source, metadata, body):
    path = root / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('---\n' + json.dumps(metadata, ensure_ascii=False) + '\n---\n' + body, encoding='utf-8')
    return path


def test_sahte_not_receipt_entegre(tmp_path):
    not_yaz(tmp_path, 'knowledge/A.md', {'id': 'a', 'project': 'p', 'facts': {'dogrulama': 'test'}}, 'İçerik')
    not_yaz(tmp_path, 'notes/B.md', {'supersedes': ['a']}, '[[A]] knowledge/A.md')
    not_yaz(tmp_path, 'notes/C.md', {'status': 'archived'}, 'Eski')
    not_yaz(tmp_path, 'knowledge/v3/D.md', {}, 'Hariç')
    not_yaz(tmp_path, 'notes/Arşiv/E.md', {}, 'Hariç')
    receipt = tmp_path / 'receipts' / 'r.md'
    receipt.parent.mkdir()
    receipt.write_text('knowledge/A.md\nA', encoding='utf-8')
    notes, warnings = notlari_oku(tmp_path)
    assert not warnings
    assert len(notes) == 3
    now = datetime(2026, 10, 22, tzinfo=UTC)
    dates = {n['not']: (now - timedelta(days=10), 'mtime') for n in notes}
    audit = {'findings': [{'older_note': 'knowledge/A.md', 'newer_note': 'notes/B.md', 'jaccard': 0.6}]}
    rows = karne_hesapla(notes, [receipt.read_text(encoding='utf-8')], dates,
                        {'knowledge/A.md': 1}, audit, now)
    by_source = {row['not']: row for row in rows}
    a = by_source['knowledge/A.md']
    assert a['kullanim_sayisi'] == 2
    assert a['baglanti_sayisi'] == 2
    assert a['sinyaller']['kullanim'] == 0.75
    assert a['sinyaller']['baglanti'] == 0.75
    assert a['eskimis'] and a['puan'] == 20
    assert a['dogrulama_degeri'] == 'test' and by_source['notes/B.md']['dogrulama_degeri'] is None
    assert by_source['notes/B.md']['sinyaller']['dogrulama'] == 0.1
    assert a['yerine_gecen'] == ['notes/B.md']
    assert a['tekrar_ile'] == ['notes/B.md']
    assert by_source['notes/B.md']['tekrar_ile'] == ['knowledge/A.md']
    assert 'notes/C.md' not in by_source
    assert sum(karne.arsivli_mi(n) for n in notes) == 1
    assert [r['puan'] for r in rows] == sorted(r['puan'] for r in rows)
    report = {'tarih': now.isoformat(), 'notlar': rows, 'projeler': proje_ozeti(rows),
              'arsivli_atlanan': 1, 'uyarilar': []}
    assert report['projeler']['p']['ortalama_puan'] == 20
    assert '## Proje özeti' in markdown(report)
    assert 'Arşivli olduğu için atlanan not: 1' in markdown(report)


def test_bozuk_not_atlanmaz(tmp_path):
    path = tmp_path / 'notes' / 'Bozuk.md'
    path.parent.mkdir()
    path.write_text('---\n{bozuk}\n---\nMetin', encoding='utf-8')
    notes, warnings = notlari_oku(tmp_path)
    assert len(notes) == 1 and not warnings
    assert notes[0]['metadata'] == {}


def test_supersedes_yol_eslesmesi_ve_self_haric(tmp_path):
    not_yaz(tmp_path, 'notes/A.md', {'supersedes': ['notes/A.md']}, '')
    not_yaz(tmp_path, 'notes/B.md', {'supersedes': ['notes\\A.md']}, '')
    notes, _ = notlari_oku(tmp_path)
    now = datetime.now(UTC)
    dates = {n['not']: (now, 'mtime') for n in notes}
    rows = karne_hesapla(notes, [], dates, now=now)
    assert next(r for r in rows if r['not'] == 'notes/A.md')['yerine_gecen'] == ['notes/B.md']
    assert not next(r for r in rows if r['not'] == 'notes/B.md')['eskimis']


def test_tekrar_overlap_yeterli_degil():
    notes = [{'not': 'notes/A.md', 'metadata': {'id': 'a'}},
             {'not': 'notes/B.md', 'metadata': {'id': 'b'}}]
    pair = {'older_note': 'a', 'newer_note': 'b', 'overlap': 1}
    assert tekrarlar(notes, {'findings': [pair]}) == {}
    assert tekrarlar(notes, {'findings': [{**pair, 'jaccard': 0.49}]}) == {}
    assert tekrarlar(notes, {'findings': [{**pair, 'jaccard': 0.5}]}) == {
        'notes/A.md': ['notes/B.md'], 'notes/B.md': ['notes/A.md']}


def test_soru_seti_ve_gecersiz_satir(tmp_path):
    path = tmp_path / 'sorular.jsonl'
    question = {'id': 's1', 'soru': 'Nerede?', 'proje': None, 'tur': 'anlam', 'beklenen': ['notes/A.md']}
    path.write_text(json.dumps(question) + '\n', encoding='utf-8')
    assert sorulari_oku(path) == [question]
    path.write_text(json.dumps(question) + '\n' + json.dumps(question), encoding='utf-8')
    with pytest.raises(ValueError, match=':2:'):
        sorulari_oku(path)
    path.write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match=':1:'):
        sorulari_oku(path)


def test_sonuc_secimi_karne_ve_bozuk_json_haric(tmp_path):
    score = tmp_path / '2026-10-22-1200.json'
    json_yaz(score, {'ozet': {}, 'sorular': []})
    json_yaz(tmp_path / 'karne-2026-10-22.json', {'notlar': []})
    (tmp_path / 'bozuk.json').write_text('{', encoding='utf-8')
    assert puan_dosyalari(tmp_path) == [score]


def test_cli_sync_bir_kez_ve_karsilastir(tmp_path, monkeypatch, capsys):
    question = {'id': 's1', 'soru': 'Nasıl?', 'proje': None,
                'tur': 'kelime', 'beklenen': ['notes/A.md']}
    questions = tmp_path / 'set.jsonl'
    questions.write_text(json.dumps(question) + '\n', encoding='utf-8')
    output = tmp_path / 'sonuclar'
    calls = []

    def fake_run(vault, python, args, json_bekle=True):
        calls.append((args, json_bekle))
        return ({'records': [{'source': 'notes/A.md'}]} if json_bekle else {}), None

    monkeypatch.setattr(puanla, 'beyin_calistir', fake_run)
    args = ['--vault', str(tmp_path), '--sorular', str(questions), '--sonuclar', str(output)]
    assert puanla.main(args + ['--sync']) == 0
    assert [c[0] for c in calls] == [['sync'], context_argumanlari(question)]
    assert calls[0][1] is False
    calls.clear()
    assert puanla.main(args + ['--karsilastir']) == 0
    assert [c[0] for c in calls] == [context_argumanlari(question)]
    assert len(puan_dosyalari(output)) == 2
    assert 'MRR +0.000' in capsys.readouterr().out


def test_yaml_proje_liste_parse_ve_arsiv_notu_atlanir(tmp_path):
    path = tmp_path / 'notes' / 'A.md'
    path.parent.mkdir()
    path.write_text('---\nstatus: archived (2026)\nproject: evrim\nsupersedes:\n  - eski-id\n'
                    'facts:\n  dogrulama: yerel test\n---\nGövde', encoding='utf-8')
    notes, warnings = notlari_oku(tmp_path)
    assert not warnings
    assert notes[0]['metadata']['supersedes'] == ['eski-id']
    assert notes[0]['metadata']['project'] == 'evrim'
    assert karne.arsivli_mi(notes[0])
    now = datetime.now(UTC)
    rows = karne_hesapla(notes, [], {'notes/A.md': (now, 'mtime')}, now=now)
    assert rows == []


def test_beyin_parser_ve_resolver_import_onceligi(tmp_path, monkeypatch):
    monkeypatch.setenv('HATIR_KASA_KODU', '1')
    scripts = tmp_path / '.claude' / 'scripts'
    scripts.mkdir(parents=True)
    parser = lambda text: ({'project': 'beyin-parser'}, text)
    resolver = lambda records: (set(), [])
    modules = {
        'beyin_v3': SimpleNamespace(__file__=str(scripts / 'beyin_v3.py'),
                                  resolve_supersedes=resolver, RETIRED_STATUSES={'ornek'}, _status_word=lambda r: 'ornek'),
        'beyin_v3_sync': SimpleNamespace(__file__=str(scripts / 'beyin_v3_sync.py'), parse=parser)}

    def fake_import(name):
        assert str(scripts) in sys.path
        return modules[name]

    monkeypatch.setattr(karne.importlib, 'import_module', fake_import)
    functions = karne.beyin_islevleri(tmp_path)
    assert functions['parse'] is parser
    assert functions['resolve_supersedes'] is resolver
    assert functions['retired'] == {'ornek'}
    assert on_bilgi('metin', functions['parse'])[0]['project'] == 'beyin-parser'
    assert str(scripts) not in sys.path


def test_beyin_import_edilemezse_yedek(tmp_path, monkeypatch):
    (tmp_path / '.claude' / 'scripts').mkdir(parents=True)

    def fail(name):
        raise ImportError(name)

    monkeypatch.setattr(karne.importlib, 'import_module', fail)
    functions = karne.beyin_islevleri(tmp_path)
    assert functions['parse'] is None
    assert on_bilgi('---\nproject: yedek\n---\nGövde', functions['parse'])[0]['project'] == 'yedek'
    assert functions['resolve_supersedes'] is karne.resolve_supersedes


@pytest.mark.parametrize('reference', ['notes/A', 'NOTES/a.MD', '[[A]]', '[[A#b|c]]',
                                      '[[notes/A^blok|takma]]', 'A.md', 'A'])
def test_supersedes_insan_yolu_ve_wiki(reference):
    notes = [{'not': 'notes/A.md', 'metadata': {}, 'govde': ''},
             {'not': 'notes/B.md', 'metadata': {'supersedes': [reference]}, 'govde': ''}]
    assert karne.yerine_gecenler(notes) == {'notes/A.md': {'notes/B.md'}}


def test_md_id_supersedes():
    expected = 'md-' + hashlib.sha256('notes/Örnek.md'.encode()).hexdigest()[:24]
    notes = [{'not': 'notes/Örnek.md', 'metadata': {}, 'govde': ''},
             {'not': 'notes/B.md', 'metadata': {'supersedes': expected}, 'govde': ''}]
    assert karne.not_id(notes[0]) == expected
    assert karne.yerine_gecenler(notes) == {'notes/Örnek.md': {'notes/B.md'}}
    now = datetime.now(UTC)
    rows = karne_hesapla(notes, [], {n['not']: (now, 'mtime') for n in notes}, now=now)
    target = next(r for r in rows if r['not'] == 'notes/Örnek.md')
    assert target['eskimis'] and target['id'] == expected


def test_supersedes_belirsiz_ad_ve_untrusted():
    notes = [{'not': 'notes/A.md', 'metadata': {}, 'govde': ''},
             {'not': 'knowledge/A.md', 'metadata': {}, 'govde': ''},
             {'not': 'notes/B.md', 'metadata': {'supersedes': '[[A]]'}, 'govde': ''}]
    assert karne.yerine_gecenler(notes) == {}
    notes[2]['metadata'] = {'supersedes': 'notes/A', 'trust': 'untrusted'}
    assert karne.yerine_gecenler(notes) == {}


@pytest.mark.parametrize('value', ['archived (2026)', 'ArŞİv (eski)', 'GEÇERSİZ: eski', 'deprecated'])
def test_status_ilk_kelime_turkce(value):
    assert karne.status_word({'status': value}) in karne.RETIRED_STATUSES


def test_supersedes_ile_eskiyen_canli_not_satir_uretir_eskimis():
    notes = [{'not': 'notes/A.md', 'metadata': {'id': 'a'}, 'govde': ''},
             {'not': 'notes/B.md', 'metadata': {'supersedes': ['a']}, 'govde': ''}]
    now = datetime.now(UTC)
    rows = karne_hesapla(notes, [], {n['not']: (now, 'mtime') for n in notes}, now=now)
    assert len(rows) == 2
    target = next(r for r in rows if r['not'] == 'notes/A.md')
    assert target['eskimis'] and target['puan'] == 20
    assert 'arşivle' in target['oneriler']
    assert not next(r for r in rows if r['not'] == 'notes/B.md')['eskimis']


def test_arsivli_notun_supersedes_canliyi_eskitir(tmp_path):
    not_yaz(tmp_path, 'notes/A.md', {'id': 'a'}, 'İçerik')
    not_yaz(tmp_path, 'notes/B.md', {'id': 'b', 'status': 'archived', 'supersedes': ['a']}, '')
    notes, _ = notlari_oku(tmp_path)
    now = datetime.now(UTC)
    rows = karne_hesapla(notes, [], {n['not']: (now, 'mtime') for n in notes}, now=now)
    assert [r['not'] for r in rows] == ['notes/A.md']
    assert rows[0]['eskimis'] and rows[0]['puan'] == 20
    assert rows[0]['yerine_gecen'] == ['notes/B.md']


def test_arsivli_not_govdesi_baglanti_kaynagi_sayilmaz(tmp_path):
    not_yaz(tmp_path, 'knowledge/A.md', {'id': 'a'}, 'İçerik')
    not_yaz(tmp_path, 'notes/Eski.md', {'status': 'archived'}, '[[A]] knowledge/A.md')
    not_yaz(tmp_path, 'notes/Canli.md', {}, '[[A]]')
    notes, _ = notlari_oku(tmp_path)
    now = datetime.now(UTC)
    rows = karne_hesapla(notes, [], {n['not']: (now, 'mtime') for n in notes}, now=now)
    target = next(r for r in rows if r['not'] == 'knowledge/A.md')
    assert target['baglanti_sayisi'] == 1
    assert 'notes/Eski.md' not in {r['not'] for r in rows}


def test_karne_cli_arsivli_atlanan_json_ve_markdown(tmp_path, monkeypatch):
    not_yaz(tmp_path, 'knowledge/A.md', {'id': 'a', 'project': 'p'}, 'İçerik')
    not_yaz(tmp_path, 'notes/Eski.md', {'status': 'archived'}, 'Eski gövde')
    monkeypatch.setattr(karne, 'beyin_calistir', lambda *args, **kwargs: ({'findings': []}, None))
    out = tmp_path / 'sonuclar'
    assert karne.main(['--vault', str(tmp_path), '--cikti', str(out)]) == 0
    result = json_oku(next(out.glob('karne-*.json')))
    assert result['arsivli_atlanan'] == 1
    assert [r['not'] for r in result['notlar']] == ['knowledge/A.md']
    text = next(out.glob('karne-*.md')).read_text(encoding='utf-8')
    assert 'Arşivli olduğu için atlanan not: 1' in text


def test_notlar_stem_yanlis_pozitif_degil():
    assert anilma_sayisi('Notlar notluk anot x/Not Not Not.md', 'notes/Not.md') == 2
    assert anilma_sayisi('[[Notlar]] [[NOT|takma]] [[dir/Not#başlık]] [[Not^blok]]',
                         'notes/Not.md', wiki=True) == 3


def test_nfc_nfd_yol_ve_gorunum():
    source = 'notes/Öğrenim.md'
    decomposed = unicodedata.normalize('NFD', source)
    assert yol(decomposed.upper()) == yol(source)
    assert kaynaklar([{'source': decomposed}, {'source': source.upper()}]) == [decomposed]
    assert soru_metrikleri([source], [decomposed.upper()])['sira'] == 1
    assert anilma_sayisi(unicodedata.normalize('NFD', 'ÖĞRENIM'), source) == 1
    assert bulunabilirlik_sinyalleri({'k': 2, 'sorular': [
        {'beklenen': [decomposed], 'sira': 2}]}) == {yol(source): 1}


def test_hatali_soru_ozet_ve_bulunabilirlik():
    rows = [{'tur': 'kelime', 'hata': 'zaman aşımı', 'metrikler': soru_metrikleri(['a'], [])},
            {'tur': 'kelime', 'hata': None, 'metrikler': soru_metrikleri(['a'], ['a'])}]
    summary = ozetle(rows)['genel']
    assert summary['adet'] == 2 and summary['hatali'] == 1
    assert summary['MRR'] == 1 and summary['hit@5'] == 1
    assert bulunabilirlik_sinyalleri({'k': 2, 'sorular': [
        {'beklenen': ['notes/A.md'], 'hata': 'izin', 'sira': 1}]}) == {}


@pytest.mark.parametrize('all_error', [True, False])
def test_cli_hatali_cikis_ve_ilk_k(tmp_path, monkeypatch, capsys, all_error):
    questions = tmp_path / 'set.jsonl'
    base = {'soru': 'Nasıl?', 'tur': 'kelime', 'beklenen': ['notes/B.md']}
    questions.write_text('\n'.join(json.dumps({**base, 'id': key}) for key in ('s1', 's2')), encoding='utf-8')
    responses = iter([(None, 'izin engeli'), (None, 'zaman aşımı') if all_error else
                      ({'records': [{'source': 'notes/A.md'}, {'source': 'notes/B.md'}, {'source': 'notes/C.md'}]}, None)])
    monkeypatch.setattr(puanla, 'beyin_calistir', lambda *args: next(responses))
    output = tmp_path / 'sonuclar'
    assert puanla.main(['--vault', str(tmp_path), '--sorular', str(questions),
                        '--sonuclar', str(output), '--k', '2']) == int(all_error)
    result = json.loads(next(output.glob('*.json')).read_text(encoding='utf-8'))
    assert result['ozet']['genel']['hatali'] == (2 if all_error else 1)
    assert 'soru hatalı, ölçüm eksik' in capsys.readouterr().out
    if not all_error:
        assert result['sorular'][1]['ilk_k'] == ['notes/A.md', 'notes/B.md']
        assert result['ozet']['genel']['hit@2'] == 1


def test_karsilastir_yalniz_ortak_sorular_ve_ayni_k():
    old = {'k': 5, 'sorular': [{'id': 'ortak', 'tur': 'kelime', 'sira': 3},
                              {'id': 'eski', 'tur': 'anlam', 'sira': 1}]}
    new = {'k': 2, 'sorular': [{'id': 'ortak', 'tur': 'kelime', 'sira': 2},
                              {'id': 'yeni', 'tur': 'anlam', 'sira': None}]}
    a, b, warnings = karsilastirma(new, old)
    assert a['genel']['adet'] == b['genel']['adet'] == 1
    assert a['genel']['hit@2'] == 1 and b['genel']['hit@2'] == 0
    assert len(warnings) == 2 and 'id' in warnings[0] and 'k farklı' in warnings[1]
    new['sorular'][0]['hata'] = 'izin'
    a, b, warnings = karsilastirma(new, old)
    assert a['genel']['adet'] == b['genel']['adet'] == 0
    assert 'Karşılaştırılabilir ortak soru yok.' in warnings


def test_regexler_not_basina_derlenir(monkeypatch):
    notes = [{'not': f'notes/{i}.md', 'metadata': {}, 'govde': '[[0]]'} for i in range(3)]
    now = datetime.now(UTC)
    original = karne.anilma_deseni
    calls = []

    def compile_once(source, wiki=False):
        calls.append((source, wiki))
        return original(source, wiki)

    monkeypatch.setattr(karne, 'anilma_deseni', compile_once)
    karne_hesapla(notes, ['0', '0', '0'], {n['not']: (now, 'mtime') for n in notes}, now=now)
    assert len(calls) == 2 * len(notes)
    assert len(set(calls)) == len(calls)


def test_cli_sync_hatasi_olcum_baslatmaz(tmp_path, monkeypatch):
    question = {'id': 's1', 'soru': 'Nasıl?', 'tur': 'kelime', 'beklenen': ['notes/A.md']}
    questions = tmp_path / 'set.jsonl'
    questions.write_text(json.dumps(question), encoding='utf-8')
    calls = []

    def fake_run(vault, python, args, json_bekle=True):
        calls.append(args)
        return {}, 'izin engeli'

    monkeypatch.setattr(puanla, 'beyin_calistir', fake_run)
    output = tmp_path / 'sonuclar'
    with pytest.raises(SystemExit) as exc:
        puanla.main(['--vault', str(tmp_path), '--sorular', str(questions),
                     '--sonuclar', str(output), '--sync'])
    assert exc.value.code == 1
    assert calls == [['sync']]
    assert not output.exists()


def test_context_argumanlari():
    projeli = {'soru': 'q', 'proje': 'p'}
    assert puanla.context_argumanlari(projeli) == ['context', 'q', '--project', 'p', '--no-sync', '--json']
    assert puanla.context_argumanlari({'soru': 'q', 'proje': None}) == ['context', 'q', '--no-sync', '--json']

def test_anlam_argumanlari():
    projeli = {'soru': 'q', 'proje': 'p'}
    assert puanla.anlam_argumanlari(projeli, 5) == ['ara', 'q', '--project', 'p', '--k', '10', '--json']
    assert puanla.anlam_argumanlari({'soru': 'q', 'proje': None}, 20, karma=True) == ['ara', 'q', '--k', '20', '--json', '--karma']


def test_cli_yontem_karma_ve_rerank_yerlesik(tmp_path, monkeypatch):
    question = {'id': 's1', 'soru': 'Nasıl?', 'proje': 'p', 'tur': 'anlam', 'beklenen': ['notes/A.md']}
    questions = tmp_path / 'set.jsonl'
    questions.write_text(json.dumps(question) + '\n', encoding='utf-8')
    calls = []

    def fake_anlam(vault, python, args, script=None):
        calls.append((args, script))
        return {'records': [{'source': 'notes/A.md'}], 'yontem': 'rerank', 'degraded': False}, None

    monkeypatch.setattr(puanla, 'anlam_calistir', fake_anlam)
    monkeypatch.setattr(puanla, 'beyin_calistir', lambda *a, **k: pytest.fail('beyin çağrılmamalı'))
    output = tmp_path / 'sonuclar'
    args = ['--vault', str(tmp_path), '--sorular', str(questions), '--sonuclar', str(output)]
    assert puanla.main(args + ['--yontem', 'karma']) == 0
    assert calls == [(puanla.anlam_argumanlari(question, 5, karma=True), None)]
    result = json_oku(puan_dosyalari(output)[-1])
    assert result['yontem'] == 'karma' and result['ozet']['genel']['hit@1'] == 1
    # Betik verilmezse rerank paket içi `ara --rerank` olur (betik zorunlu değil).
    calls.clear()
    assert puanla.main(args + ['--yontem', 'rerank']) == 0
    assert calls == [(puanla.anlam_argumanlari(question, 5, rerank=True), None)]
    assert calls[0][0][-1] == '--rerank'
    result = json_oku(puan_dosyalari(output)[-1])
    assert result['yontem'] == 'rerank' and result['sorular'][0]['hata'] is None


def test_yontem_beyin_kasa_betigini_cagirir(tmp_path, monkeypatch):
    question = {'id': 's1', 'soru': 'Nasıl?', 'proje': 'p', 'tur': 'kelime', 'beklenen': ['notes/A.md']}
    questions = tmp_path / 'set.jsonl'
    questions.write_text(json.dumps(question) + '\n', encoding='utf-8')
    calls = []
    monkeypatch.setattr(puanla, 'beyin_calistir',
                        lambda vault, python, args, json_bekle=True: (calls.append(args), ({'records': [{'source': 'notes/A.md'}]}, None))[1])
    assert puanla.main(['--vault', str(tmp_path), '--sorular', str(questions),
                        '--sonuclar', str(tmp_path / 'o'), '--yontem', 'beyin']) == 0
    assert calls == [context_argumanlari(question)]


def test_olumsuz_soru_yasak_ihlal_ve_set_suzgeci(tmp_path, monkeypatch, capsys):
    rows = [
        {'id': 'g1', 'soru': 'Güncel?', 'tur': 'kelime', 'beklenen': ['notes/Yeni.md'],
         'yasak': ['notes/Eski.md'], 'set': 'gerileme'},
        {'id': 'g2', 'soru': 'Kayıp?', 'tur': 'anlam', 'beklenen': ['notes/Yok.md'], 'set': 'gerileme'},
        {'id': 'n1', 'soru': 'Gizli?', 'tur': 'anlam', 'beklenen': [], 'yasak': ['notes/Gizli.md'],
         'set': 'gerileme'},
        {'id': 'y1', 'soru': 'Zor?', 'tur': 'anlam', 'beklenen': ['notes/Yeni.md'], 'set': 'yetenek'},
    ]
    questions = tmp_path / 'set.jsonl'
    questions.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    donus = {'Güncel?': ['notes/Yeni.md', 'notes/Eski.md'], 'Kayıp?': ['notes/X.md'],
             'Gizli?': ['notes/Y.md'], 'Zor?': ['notes/Yeni.md']}
    monkeypatch.setattr(puanla, 'beyin_calistir', lambda vault, python, args, json_bekle=True:
                        ({'records': [{'source': s} for s in donus[args[1]]]}, None))
    # Sonuç yolu da Bulunamayan'dan sonra basılır. Windows geçici klasör adları 'n1' alt dizgisi taşıyabilir.
    out = tmp_path / 'n1-yol'
    assert puanla.main(['--vault', str(tmp_path), '--sorular', str(questions), '--sonuclar', str(out),
                        '--set', 'gerileme']) == 0
    text = capsys.readouterr().out
    bulunamayan = text.split('Bulunamayan', 1)[1].split('Sonuç:', 1)[0]
    assert 'İHLAL g1: notes/Eski.md' in text and 'GERİLEDİ g2' in text and '- n1:' not in bulunamayan
    result = json_oku(puan_dosyalari(out)[-1])
    assert [r['id'] for r in result['sorular']] == ['g1', 'g2', 'n1']
    assert result['ozet']['olumsuz'] == {'adet': 2, 'ihlal': 1}
    genel = result['ozet']['genel']
    assert genel['adet'] == 2 and genel['hatali'] == 0 and genel['hit@5'] == 0.5  # n1 ne adet ne hatalı


def test_neden_sinifi_asama_ve_tekrar(tmp_path, monkeypatch, capsys):
    rows = [{'id': 'a', 'soru': 'A', 'tur': 'kelime', 'beklenen': ['notes/A.md']},
            {'id': 'b', 'soru': 'B', 'tur': 'anlam', 'beklenen': ['notes/B.md']},
            {'id': 'c', 'soru': 'C', 'tur': 'anlam', 'beklenen': ['notes/C.md']}]
    questions = tmp_path / 'set.jsonl'
    questions.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    sayac = {'n': 0}
    # a: bulunur; b: adaylarda var ama 6. sıra (siralama); c: adaylarda yok (aday_yok); a 2. koşuda kaçar.
    def sahte(vault, python, args, script=None):
        soru = args[0]
        sayac['n'] += 1
        ikinci = sayac['n'] > 3
        dolgu = [f'notes/x{i}.md' for i in range(5)]
        sonuc = {'A': (dolgu if ikinci else ['notes/A.md']) + ['notes/A.md'], 'B': dolgu + ['notes/B.md'], 'C': dolgu}[soru]
        adaylar = {'A': ['notes/A.md'], 'B': ['notes/B.md'], 'C': ['notes/x0.md']}[soru]
        return {'records': [{'source': s} for s in sonuc], 'adaylar': adaylar}, None
    monkeypatch.setattr(puanla, 'anlam_calistir', sahte)
    monkeypatch.delenv('HATIR_RERANK_ONBELLEK', raising=False)
    out = tmp_path / 'o'
    assert puanla.main(['--vault', str(tmp_path), '--sorular', str(questions), '--sonuclar', str(out),
                        '--yontem', 'rerank', '--rerank-betigi', str(tmp_path / 'ara.py'), '--tekrar', '2']) == 0
    text = capsys.readouterr().out
    result = json_oku(puan_dosyalari(out)[-1])
    assert result['ozet']['nedenler'] == {'aday_yok': 1, 'siralama': 1}
    assert result['ozet']['asama']['aday_kapsama'] == 2 / 3
    assert result['ozet']['tekrar']['kararsiz'] == ['a'] and 'kararsız soru 1: a' in text
    assert puanla.os.environ.get('HATIR_RERANK_ONBELLEK') == '0'
    monkeypatch.delenv('HATIR_RERANK_ONBELLEK', raising=False)


def test_bos_beklenen_yalniz_yasakla_gecerli(tmp_path):
    path = tmp_path / 's.jsonl'
    path.write_text(json.dumps({'id': 'a', 'soru': 'x', 'tur': 'anlam', 'beklenen': []}) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='beklenen'):
        sorulari_oku(path)
    path.write_text(json.dumps({'id': 'a', 'soru': 'x', 'tur': 'anlam', 'beklenen': ['a.md'], 'set': 'kolay'}) + '\n',
                    encoding='utf-8')
    with pytest.raises(ValueError, match='set'):
        sorulari_oku(path)


@pytest.mark.parametrize('ikinci_hata', [None, 'zaman aşımı'])
def test_tekrar_ikinci_kosunun_ihlali_ve_hatasi_ozetindedir(tmp_path, monkeypatch, capsys, ikinci_hata):
    rows = [{'id': 'n', 'soru': 'Gizli?', 'tur': 'anlam', 'beklenen': [], 'yasak': ['notes/Gizli.md']},
            {'id': 'p', 'soru': 'A?', 'tur': 'kelime', 'beklenen': ['notes/A.md']}]
    questions = tmp_path / 's.jsonl'
    questions.write_bytes(('\n'.join(json.dumps(r) for r in rows) + '\n').encode())
    responses = iter([({'records': []}, None), ({'records': [{'source': 'notes/A.md'}]}, None),
                      ({'records': [{'source': 'notes/Gizli.md'}]}, ikinci_hata),
                      ({'records': [{'source': 'notes/A.md'}]}, 'pozitif hata')])
    monkeypatch.setattr(puanla, 'beyin_calistir', lambda *a: next(responses))
    out = tmp_path / 'o'
    assert puanla.main(['--vault', str(tmp_path), '--sorular', str(questions), '--sonuclar', str(out),
                        '--tekrar', '2']) == int(bool(ikinci_hata))
    result = json_oku(puan_dosyalari(out)[-1])
    summary = result['ozet']
    assert summary['olumsuz'] == {'adet': 1, 'ihlal': 1}
    assert summary['hatali'] == 1 + bool(ikinci_hata)
    assert summary['genel']['hatali'] == 1
    assert summary['olumsuz_hatali'] == int(bool(ikinci_hata))
    assert result['sorular'][0]['yasak_ihlal'] == ['notes/Gizli.md']
    tekrar = summary['tekrar']
    assert tekrar['olumsuz_ihlal'] == {'ortalama': 0.5, 'min': 0, 'max': 1}
    assert tekrar['olumsuz_ihlal_kararsiz'] == ['n']
    assert tekrar['hatali']['min'] == 0 and tekrar['hatali']['max'] == 1 + bool(ikinci_hata)
    assert tekrar['hatali_kararsiz'] == (['n', 'p'] if ikinci_hata else ['p'])
    text = capsys.readouterr().out
    assert 'İHLAL n: notes/Gizli.md' in text and 'Tekrar hatali:' in text


@pytest.mark.parametrize('pozitif_var', [False, True])
def test_olumsuz_calistirma_hatasi_sayilir_yazilir_ve_cikis_bir(tmp_path, monkeypatch, capsys, pozitif_var):
    rows = [{'id': 'n', 'soru': 'Gizli?', 'tur': 'anlam', 'beklenen': [], 'yasak': ['notes/Gizli.md']}]
    if pozitif_var:
        rows.append({'id': 'p', 'soru': 'A?', 'tur': 'kelime', 'beklenen': ['notes/A.md']})
    questions = tmp_path / 's.jsonl'
    questions.write_bytes(('\n'.join(json.dumps(r) for r in rows)).encode())
    def sahte(vault, python, args):
        return (None, 'olumsuz zaman aşımı') if args[1] == 'Gizli?' else ({'records': [{'source': 'notes/A.md'}]}, None)
    monkeypatch.setattr(puanla, 'beyin_calistir', sahte)
    out = tmp_path / 'o'
    assert puanla.main(['--vault', str(tmp_path), '--sorular', str(questions), '--sonuclar', str(out)]) == 1
    summary = json_oku(puan_dosyalari(out)[-1])['ozet']
    assert summary['olumsuz_hatali'] == 1 and summary['genel']['hatali'] == 0
    text = capsys.readouterr().out
    assert 'hatalı: 1' in text and 'HATA n: olumsuz zaman aşımı' in text


def test_karne_dongu_ve_valid_to_cli(tmp_path, monkeypatch):
    not_yaz(tmp_path, 'notes/A.md', {'id': 'a', 'supersedes': ['b'], 'valid_to': '2026-10-01'}, 'a')
    not_yaz(tmp_path, 'notes/B.md', {'id': 'b', 'supersedes': ['a'], 'facts': {'valid_to': 'bozuk'}}, 'b')
    not_yaz(tmp_path, 'notes/C.md', {'id': 'c', 'facts': {'gecerlilik': 'valid_to: 2026-12-01'}}, 'c')
    monkeypatch.setattr(karne, 'beyin_calistir', lambda *args, **kwargs: ({'findings': []}, None))
    out = tmp_path / 'sonuclar'
    assert karne.main(['--vault', str(tmp_path), '--cikti', str(out), '--bugun', '2026-10-31']) == 0
    result = json_oku(next(out.glob('karne-*.json')))
    assert result['supersedes_donguleri'] == [['notes/A.md', 'notes/B.md']]
    rows = {r['not']: r for r in result['notlar']}
    assert rows['notes/A.md']['supersedes_dongu'] is True
    assert rows['notes/A.md']['valid_to_durum'] == 'gecmis' and rows['notes/A.md']['valid_to'] == '2026-10-01'
    assert any(o.startswith('döngü') for o in rows['notes/A.md']['oneriler'])
    assert any(o.startswith('süresi geçmiş') for o in rows['notes/A.md']['oneriler'])
    assert rows['notes/B.md']['valid_to_durum'] == 'gecersiz'
    assert any(o.startswith('valid_to geçersiz') for o in rows['notes/B.md']['oneriler'])
    assert rows['notes/C.md']['supersedes_dongu'] is False and rows['notes/C.md']['valid_to_durum'] == 'gecerli'
    text = next(out.glob('karne-*.md')).read_text(encoding='utf-8')
    assert 'Supersedes döngüsü: 1; süresi geçmiş not: 1' in text


def test_karne_dongu_emekli_not_dahil_satirsiz(tmp_path):
    notes = [{'not': 'notes/A.md', 'metadata': {'id': 'a', 'supersedes': ['b'], 'status': 'archived'}, 'govde': ''},
             {'not': 'notes/B.md', 'metadata': {'id': 'b', 'supersedes': ['a']}, 'govde': ''}]
    assert karne.supersedes_donguleri(karne.yerine_gecenler(notes)) == [['notes/A.md', 'notes/B.md']]
    now = datetime.now(UTC)
    rows = karne_hesapla(notes, [], {n['not']: (now, 'mtime') for n in notes}, now=now)
    assert [r['not'] for r in rows] == ['notes/B.md'] and rows[0]['supersedes_dongu'] is True


def test_karne_dongu_eskimis_saymaz_dongusuz_sayar():
    notes = [{'not': 'notes/A.md', 'metadata': {'id': 'a', 'supersedes': ['b']}, 'govde': ''},
             {'not': 'notes/B.md', 'metadata': {'id': 'b', 'supersedes': ['a']}, 'govde': ''},
             {'not': 'notes/C.md', 'metadata': {'id': 'c', 'supersedes': ['d']}, 'govde': ''},
             {'not': 'notes/D.md', 'metadata': {'id': 'd'}, 'govde': ''}]
    now = datetime.now(UTC)
    rows = {r['not']: r for r in karne_hesapla(notes, [], {n['not']: (now, 'mtime') for n in notes}, now=now)}
    for ad in ('notes/A.md', 'notes/B.md'):
        assert rows[ad]['eskimis'] is False and rows[ad]['supersedes_dongu'] is True
        assert rows[ad]['yerine_gecen'] == [] and 'arşivle' not in rows[ad]['oneriler']
    assert rows['notes/D.md']['eskimis'] is True and rows['notes/D.md']['yerine_gecen'] == ['notes/C.md']


# --- ayir ---

def q(i, **kw):
    return {'id': i, 'soru': i, 'tur': 'kelime', 'beklenen': ['a.md'], **kw}


def test_hepsinde_gecen_gerilemeye_gecer_digeri_yetenek():
    sorular = [q('a'), q('b'), q('c', altin_supheli='x')]
    mezun, sayac = ayir.ayir(sorular, [{'a': 1, 'b': 2, 'c': 1}, {'a': 3, 'b': None, 'c': 1}])
    assert [s['set'] for s in sorular] == ['gerileme', 'yetenek', 'yetenek']
    assert sayac == {'gerileme': 1, 'yetenek': 2} and mezun == []


def test_yetenekten_mezuniyet_ve_gerileme_dusurulmez():
    sorular = [q('a', set='yetenek'), q('b', set='gerileme')]
    mezun, _ = ayir.ayir(sorular, [{'a': 1, 'b': None}])
    assert mezun == ['a'] and sorular[1]['set'] == 'gerileme'
    ayir.ayir(sorular, [{'a': 1, 'b': None}], yeniden=True)
    assert sorular[1]['set'] == 'yetenek'


def test_olumsuz_hep_gerileme_ve_sonucta_olmayan_yetenek():
    sorular = [q('n', beklenen=[], yasak=['x.md']), q('yok')]
    ayir.ayir(sorular, [{'n': None}])
    assert [s['set'] for s in sorular] == ['gerileme', 'yetenek']


def test_cli_satir_sonunu_korur(tmp_path):
    sonuc = tmp_path / 'r.json'
    sonuc.write_text(json.dumps({'sorular': [{'id': 'a', 'sira': 1}]}), encoding='utf-8')
    for son in (b'\n', b'\r\n'):
        path = tmp_path / 's.jsonl'
        path.write_bytes(json.dumps(q('a'), ensure_ascii=False).encode() + son)
        assert ayir.main(['--sorular', str(path), '--sonuc', str(sonuc)]) == 0
        ham = path.read_bytes()
        assert ham.endswith(son) and (son == b'\r\n' or b'\r' not in ham)
        assert json.loads(ham)['set'] == 'gerileme'



def test_mezuniyet_isabet_yaninda_hatasiz_ve_yasaksiz_olmali(tmp_path):
    sonuc = tmp_path / 'r.json'
    rows = [{'id': 'temiz', 'sira': 1, 'hata': None, 'yasak_ihlal': []},
            {'id': 'yasak', 'sira': 1, 'hata': None, 'yasak_ihlal': ['gizli.md']},
            {'id': 'hata', 'sira': 1, 'hata': 'zaman aşımı', 'yasak_ihlal': []}]
    sonuc.write_bytes(json.dumps({'sorular': rows}).encode())
    siralar = ayir.siralar(sonuc)
    assert siralar['yasak']['yasak_ihlal'] == ['gizli.md']
    assert siralar['hata']['hata'] == 'zaman aşımı'
    sorular = [q(i, set='yetenek') for i in ('temiz', 'yasak', 'hata')]
    temiz_onceki = {i: {'sira': 1, 'hata': None, 'yasak_ihlal': []} for i in siralar}
    mezun, sayac = ayir.ayir(sorular, [temiz_onceki, siralar])
    assert mezun == ['temiz'] and sayac == {'gerileme': 1, 'yetenek': 2}


# --- gecerlilik ---

BUGUN = date(2026, 10, 31)


def test_dongu_karsilikli():
    assert donguleri_bul({'A': {'B'}, 'B': ['A']}) == [['A', 'B']]


def test_dongu_uclu():
    assert donguleri_bul({'A': {'B'}, 'B': {'C'}, 'C': {'A'}}) == [['A', 'B', 'C']]


def test_dongu_zincir_rapor_yok():
    assert donguleri_bul({'A': {'B'}, 'B': {'C'}}) == []


def test_dongu_kendine_kenar_yok_sayilir():
    assert donguleri_bul({'A': {'A'}, 'B': {'B', 'C'}}) == []


def test_dongu_iki_ayri_ve_deterministik():
    kenar = {'D': {'C'}, 'C': {'D'}, 'B': {'A'}, 'A': {'B', 'X'}}
    assert donguleri_bul(kenar) == [['A', 'B'], ['C', 'D']]


def test_dongu_5000_dugum_zincir_recursion_yok():
    kenar = {str(i): {str(i + 1)} for i in range(5000)}
    assert donguleri_bul(kenar) == []
    kenar['5000'] = {'0'}
    assert len(donguleri_bul(kenar)) == 1


def test_valid_to_gecmis():
    s = valid_to_oku({'valid_to': '2026-10-30'}, BUGUN)
    assert s == {'valid_to': '2026-10-30', 'kaynak': 'alan', 'durum': 'gecmis', 'ham': None}


def test_valid_to_gelecek():
    assert valid_to_oku({'valid_to': '2026-11-01'}, BUGUN)['durum'] == 'gecerli'


def test_valid_to_bugun_gecerli():
    assert valid_to_oku({'valid_to': '2026-10-31'}, BUGUN)['durum'] == 'gecerli'


def test_valid_to_yok():
    s = valid_to_oku({'facts': {'gecerlilik': 'valid_from: 2026-10-31; n>=8 ölçümüne kadar'}}, BUGUN)
    assert s == {'valid_to': None, 'kaynak': None, 'durum': 'yok', 'ham': None}
    assert valid_to_oku({}, BUGUN)['durum'] == 'yok'


def test_valid_to_gecersiz():
    s = valid_to_oku({'valid_to': 'yarın'}, BUGUN)
    assert s == {'valid_to': None, 'kaynak': 'alan', 'durum': 'gecersiz', 'ham': 'yarın'}
    assert valid_to_oku({'valid_to': '2026-13-45'}, BUGUN)['durum'] == 'gecersiz'


def test_valid_to_facts():
    s = valid_to_oku({'facts': {'valid_to': '2026-01-01'}}, BUGUN)
    assert s['kaynak'] == 'facts' and s['durum'] == 'gecmis'


def test_valid_to_gecerlilik_metninden():
    s = valid_to_oku({'facts': {'gecerlilik': 'valid_from: 2026-01-01; valid_to: 2026-12-31 (tahmini)'}}, BUGUN)
    assert s == {'valid_to': '2026-12-31', 'kaynak': 'gecerlilik', 'durum': 'gecerli', 'ham': None}
    assert valid_to_oku({'facts': {'gecerlilik': 'valid_to=2026-01-02'}}, BUGUN)['durum'] == 'gecmis'


def test_valid_to_ust_duzey_oncelik_ve_gecersiz_dusmez():
    meta = {'valid_to': '2030-01-01', 'facts': {'valid_to': '2020-01-01'}}
    assert valid_to_oku(meta, BUGUN)['kaynak'] == 'alan'
    meta = {'valid_to': 'bozuk', 'facts': {'valid_to': '2020-01-01'}}
    assert valid_to_oku(meta, BUGUN)['durum'] == 'gecersiz'
    meta = {'facts': {'valid_to': 'bozuk', 'gecerlilik': 'valid_to: 2020-01-01'}}
    assert valid_to_oku(meta, BUGUN)['durum'] == 'gecersiz'


def test_valid_to_iso_datetime_ve_date_nesnesi():
    assert valid_to_oku({'valid_to': '2026-10-30T12:00:00Z'}, BUGUN)['valid_to'] == '2026-10-30'
    assert valid_to_oku({'valid_to': date(2026, 11, 5)}, BUGUN)['durum'] == 'gecerli'


def test_valid_to_gecerlilik_simge_siki():
    def oku(v):
        return valid_to_oku({'facts': {'gecerlilik': 'valid_to: ' + v}}, BUGUN)
    assert oku('2026-10-301')['durum'] == 'gecersiz' and oku('2026-10-301')['ham'] == '2026-10-301'
    assert oku('2026-10-30T99:99:99')['durum'] == 'gecersiz'
    assert oku('2026-12-31 (tahmini)')['valid_to'] == '2026-12-31' and oku('2026-12-31 (tahmini)')['durum'] == 'gecerli'
    s = oku('2026-10-30T15:00:00Z')
    assert s['valid_to'] == '2026-10-30' and s['durum'] == 'gecmis'


def test_valid_to_gecerlilik_metin_ortasinda_ve_ayirac():
    s = valid_to_oku({'facts': {'gecerlilik': 'valid_from: 2026-01-01; valid_to: 2026-11-05, sonra gözden geçir'}}, BUGUN)
    assert s['valid_to'] == '2026-11-05' and s['durum'] == 'gecerli'


def test_valid_to_gecerlilik_ek_metin_ve_kucuk_z():
    def oku(v):
        return valid_to_oku({'facts': {'gecerlilik': 'valid_to: ' + v}}, BUGUN)
    for v in ("2026-12-31'e kadar", '2026-12-31(tahmini)', '2026-12-31/2027-01-01'):
        s = oku(v)
        assert s['valid_to'] == '2026-12-31' and s['durum'] == 'gecerli', v
    s = oku('2026-12-31t00:00:00z')
    assert s['valid_to'] == '2026-12-31' and s['durum'] == 'gecerli'
    assert oku('2026-10-301')['durum'] == 'gecersiz'
    assert oku('2026-10-30T99:99:99')['durum'] == 'gecersiz'


def test_valid_to_gecerlilik_z_siki():
    def oku(v):
        return valid_to_oku({'facts': {'gecerlilik': 'valid_to: ' + v}}, BUGUN)
    for v in ('2026-12-31t00:00:00zz', '2026-12-31T00:00:00xz', '2026-12-31T00:00:00Zz',
              '2026-12-31T00:00:00+03:00z', '2026-12-31T00:00:00Zx'):
        s = oku(v)
        assert s['durum'] == 'gecersiz' and s['ham'] == v, v
    for v in ('2026-12-31t00:00:00z', '2026-12-31T00:00:00Z', '2026-12-31T00:00:00.5z'):
        s = oku(v)
        assert s['valid_to'] == '2026-12-31' and s['durum'] == 'gecerli', v
    assert valid_to_oku({'valid_to': '2026-12-31T00:00:00xz'}, BUGUN)['durum'] == 'gecersiz'


def test_valid_to_bos_kesirli_saniye_siki():
    for v in ('2026-12-31T00:00:00.z', '2026-12-31T00:00:00.Z'):
        s = valid_to_oku({'valid_to': v}, BUGUN)
        assert s == {'valid_to': None, 'kaynak': 'alan', 'durum': 'gecersiz', 'ham': v}, v
        s = valid_to_oku({'facts': {'gecerlilik': 'valid_to: ' + v}}, BUGUN)
        assert s == {'valid_to': None, 'kaynak': 'gecerlilik', 'durum': 'gecersiz', 'ham': v}, v
    for v in ('2026-12-31T00:00:00.5z', '2026-12-31T00:00:00z', '2026-12-31T00:00:00Z'):
        s = valid_to_oku({'valid_to': v}, BUGUN)
        assert s['valid_to'] == '2026-12-31' and s['durum'] == 'gecerli', v
        s = valid_to_oku({'facts': {'gecerlilik': 'valid_to: ' + v}}, BUGUN)
        assert s['valid_to'] == '2026-12-31' and s['durum'] == 'gecerli', v


def test_valid_to_katı_dilbilgisi_ve_nokta():
    def alan(v):
        return valid_to_oku({'valid_to': v}, BUGUN)

    def metin(v):
        return valid_to_oku({'facts': {'gecerlilik': 'valid_to: ' + v}}, BUGUN)
    for v in ('2026-12-31T00:00:00,Z', '2026-12-31T00:00:00,+00:00', '2026-12-31T00:00,Z',
              '2026-12-31T00:00:00.', '2026-12-31T00:00:00.Z', '2026-12-31T24', '2026-12-31T00:00:00+0300'):
        assert alan(v)['durum'] == 'gecersiz' and alan(v)['ham'] == v, v
        assert metin(v.replace(',', '#'))['durum'] == 'gecersiz', v
    for v in ('2026-12-31T00:00:00.', '2026-12-31T00:00:00. devam', '2026-12-31T00:00:00., not',
              '2026-12-31T00:00:00..'):
        assert metin(v)['durum'] == 'gecersiz', v
    for v in ('2026-12-31.', '2026-12-31T00:00:00Z.', '2026-12-31T00:00:00.5.', '2026-12-31T00:00.',
              '2026-12-31T00:00:00+03:00.', '2026-12-31. devam'):
        s = metin(v)
        assert s['valid_to'] == '2026-12-31' and s['durum'] == 'gecerli', v
    assert alan('2026-12-31T00:00:00.5z')['durum'] == 'gecerli'


def test_valid_to_bosluklu_tarih_saat():
    for v in ('2026-12-31 10:00:00', '2026-12-31 10:00'):
        for meta in ({'valid_to': v}, {'facts': {'valid_to': v}}):
            s = valid_to_oku(meta, BUGUN)
            assert s['valid_to'] == '2026-12-31' and s['durum'] == 'gecerli', (v, meta)
    assert valid_to_oku({'valid_to': '2026-12-31  10:00'}, BUGUN)['durum'] == 'gecersiz'


# --- Paket kopyasına özgü: vault ve varsayılanlar ---

def _soru_dosyasi(tmp_path):
    path = tmp_path / 'set.jsonl'
    path.write_text(json.dumps({'id': 's1', 'soru': 'Nasıl?', 'proje': None, 'tur': 'kelime',
                                'beklenen': ['notes/A.md']}) + '\n', encoding='utf-8')
    return path


def test_puanla_vault_yoksa_hata_ortamdan_gelirse_varsayilan_sonuclar(tmp_path, monkeypatch):
    questions = _soru_dosyasi(tmp_path)
    monkeypatch.delenv('HATIR_KASA', raising=False)
    with pytest.raises(SystemExit):
        puanla.main(['--sorular', str(questions)])
    with pytest.raises(SystemExit):
        puanla.main(['--vault', str(tmp_path)])  # --sorular zorunlu
    monkeypatch.setenv('HATIR_KASA', str(tmp_path))
    monkeypatch.setattr(puanla, 'beyin_calistir',
                        lambda vault, python, args, json_bekle=True: ({'records': [{'source': 'notes/A.md'}]}, None))
    assert puanla.main(['--sorular', str(questions)]) == 0
    assert puan_dosyalari(tmp_path / '.hatir' / 'sonuclar')


def test_anlam_betigi_paket_icinden(monkeypatch):
    assert puanla.ANLAM_BETIK.name == 'anlam.py' and puanla.ANLAM_BETIK.is_file()
    assert puanla.ANLAM_BETIK.parent.name == 'anlam'
    gorulen = []

    def fake_run(cmd, **kw):
        gorulen.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout='{"records": []}', stderr='')
    monkeypatch.setattr(puanla.subprocess, 'run', fake_run)
    data, hata = puanla.anlam_calistir(Path('.'), 'py', ['ara', 'q'])
    assert data == {'records': []} and hata is None
    # Göreli içe aktarmalı modül betik olarak çalışmaz; `-m` ve paket kökü PYTHONPATH'te.
    cmd, kw = gorulen[0]
    assert cmd[:3] == ['py', '-m', 'hatir.anlam.anlam']
    assert kw['env']['PYTHONPATH'].split(puanla.os.pathsep)[0] == str(puanla.ANLAM_BETIK.parents[2])
    puanla.anlam_calistir(Path('.'), 'py', ['q'], Path('ara.py'))
    assert gorulen[1][0][:2] == ['py', 'ara.py']


def test_anlam_alt_sureci_gercekten_ice_aktarir(tmp_path):
    """Alt süreç ImportError vermez: sağlayıcı yokken anlam modülünün kendi açık hatasına ulaşır."""
    data, hata = puanla.anlam_calistir(tmp_path, sys.executable, ['ara', 'soru', '--json'])
    assert data is None and 'gömme sağlayıcısı yok' in hata and 'ImportError' not in hata


def test_karne_vault_ortamdan_ve_varsayilan_cikti(tmp_path, monkeypatch):
    monkeypatch.delenv('HATIR_KASA', raising=False)
    with pytest.raises(SystemExit):
        karne.main([])
    (tmp_path / 'notes').mkdir()
    (tmp_path / 'notes' / 'A.md').write_text('---\ntitle: A\n---\ngövde\n', encoding='utf-8')
    monkeypatch.setenv('HATIR_KASA', str(tmp_path))
    monkeypatch.setattr(karne, 'beyin_calistir', lambda *a, **k: (None, 'yok'))
    assert karne.main([]) == 0
    assert list((tmp_path / '.hatir' / 'sonuclar').glob('karne-*.json'))


def test_tire_ile_baslayan_soru_reddedilir(tmp_path):
    path = tmp_path / 's.jsonl'
    path.write_text(json.dumps({'id': 'a', 'soru': '--yardim', 'tur': 'anlam', 'beklenen': ['notes/A.md']}) + '\n',
                    encoding='utf-8')
    with pytest.raises(ValueError, match="'-' ile"):
        puanla.sorulari_oku(path)
