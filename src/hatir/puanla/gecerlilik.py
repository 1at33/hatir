"""Supersedes döngüsü tespiti ve valid_to okuma: yalnız standart kütüphane, saf fonksiyonlar."""
import datetime
import re

# Katı tam-eşleşme dilbilgisi: tarih, isteğe bağlı T saat:dakika[:saniye[.kesir]] ve Z ya da +-SS:DD.
_SIMGE = re.compile(r'\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?)?')
# Cümle sonu noktası atıldığında geriye ':SS' ile biten saat kalırsa nokta kesir ayracıdır.
_SANIYE_SONU = re.compile(r'[Tt]\d{2}:\d{2}:\d{2}$')
_GECERLILIK_KALIBI = re.compile(r'(?<![\w])valid_to[ \t]*[:=][ \t]*(\d{4}-\d{2}-\d{2}(?:[Tt][^\s,;)]*|(?=\d)[^\s,;)]*)?)', re.IGNORECASE)


def donguleri_bul(kenarlar):
    """Düğüm -> supersede ettiği düğümler. Boyutu >= 2 olan her SCC (Tarjan, iteratif) bir döngüdür.

    Kendine kenar yok sayılır. Çıktı: sıralı düğüm listelerinin sıralı listesi.
    """
    graf = {}
    for dugum, hedefler in (kenarlar or {}).items():
        graf.setdefault(dugum, set())
        for hedef in hedefler or ():
            if hedef != dugum:
                graf[dugum].add(hedef)
                graf.setdefault(hedef, set())
    komsu = {d: sorted(h) for d, h in graf.items()}
    indeks, dusuk, yigin, yigindami = {}, {}, [], set()
    sayac = 0
    sonuc = []
    for kok in sorted(komsu):
        if kok in indeks:
            continue
        indeks[kok] = dusuk[kok] = sayac
        sayac += 1
        yigin.append(kok)
        yigindami.add(kok)
        is_yigini = [(kok, iter(komsu[kok]))]
        while is_yigini:
            dugum, it = is_yigini[-1]
            ilerledi = False
            for hedef in it:
                if hedef not in indeks:
                    indeks[hedef] = dusuk[hedef] = sayac
                    sayac += 1
                    yigin.append(hedef)
                    yigindami.add(hedef)
                    is_yigini.append((hedef, iter(komsu[hedef])))
                    ilerledi = True
                    break
                if hedef in yigindami:
                    dusuk[dugum] = min(dusuk[dugum], indeks[hedef])
            if ilerledi:
                continue
            is_yigini.pop()
            if is_yigini:
                ust = is_yigini[-1][0]
                dusuk[ust] = min(dusuk[ust], dusuk[dugum])
            if dusuk[dugum] == indeks[dugum]:
                bilesen = []
                while True:
                    x = yigin.pop()
                    yigindami.discard(x)
                    bilesen.append(x)
                    if x == dugum:
                        break
                if len(bilesen) >= 2:
                    sonuc.append(sorted(bilesen))
    return sorted(sonuc)


def _z_cevir(metin):
    """Sondaki z/Z yalnız hemen öncesi rakamsa UTC ofsetine çevrilir."""
    if metin[-1] in 'zZ' and metin[-2:-1] in tuple('0123456789'):
        return metin[:-1] + '+00:00'
    return metin


def _tarihe_cevir(deger):
    """Değeri datetime.date'e çevirir; olmazsa None."""
    if isinstance(deger, datetime.datetime):
        return deger.date()
    if isinstance(deger, datetime.date):
        return deger
    if not isinstance(deger, str):
        return None
    metin = deger.strip()
    if not _SIMGE.fullmatch(metin):
        return None
    try:
        if len(metin) == 10:
            return datetime.date.fromisoformat(metin)
        return datetime.datetime.fromisoformat(_z_cevir(metin)).date()
    except ValueError:
        return None


def _durum(tarih, kaynak, bugun):
    return {'valid_to': tarih.isoformat(), 'kaynak': kaynak,
            'durum': 'gecmis' if tarih < bugun else 'gecerli', 'ham': None}


def _gecersiz(kaynak, ham):
    return {'valid_to': None, 'kaynak': kaynak, 'durum': 'gecersiz',
            'ham': '' if ham is None else str(ham)}


def valid_to_oku(meta, bugun):
    """Öncelik: üst düzey valid_to > facts.valid_to > facts.gecerlilik metnindeki kalıp."""
    meta = meta if isinstance(meta, dict) else {}
    facts = meta.get('facts')
    facts = facts if isinstance(facts, dict) else {}
    for kaynak, kap in (('alan', meta), ('facts', facts)):
        if 'valid_to' in kap:
            tarih = _tarihe_cevir(kap['valid_to'])
            return _durum(tarih, kaynak, bugun) if tarih else _gecersiz(kaynak, kap['valid_to'])
    metin = facts.get('gecerlilik')
    if isinstance(metin, str):
        eslesme = _GECERLILIK_KALIBI.search(metin)
        if eslesme:
            simge = eslesme.group(1)
            if simge.endswith('.') and not _SANIYE_SONU.search(simge[:-1]):
                simge = simge[:-1]
            tarih = _tarihe_cevir(simge)
            return _durum(tarih, 'gecerlilik', bugun) if tarih else _gecersiz('gecerlilik', simge)
    return {'valid_to': None, 'kaynak': None, 'durum': 'yok', 'ham': None}
