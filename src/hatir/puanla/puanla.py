"""Sabit soru setiyle not kasası geri getirme ölçümü. Komut: `hatir-puanla`."""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .. import anlam as _anlam_paketi
from ..ortak import beyin_calistir, json_oku, json_yaz, kaynaklar, puan_dosyalari, yol

sys.dont_write_bytecode = True

# Anlam araması paket içinden çözülür (`python -m hatir.anlam.anlam`); kasada ayrıca betik aranmaz.
ANLAM_BETIK = Path(_anlam_paketi.__file__).resolve().parent / 'anlam.py'
ANLAM_MODULU = 'hatir.anlam.anlam'
# Alt süreç paketi kurulu olmasa da bulsun: hatir paketinin üst klasörü PYTHONPATH'e eklenir.
_PAKET_KOKU = str(Path(_anlam_paketi.__file__).resolve().parents[2])


def soru_metrikleri(beklenen, sources, k=5):
    if k < 1:
        raise ValueError('k en az 1 olmalı')
    unique = list(dict.fromkeys(yol(s) for s in sources))
    expected = {yol(s) for s in beklenen}
    rank = next((i for i, source in enumerate(unique, 1) if source in expected), None)
    return sira_metrikleri(rank, k)


def sira_metrikleri(rank, k=5):
    return {'sira': rank, 'hit@1': int(rank is not None and rank <= 1),
            'hit@3': int(rank is not None and rank <= 3),
            f'hit@{k}': int(rank is not None and rank <= k),
            'MRR': 1 / rank if rank else 0.0}


def yasak_ihlali(yasak, sources, k=5):
    """İlk k kaynakta görünen yasak (eskimiş/private) yollar."""
    top = {yol(s) for s in list(dict.fromkeys(sources))[:k]}
    return [y for y in yasak if yol(y) in top]


def neden_sinifi(row, k=5):
    """Başarısızlık nedeni (Palantir AIP Evals'taki kök neden sınıflaması); başarılıysa None.

    siralama: doğru not sıralayıcının aday kümesindeydi ama ilk k'ye çıkmadı.
    aday_yok: doğru not aday kümesinde (ya da hiç) yok — arama aşaması kaçırdı.
    sira_dusuk: aday kümesi bilinmiyor (beyin/anlam), not listede ama k'nin altında.
    """
    if row.get('hata'):
        return 'hata'
    if row.get('yasak_ihlal'):
        return 'yasak_ihlal'
    if not row.get('beklenen') or row['metrikler'][f'hit@{k}']:
        return None
    if row.get('aday_kapsama') is not None:
        return 'siralama' if row['aday_kapsama'] else 'aday_yok'
    return 'aday_yok' if row['sira'] is None else 'sira_dusuk'


def tekrar_ozeti(kosular, k=5):
    """Aynı setin N koşusu: genel hit@k / MRR ortalaması, aralığı ve sonucu koşudan koşuya değişen sorular."""
    ozetler = [ozetle(rows, k)['genel'] for rows in kosular]
    out = {'n': len(kosular)}
    for key in (f'hit@{k}', 'hit@1', 'MRR'):
        values = [o[key] for o in ozetler]
        out[key] = {'ortalama': sum(values) / len(values), 'min': min(values), 'max': max(values)}
    by_id = {}
    for rows in kosular:
        for r in rows:
            if r.get('beklenen') and not r.get('hata'):
                by_id.setdefault(r['id'], set()).add(r['metrikler'][f'hit@{k}'])
    out['kararsiz'] = sorted(i for i, v in by_id.items() if len(v) > 1)
    for key, select in (('olumsuz_ihlal', lambda r: bool(r.get('yasak_ihlal'))),
                        ('hatali', lambda r: bool(r.get('hata')))):
        values = [sum(select(r) for r in rows) for rows in kosular]
        out[key] = {'ortalama': sum(values) / len(values), 'min': min(values), 'max': max(values)}
        durumlar = {}
        for rows in kosular:
            for r in rows:
                durumlar.setdefault(r['id'], set()).add(select(r))
        out[key + '_kararsiz'] = sorted(i for i, v in durumlar.items() if len(v) > 1)
    return out


def kosulari_birlestir(kosular, k=5):
    # İsabet ölçümü ilk koşuyu korur; güvenlik ve hata için tek kötü koşu yeterlidir.
    rows = []
    for ilk in kosular[0]:
        ayni = [r for kosu in kosular for r in kosu if r['id'] == ilk['id']]
        row = dict(ilk)
        row['yasak_ihlal'] = list(dict.fromkeys(p for r in ayni for p in r.get('yasak_ihlal', [])))
        hatalar = list(dict.fromkeys(r['hata'] for r in ayni if r.get('hata')))
        row['hata'] = '; '.join(hatalar) or None
        row['neden'] = neden_sinifi(row, k)
        rows.append(row)
    return rows


