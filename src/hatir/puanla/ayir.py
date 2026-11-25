"""Soru setini gerileme / yetenek olarak ayırır (Anthropic "Demystifying evals", 2026-10-24).

- gerileme: bugünkü sistemin güvenle geçtiği sorular; ≈%100 kalmalı, düşüş = gerileme.
- yetenek: henüz geçilemeyen sorular; gelişme alanı.
Kural: verilen TÜM sonuç dosyalarında ilk k'de bulunan soru gerilemeye geçer ("mezun olur").
Gerileme setindeki soru bir koşuda kaçırılsa bile otomatik yeteneğe düşmez (o bir gerileme
sinyalidir); `--yeniden` tüm seti baştan sınıflar. Olumsuz sorular (beklenen boş) hep gerilemedir.

    hatir-ayir --sorular sorular.jsonl --sonuc sonuclar/A.json [--sonuc sonuclar/B.json] [--k 5]
"""
import argparse
import json
import sys
from pathlib import Path

from ..ortak import json_oku

sys.dont_write_bytecode = True


def siralar(path):
    return {r['id']: {'sira': r.get('sira'), 'hata': r.get('hata'),
                       'yasak_ihlal': r.get('yasak_ihlal', [])} for r in json_oku(path)['sorular']}


def gecerli(sonuc, k):
    # Sayısal girdiler eski Python çağırıcılarıyla uyumluluk içindir.
    row = sonuc if isinstance(sonuc, dict) else {'sira': sonuc}
    return (row.get('sira') is not None and 1 <= row['sira'] <= k
            and not row.get('hata') and not row.get('yasak_ihlal'))


def ayir(sorular, sonuclar, k=5, yeniden=False):
    """sorular: dict listesi (yerinde güncellenir). (mezun id'ler, sayaç)."""
    mezun = []
    for q in sorular:
        eski = None if yeniden else q.get('set')
        if not q.get('beklenen'):
            q['set'] = 'gerileme'
            continue
        gecti = bool(sonuclar) and all(q['id'] in s and gecerli(s[q['id']], k)
                                       for s in sonuclar)
        if eski == 'gerileme':
            continue
        if gecti and not q.get('altin_supheli'):
            q['set'] = 'gerileme'
            if eski == 'yetenek':
                mezun.append(q['id'])
        else:
            q['set'] = 'yetenek'
    sayac = {s: sum(q.get('set') == s for q in sorular) for s in ('gerileme', 'yetenek')}
    return mezun, sayac


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sorular', type=Path, required=True)
    parser.add_argument('--sonuc', type=Path, action='append', required=True)
    parser.add_argument('--k', type=int, default=5)
    parser.add_argument('--yeniden', action='store_true')
    args = parser.parse_args(argv)
    ham = args.sorular.read_bytes()
    sorular = [json.loads(line) for line in ham.decode('utf-8-sig').splitlines() if line.strip()]
    mezun, sayac = ayir(sorular, [siralar(p) for p in args.sonuc], args.k, args.yeniden)
    # Dosyanın kendi satır sonu korunur (write_text Windows'ta her şeyi CRLF yapar).
    son = '\r\n' if b'\r\n' in ham else '\n'
    args.sorular.write_bytes(''.join(json.dumps(q, ensure_ascii=False) + son for q in sorular).encode('utf-8'))
    print(f"gerileme {sayac['gerileme']}, yetenek {sayac['yetenek']}"
          + (f"; gerilemeye geçen: {' '.join(mezun)}" if mezun else ''))
    return 0


if __name__ == '__main__':
    # reconfigure yalnız TextIOWrapper'dadır; StringIO ya da None üzerinde AttributeError olur.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
