"""Ölçek testi: geçici klasörde sentetik kasa üretir, büyüdükçe sözcük aramasının isabetini ölçer.

Soru: kasa büyüyünce (yakın kopyalar, başka projede aynı konu) sözcük araması hâlâ doğru notu buluyor mu ve
proje kapsamını koruyor mu? Notlar diske yazılır, diskten okunup paket içi BM25 ile aranır. Seviyeler iç içedir
(500 notluk seviye 10 000'in ilk 500'üdür). Model, gömme ve ağ çağrısı yoktur; gerçek kasaya dokunmaz.

    hatir-olcek [--adet 500 | --buyuk] [--soru 30] [--tohum 1] [--seviyeler 100,250,500] [--json] [--cikti F]
"""
import argparse
import json
import math
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

from ..ortak import yol
from ..puanla.karne import on_bilgi
from ..puanla.puanla import sira_metrikleri
from .uret import uret

VARSAYILAN_ADET = 500
BUYUK_ADET = 10_000
DURAK = frozenset({'acaba', 'ama', 'ancak', 'bir', 'bu', 'bunu', 'çok', 'da', 'daha', 'de', 'diye', 'en', 'gibi',
                   'hangi', 'her', 'için', 'ile', 'ise', 'ki', 'mi', 'mı', 'mu', 'mü', 'ne', 'neden', 'nasıl',
                   'o', 'olan', 've', 'veya', 'ya', 'neydi', 'nedir', 'var', 'yok'})


def katla(metin):
    """Türkçe küçük harf: I→ı, İ→i."""
    return unicodedata.normalize('NFC', metin).replace('I', 'ı').replace('İ', 'i').lower()


def simgeler(metin, kok=6):
    """Sözcüklere böl, durak sözcükleri at, ilk `kok` harfe kes (kaba Türkçe ek budama)."""
    out = []
    for word in re.findall(r'\w+', katla(metin)):
        if word in DURAK or len(word) < 2:
            continue
        out.append(word[:kok] if kok and len(word) > kok else word)
    return out