def ozetle(rows, k=5):
    def average(items):
        # Yalnız olumsuz (beklenen boş, yasak dolu) soru isabet ortalamasına ve adede girmez.
        items = [r for r in items if r.get('beklenen', [None])]
        valid = [r for r in items if not r.get('hata')]
        keys = list(dict.fromkeys(['hit@1', 'hit@3', f'hit@{k}', 'MRR']))
        return {'adet': len(items), 'hatali': len(items) - len(valid),
                **{key: sum(r['metrikler'][key] for r in valid) / len(valid)
                   if valid else 0.0 for key in keys}}
    return {group: average(rows if group == 'genel' else [r for r in rows if r['tur'] == group])
            for group in ('genel', 'kelime', 'anlam')}


def karsilastirma(current, previous):
    """Ortak, iki koşuda da hatasız soruları aynı k ile yeniden ölç."""
    new = {r['id']: r for r in current['sorular']}
    old = {r['id']: r for r in previous['sorular']}
    k = current.get('k', 5)
    warnings = []
    if new.keys() != old.keys():
        warnings.append('Soru id kümeleri farklı; yalnız ortak sorular karşılaştırılıyor.')
    if k != previous.get('k', 5):
        warnings.append(f'k farklı; ortak sorular her iki koşuda k={k} ile yeniden hesaplandı.')
    common = new.keys() & old.keys()
    valid = sorted(key for key in common if not new[key].get('hata') and not old[key].get('hata'))
    if len(valid) != len(common):
        warnings.append('İki koşudan birinde hatalı olan ortak sorular karşılaştırmadan çıkarıldı.')
    if not valid:
        warnings.append('Karşılaştırılabilir ortak soru yok.')

    def summary(rows):
        return ozetle([{**rows[key], 'tur': new[key]['tur'],
                        'metrikler': sira_metrikleri(rows[key].get('sira'), k)} for key in valid], k)
    return summary(new), summary(old), warnings


def sorulari_oku(path):
    rows, ids = [], set()
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('nesne bekleniyor')  # noqa: TRY004  # çağıran ValueError yakalar
            if not isinstance(row.get('id'), str) or not row['id'] or row['id'] in ids:
                raise ValueError('id boş ya da tekrarlı')
            if not isinstance(row.get('soru'), str) or not row['soru'].strip():
                raise ValueError('soru gerekli')
            if row['soru'].lstrip().startswith('-'):
                # Soru alt betiğe argüman olarak gider; '-' ile başlarsa seçenek sanılır.
                raise ValueError("soru '-' ile başlayamaz")
            if row.get('tur') not in ('kelime', 'anlam'):
                raise ValueError('tur kelime/anlam olmalı')
            if row.get('proje') is not None and not isinstance(row['proje'], str):
                raise ValueError('proje metin ya da null olmalı')
            yasak = row.get('yasak', [])
            if not isinstance(yasak, list):
                raise ValueError('yasak liste olmalı')  # noqa: TRY004  # çağıran ValueError yakalar
            if not isinstance(row.get('beklenen'), list) or (not row['beklenen'] and not yasak):
                raise ValueError('beklenen boş olamaz (yalnız olumsuz soruda, yasak doluysa boş olabilir)')
            for source in row['beklenen'] + yasak:
                if not isinstance(source, str) or not source or '\\' in source or source.startswith('/') or ':' in source or '..' in source.split('/'):
                    raise ValueError('beklenen/yasak vault içindeki ileri eğik çizgili yol olmalı')
            if row.get('set') not in (None, 'gerileme', 'yetenek'):
                raise ValueError('set gerileme/yetenek ya da yok olmalı')
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{path}:{number}: {exc}') from exc
        ids.add(row['id'])
        rows.append(row)
    if not rows:
        raise ValueError('Soru seti boş')
    return rows


def context_argumanlari(question):
    """Kasa betiğinin `context` alt komutu (avenoxbeyin sözleşmesi); indeksi yazmaz."""
    command = ['context', question['soru']]
    if question.get('proje'):
        command += ['--project', question['proje']]
    return command + ['--no-sync', '--json']


def anlam_argumanlari(question, k, karma=False, rerank=False):
    """hatir.anlam.anlam ara alt komutunun argümanları (modül adı hariç)."""
    command = ['ara', question['soru']]
    if question.get('proje'):
        command += ['--project', question['proje']]
    command += ['--k', str(max(k, 10)), '--json']
    return command + (['--karma'] if karma else []) + (['--rerank'] if rerank else [])


def rerank_argumanlari(question, k):
    """Kullanıcının yeniden sıralama betiğinin argümanları (betik yolu hariç); indeksi yazmaz."""
    command = [question['soru']]
    if question.get('proje'):
        command += ['--project', question['proje']]
    return command + ['--k', str(max(k, 10)), '--json', '--no-sync']


def anlam_calistir(vault, python, args, script=None):
    """beyin_calistir ile aynı sözleşme: (JSON nesnesi | None, hata | None).

    script verilmezse paket içi anlam modülü `-m` ile çalışır (göreli içe aktarmalar betik olarak çalışmaz)."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    if script is None:
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [_PAKET_KOKU, os.environ.get('PYTHONPATH')]))
        hedef = ['-m', ANLAM_MODULU]
    else:
        hedef = [str(script)]
    try:
        result = subprocess.run([python, *hedef, *args, '--vault', str(vault)],
                                cwd=vault, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=180, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    error = None
    if result.returncode:
        error = f'Çıkış {result.returncode}: {result.stderr.strip() or result.stdout.strip()}'
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError('JSON nesnesi bekleniyor')  # noqa: TRY004  # çağıran ValueError yakalar
        return data, error
    except ValueError as exc:
        return None, error or f'Geçersiz JSON: {exc}; {result.stderr.strip()}'


def _kayitli_siralayici():
    """Sonuç dosyasına yazılan yeniden sıralama sağlayıcısının adı ve modeli; yoksa None."""
    from hatir.ayar import yeniden_siralama_coz
    try:
        ayar = yeniden_siralama_coz()
    except ValueError:
        return None
    if ayar is None or ayar.kapali:
        return None
    return f'{ayar.saglayici}/{ayar.model}'


def _kayitli_saglayici():
    """Sonuç dosyasına yazılan gömme adı. Ortam değişkeni, yoksa [gomme]; ikisi de yoksa None."""
    ad = os.environ.get('HATIR_SAGLAYICI', '').strip()
    if ad:
        return ad
    from hatir.ayar import gomme_adi
    try:
        return gomme_adi()
    except ValueError:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, default=os.environ.get('HATIR_KASA') or None,
                        help='not kasası kökü (varsayılan: HATIR_KASA ortam değişkeni)')
    parser.add_argument('--sorular', type=Path, required=True,
                        help='JSONL soru seti (biçim: veri/sorular.ornek.jsonl)')
    parser.add_argument('--sonuclar', type=Path, default=None,
                        help='sonuç klasörü (varsayılan: <kasa>/.hatir/sonuclar)')
    parser.add_argument('--etiket', default='')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--k', type=int, default=5)
    parser.add_argument('--karsilastir', action='store_true')
    parser.add_argument('--sync', action='store_true', help='Ölçümden önce bir kez sync çalıştırır (vault/runtime yazabilir)')
    parser.add_argument('--yontem', choices=('beyin', 'anlam', 'karma', 'rerank'), default=None,
                        help='Arama yöntemi (varsayılan beyin: kasa betiğinin context komutu); anlam/karma/rerank '
                             'paket içi hatir.anlam (rerank: `ara --rerank`, [yeniden_siralama] ayarı); '
                             '--rerank-betigi verilirse rerank o betiği kullanır')
    parser.add_argument('--rerank-betigi', type=Path, default=os.environ.get('HATIR_RERANK_BETIGI') or None,
                        help='isteğe bağlı: yerleşik yeniden sıralama yerine betik (varsayılan: HATIR_RERANK_BETIGI); '
                             '`<soru> [--project P] --k N --json --no-sync --vault <kasa>` alıp records JSON basmalı')
    parser.add_argument('--set', choices=('gerileme', 'yetenek'), default=None,
                        help='Yalnız bu setin soruları (gerileme ≈%%100 kalmalı; yetenek gelişme alanı)')
    parser.add_argument('--tekrar', type=int, default=1,
                        help='Seti N kez koş; ortalama/aralık ve kararsız sorular (rerank\'ta önbelleksiz, ücretli)')
    args = parser.parse_args(argv)
    if args.vault is None:
        parser.error('--vault verin ya da HATIR_KASA ortam değişkenini ayarlayın')
    if args.sonuclar is None:
        args.sonuclar = args.vault / '.hatir' / 'sonuclar'
    if args.tekrar < 1:
        parser.error('--tekrar en az 1 olmalı')
    yontem = args.yontem or 'beyin'
    if args.k < 1:
        parser.error('--k en az 1 olmalı')
    try:
        questions = sorulari_oku(args.sorular)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.set:
        questions = [q for q in questions if q.get('set') == args.set]
        if not questions:
            parser.error(f'{args.sorular} içinde set={args.set} soru yok')
    if args.sync:
        _, error = beyin_calistir(args.vault.resolve(), args.python, ['sync'], json_bekle=False)
        if error:
            parser.exit(1, f'Ölçüm başlatılmadı; sync başarısız: {error}\n')
    previous = puan_dosyalari(args.sonuclar)
    old = json_oku(previous[-1]) if args.karsilastir and previous else None
    def koş():
        rows = []
        for question in questions:
            if yontem == 'beyin':
                data, error = beyin_calistir(args.vault.resolve(), args.python, context_argumanlari(question))
            elif yontem == 'rerank':
                if args.rerank_betigi is not None:
                    data, error = anlam_calistir(args.vault.resolve(), args.python,
                                                 rerank_argumanlari(question, args.k), args.rerank_betigi.resolve())
                else:
                    data, error = anlam_calistir(args.vault.resolve(), args.python,
                                                 anlam_argumanlari(question, args.k, rerank=True))
                if data and data.get('degraded'):
                    error = error or 'degraded: ' + '; '.join(data.get('uyari') or [])
            else:
                data, error = anlam_calistir(args.vault.resolve(), args.python,
                                             anlam_argumanlari(question, args.k, yontem == 'karma'))
            records = data.get('records') if data is not None else None
            if not isinstance(records, list):
                error = error or 'JSON records listesi yok'
                records = []
            sources = kaynaklar(records)
            metrics = soru_metrikleri(question['beklenen'], sources, args.k)
            adaylar = data.get('adaylar') if isinstance(data, dict) else None
            kapsama = (any(yol(b) in {yol(a) for a in adaylar} for b in question['beklenen'])
                       if isinstance(adaylar, list) and question['beklenen'] else None)
            row = {**question, 'sira': metrics['sira'], 'ilk_5': sources[:5], 'ilk_k': sources[:args.k],
                   'hata': error, 'metrikler': metrics, 'aday_kapsama': kapsama,
                   'yasak_ihlal': yasak_ihlali(question.get('yasak', []), sources, args.k)}
            row['neden'] = neden_sinifi(row, args.k)
            rows.append(row)
        return rows

    if args.tekrar > 1 and yontem == 'rerank':
        # Varyans ölçümü önbellekten gelmesin: her tekrarda modele gidilir (ücretli olabilir).
        os.environ['HATIR_RERANK_ONBELLEK'] = '0'
    kosular = [koş() for _ in range(args.tekrar)]
    rows = kosulari_birlestir(kosular, args.k)
    summary = ozetle(rows, args.k)
    olumsuz = [r for r in rows if r.get('yasak')]
    summary['hatali'] = sum(bool(r.get('hata')) for r in rows)
    summary['olumsuz_hatali'] = sum(bool(r.get('hata')) for r in olumsuz)
    summary['olumsuz'] = {'adet': len(olumsuz), 'ihlal': sum(bool(r['yasak_ihlal']) for r in olumsuz)}
    summary['nedenler'] = dict(Counter(r['neden'] for r in rows if r['neden']))
    pozitif = [r for r in rows if r.get('beklenen') and not r.get('hata') and r.get('aday_kapsama') is not None]
    if pozitif:
        summary['asama'] = {'aday_kapsama': sum(r['aday_kapsama'] for r in pozitif) / len(pozitif),
                            f'hit@{args.k}': sum(r['metrikler'][f'hit@{args.k}'] for r in pozitif) / len(pozitif),
                            'adet': len(pozitif)}
    if args.tekrar > 1:
        summary['tekrar'] = tekrar_ozeti(kosular, args.k)
    try:
        version = (args.vault / '.beyin-version').read_text(encoding='utf-8').strip()
    except OSError:
        version = '?'
    now = datetime.now().astimezone()
    result = {'beyin_surumu': version, 'tarih': now.isoformat(), 'etiket': args.etiket,
              'k': args.k, 'sync': args.sync, 'yontem': yontem,
              'saglayici': (_kayitli_saglayici() if yontem in ('anlam', 'karma')
                            or (yontem == 'rerank' and args.rerank_betigi is None) else None),
              'yeniden_siralayici': (_kayitli_siralayici() if yontem == 'rerank' and args.rerank_betigi is None
                                     else None),
              'ozet': summary, 'sorular': rows, 'kosular': kosular}
    args.sonuclar.mkdir(parents=True, exist_ok=True)
    label = re.sub(r'[^\w.-]+', '-', args.etiket, flags=re.UNICODE).strip('.-')
    filename = now.strftime('%Y-%m-%d-%H%M') + ('-' + label if label else '')
    target = args.sonuclar / (filename + '.json')
    # Aynı dakikadaki önceki ölçümü kaybetme.
    suffix = 2
    while target.exists():
        target = args.sonuclar / f'{filename}-{suffix}.json'
        suffix += 1
    json_yaz(target, result)
    print(f"{'Grup':<10} {'Soru':>5} {'Hatalı':>6} {'hit@1':>8} {'hit@3':>8} {('hit@' + str(args.k)):>8} {'MRR':>8}")
    for group in ('genel', 'kelime', 'anlam'):
        metrics = summary[group]
        print(f"{group:<10} {metrics['adet']:>5} {metrics['hatali']:>6} {metrics['hit@1']:>8.3f} {metrics['hit@3']:>8.3f} {metrics[f'hit@{args.k}']:>8.3f} {metrics['MRR']:>8.3f}")
    if summary['genel']['hatali']:
        print(f"Uyarı: {summary['genel']['hatali']} soru hatalı, ölçüm eksik")
    if olumsuz:
        print(f"Olumsuz denetim: {len(olumsuz)} soru, ilk {args.k}'te yasak (eskimiş/private) not: {summary['olumsuz']['ihlal']}; hatalı: {summary['olumsuz_hatali']}")
        for row in olumsuz:
            if row.get('hata'):
                print(f"- HATA {row['id']}: {row['hata']}")
            if row['yasak_ihlal']:
                print(f"- İHLAL {row['id']}: {', '.join(row['yasak_ihlal'])}")
    if summary['nedenler']:
        print('Başarısızlık nedenleri: ' + ', '.join(f'{n} {c}' for n, c in sorted(summary['nedenler'].items())))
    if 'asama' in summary:
        a = summary['asama']
        print(f"Aşama: aday kümesi kapsaması {a['aday_kapsama']:.3f} (tavan), son sıra hit@{args.k} "
              f"{a[f'hit@{args.k}']:.3f} ({a['adet']} soru)")
    if 'tekrar' in summary:
        t = summary['tekrar']
        h = t[f'hit@{args.k}']
        print(f"Tekrar ({t['n']} koşu): hit@{args.k} ortalama {h['ortalama']:.3f}, aralık {h['min']:.3f}–{h['max']:.3f}; "
              f"MRR ortalama {t['MRR']['ortalama']:.3f}; kararsız soru {len(t['kararsiz'])}"
              + (f": {' '.join(t['kararsiz'])}" if t['kararsiz'] else ''))
        for key in ('olumsuz_ihlal', 'hatali'):
            v = t[key]
            print(f"Tekrar {key}: ortalama {v['ortalama']:.3f}, aralık {v['min']}–{v['max']}; "
                  f"kararsız: {' '.join(t[key + '_kararsiz']) or '-'}")
    geri = [r for r in rows if r.get('set') == 'gerileme' and r.get('beklenen')
            and not r.get('hata') and not r['metrikler'][f'hit@{args.k}']]
    if any(r.get('set') == 'gerileme' for r in rows):
        print(f"Gerileme seti: {len(geri)} soru ilk {args.k}'ten düştü" + (':' if geri else ' (beklenen 0).'))
        for row in geri:
            print(f"- GERİLEDİ {row['id']}: {row['soru']}")
    print('Bulunamayan sorular (tüm sıralama):')
    missing = [r for r in rows if r['sira'] is None and r.get('beklenen')]
    for row in missing:
        print(f"- {row['id']}: {row['soru']}" + (f" | {row['hata']}" if row['hata'] else ''))
    if not missing:
        print('- Yok')
    if args.karsilastir:
        print(f'Karşılaştırma: {previous[-1]}' if old else 'Karşılaştırma: önceki puan sonucu yok')
        if old:
            new_summary, old_summary, warnings = karsilastirma(result, old)
            for warning in warnings:
                print(f'Uyarı: {warning}')
            for group, metrics in new_summary.items():
                differences = [f"{key} {value - old_summary[group][key]:+.3f}"
                               for key, value in metrics.items() if key not in ('adet', 'hatali')]
                print(f"{group}: {', '.join(differences)}")
    print(f'Sonuç: {target}')
    return int(bool(summary['olumsuz_hatali']) or summary['genel']['hatali'] == len(rows))


if __name__ == '__main__':
    raise SystemExit(main())