class BM25:
    """Ters dizinli BM25; proje süzgeci anlam aramasıyla aynı: not projesi == P ya da projesiz."""

    def __init__(self, belgeler, k1=1.2, b=0.75):
        """belgeler: [(kaynak, metin, proje)]."""
        self.k1, self.b = k1, b
        self.kaynak, self.proje, self.uzunluk = [], [], []
        self.ilan = {}
        for i, (source, text, project) in enumerate(belgeler):
            tf = {}
            tokens = simgeler(text)
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            for token, f in tf.items():
                self.ilan.setdefault(token, []).append((i, f))
            self.kaynak.append(source)
            self.proje.append(project)
            self.uzunluk.append(len(tokens))
        n = len(self.kaynak)
        self.ort = sum(self.uzunluk) / n if n else 0.0
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.ilan.items()}

    def ara(self, sorgu, project=None, k=10):
        scores = {}
        for term in set(simgeler(sorgu)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, f in self.ilan[term]:
                if project and self.proje[i] not in (project, None):
                    continue
                norm = f + self.k1 * (1 - self.b + self.b * self.uzunluk[i] / (self.ort or 1))
                scores[i] = scores.get(i, 0.0) + idf * f * (self.k1 + 1) / norm
        order = sorted(scores, key=lambda i: (-scores[i], self.kaynak[i]))[:k]
        return [self.kaynak[i] for i in order]


def kasa_hazirla(klasor):
    """Hedef klasör yoksa oluşturulur; varsa boş olmalı (gerçek kasaya yazmamak için)."""
    klasor = Path(klasor)
    if klasor.exists():
        if not klasor.is_dir():
            raise ValueError(f'{klasor} klasör değil')
        if any(klasor.iterdir()):
            raise ValueError(f'{klasor} boş değil; ölçek testi yalnız boş ya da yeni klasöre yazar')
    klasor.mkdir(parents=True, exist_ok=True)
    return klasor


def oku(kasa, notlar):
    """Diske yazılan notları geri oku -> [(kaynak, metin, proje)] (üretim sırasıyla)."""
    out = []
    for kayit in notlar:
        text = (Path(kasa) / kayit['yol']).read_text(encoding='utf-8')
        metadata, body = on_bilgi(text)
        project = metadata.get('project') if isinstance(metadata.get('project'), str) else None
        title = metadata.get('title') if isinstance(metadata.get('title'), str) else ''
        out.append((kayit['yol'], f'{title}\n{body}', project))
    return out


def seviyeleri_coz(adet, soru, istenen=None):
    if istenen:
        sev = sorted({s for s in istenen if soru <= s <= adet} | {adet})
    else:
        sev = sorted({s for s in (adet // 10, adet // 4, adet // 2, adet) if s >= soru} | {adet})
    return sev


def seviye_olc(belgeler, sorular, k=5):
    t0 = time.perf_counter()
    motor = BM25(belgeler)
    dizin_sn = time.perf_counter() - t0
    projeler = dict(zip((yol(s) for s, _, _ in belgeler), (p for _, _, p in belgeler)))
    out = {'not': len(belgeler), 'dizin_ms': round(1000 * dizin_sn, 1)}
    for mod, kapsamli in (('kapsamli', True), ('kapsamsiz', False)):
        satirlar, ihlal, sure = [], 0, 0.0
        for q in sorular:
            proje = q['proje'] if kapsamli else None
            t = time.perf_counter()
            sonuc = motor.ara(q['soru'], proje, k=max(k, 10))
            sure += time.perf_counter() - t
            expected = {yol(b) for b in q['beklenen']}
            rank = next((i for i, s in enumerate(sonuc, 1) if yol(s) in expected), None)
            satirlar.append(sira_metrikleri(rank, k))
            if kapsamli:
                ihlal += sum(projeler.get(yol(s)) not in (q['proje'], None) for s in sonuc[:k])
        n = len(satirlar)
        ozet = {key: round(sum(r[key] for r in satirlar) / n, 4) for key in ('hit@1', f'hit@{k}', 'MRR')}
        ozet['sorgu_ms'] = round(1000 * sure / n, 2)
        if kapsamli:
            ozet['kapsam_ihlal'] = ihlal
        out[mod] = ozet
    return out


def olc(adet=VARSAYILAN_ADET, soru=30, tohum='1', seviyeler=None, k=5, klasor=None):
    """Sentetik kasayı üret, her seviyede ölç. klasor None ise geçici klasör kullanılır ve sonra silinir."""
    if klasor is None:
        with tempfile.TemporaryDirectory(prefix='hatir-olcek-') as gecici:
            return olc(adet, soru, tohum, seviyeler, k, Path(gecici) / 'kasa')
    kasa = kasa_hazirla(klasor)
    t0 = time.perf_counter()
    notlar, sorular, rapor = uret(kasa, adet, soru, tohum)
    rapor['uretim_sn'] = round(time.perf_counter() - t0, 2)
    rapor['md_dosya'] = sum(1 for _ in kasa.rglob('*.md'))
    belgeler = oku(kasa, notlar)
    rapor['k'] = k
    rapor['seviyeler'] = [seviye_olc(belgeler[:s], sorular, k) for s in seviyeleri_coz(adet, rapor['soru'], seviyeler)]
    return rapor


def tablo(rapor):
    k = rapor['k']
    baslik = (f"{'Not':>7} {'kapsamlı hit@1':>15} {f'hit@{k}':>7} {'MRR':>7} {'ihlal':>6} "
              f"{'kapsamsız hit@1':>16} {f'hit@{k}':>7} {'MRR':>7} {'sorgu ms':>9}")
    satirlar = [baslik]
    for s in rapor['seviyeler']:
        a, b = s['kapsamli'], s['kapsamsiz']
        satirlar.append(f"{s['not']:>7} {a['hit@1']:>15.3f} {a[f'hit@{k}']:>7.3f} {a['MRR']:>7.3f} "
                        f"{a['kapsam_ihlal']:>6} {b['hit@1']:>16.3f} {b[f'hit@{k}']:>7.3f} {b['MRR']:>7.3f} "
                        f"{a['sorgu_ms']:>9.2f}")
    satirlar.append(f"{rapor['adet']} not, {rapor['soru']} soru, tohum {rapor['tohum']}, "
                    f"dağılım {rapor['tur_dagilimi']}, dizi {rapor['dizi_sha256']}")
    return '\n'.join(satirlar)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--adet', type=int, default=None, help=f'toplam not (varsayılan {VARSAYILAN_ADET})')
    parser.add_argument('--buyuk', action='store_true', help=f'{BUYUK_ADET} not (yavaş; --adet ile birlikte verilmez)')
    parser.add_argument('--soru', type=int, default=30, help='altın not / soru sayısı')
    parser.add_argument('--tohum', default='1')
    parser.add_argument('--k', type=int, default=5)
    parser.add_argument('--seviyeler', default=None, help='virgüllü not sayıları (varsayılan adet/10, /4, /2, adet)')
    parser.add_argument('--klasor', type=Path, default=None,
                        help='sentetik kasayı bu boş/yeni klasörde bırak (varsayılan: geçici klasör, sonra silinir)')
    parser.add_argument('--cikti', type=Path, default=None, help='JSON raporu bu dosyaya yaz')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.buyuk and args.adet is not None:
        parser.error('--buyuk ile --adet birlikte verilmez')
    adet = BUYUK_ADET if args.buyuk else (args.adet or VARSAYILAN_ADET)
    if adet < 1 or args.soru < 1 or args.k < 1:
        parser.error('--adet, --soru ve --k en az 1 olmalı')
    try:
        seviyeler = [int(x) for x in args.seviyeler.split(',') if x.strip()] if args.seviyeler else None
    except ValueError:
        parser.error('--seviyeler virgüllü tam sayılar olmalı')
    try:
        rapor = olc(adet, args.soru, args.tohum, seviyeler, args.k, args.klasor)
    except ValueError as exc:
        print(f'Hata: {exc}', file=sys.stderr)
        return 1
    if args.cikti:
        args.cikti.parent.mkdir(parents=True, exist_ok=True)
        args.cikti.write_text(json.dumps(rapor, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(rapor, ensure_ascii=False) if args.json else tablo(rapor))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
