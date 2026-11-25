"""Belirlenimli sentetik kasa: altın notlar, sorular ve dikkat dağıtıcı notlar (yalnız standart kütüphane).

Her not kendi tohumuyla (`<tohum>:<sıra>`) üretilir: 500 notluk kasa 10 000 notluk kasanın ilk 500 notudur,
böylece seviyeler iç içedir. İçerik tamamen sentetiktir; hiçbir gerçek kasadan metin okunmaz.

Not türleri:
  altin        : sorunun beklenen notu; iki nadir uydurma terim (t1, t2) taşır, kendi projesindedir.
  zor-ayni     : aynı proje ve klasörde, aynı başlık kalıbıyla tarihli takip kaydı; t1'i taşır, t2'yi
                 yalnız %10 olasılıkla (yakın kopya).
  zor-yayilmis : başka projede aynı konu ve başlık; t1'i, %25 olasılıkla t2'yi de taşır (kapsamsız aramayı
                 şaşırtır, kapsamlı arama elemelidir).
  proje        : bir projede, projenin genel sözcükleriyle başka alt konu.
  alakasiz     : proje dışı genel konular (`knowledge/genel/`), proje alanı yok.
"""
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

PROJELER = ('kuzey', 'liman', 'pusula', 'yaprak', 'kervan', 'fener')
KONULAR = ('yedekleme', 'sürüm etiketi', 'dağıtım hattı', 'önbellek süresi', 'kimlik doğrulama',
           'günlük kaydı', 'ölçüm paneli', 'arşivleme', 'veri göçü', 'zamanlayıcı', 'bildirim kuyruğu',
           'erişim listesi', 'test kapsamı', 'yük dengeleme', 'izleme alarmı', 'şema değişikliği')
PROJE_SOZCUK = ('sunucu', 'istemci', 'arayüz', 'kuyruk', 'rapor', 'ayar', 'modül', 'katman', 'bağlantı',
                'şablon', 'görev', 'süreç', 'kayıt', 'tablo', 'istek', 'yanıt', 'sınır', 'eşik')
GENEL = {
    'mutfak': ('mercimek çorbası', 'zeytinyağı', 'fırın ısısı', 'hamur mayası', 'baharat rafı'),
    'gezi': ('otobüs bileti', 'otel rezervasyonu', 'müze kartı', 'bavul listesi', 'uçuş saati'),
    'bahce': ('domates fidesi', 'sulama saati', 'kompost kutusu', 'budama mevsimi', 'saksı toprağı'),
    'kitap': ('roman önerisi', 'okuma alıntısı', 'kütüphane iadesi', 'çeviri karşılaştırması', 'dipnot'),
    'ev': ('kombi bakımı', 'musluk contası', 'boya rengi', 'perde ölçüsü', 'temizlik takvimi'),
    'muzik': ('gitar akoru', 'metronom ayarı', 'nota okuma', 'ritim egzersizi', 'ses ısınması'),
    'spor': ('antrenman planı', 'ısınma süresi', 'sezon takvimi', 'ekipman listesi', 'toparlanma günü'),
    'dil': ('kelime kartı', 'telaffuz notu', 'dinleme alıştırması', 'gramer tablosu', 'deyim listesi'),
}
HECE = ('ka', 'ri', 'mo', 'te', 'lu', 'sa', 've', 'no', 'zi', 'pa', 'de', 'ro', 'mi', 'ta', 'ku', 'le',
        'fi', 'ne', 'bo', 'gu', 'ya', 'ce', 'hu', 'di')
CUMLE = (
    '{a} için son durum gözden geçirildi, {b} tarafı sonraya bırakıldı.',
    'Bu hafta {a} ile {b} arasındaki fark yeniden not edildi.',
    '{a} konusunda yapılacaklar sıralandı; en acil olan {b} oldu.',
    '{b} yüzünden {a} planı bir gün kaydı.',
    '{a} için üç seçenek vardı; {b} en az zahmetli görünüyor.',
    '{a} tamamlandı, ardından {b} kontrol edilecek.',
)
KAYIT = (
    'Durum notu: {t} yeniden gözden geçirildi, sonuç değişmedi.',
    '{t} için kontrol tekrarlandı; küçük bir sapma görüldü.',
    'Takip: {t} henüz kapanmadı, ilişkisi netleştirilecek.',
    'Gözlem: {t} beklendiği gibi, ayrıca izlenecek.',
)
KARISIM_VARSAYILAN = {'zor-ayni': 0.2, 'zor-yayilmis': 0.2, 'proje': 0.3, 'alakasiz': 0.3}


def nadir_terim(rng):
    return ''.join(rng.choice(HECE) for _ in range(rng.randint(3, 4))) + rng.choice('nrls')


def tarih(rng):
    return f'2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}'


def cumleler(rng, sozcukler, adet):
    return ' '.join(rng.choice(CUMLE).format(a=rng.choice(sozcukler), b=rng.choice(sozcukler))
                    for _ in range(adet))


def altin_notlar(adet, tohum):
    """[{yol, proje, konu, t1, t2, baslik, govde, soru}] — her soru için bir beklenen not."""
    kullanilan, out = set(), []
    for i in range(adet):
        rng = random.Random(f'{tohum}:altin:{i}')
        proje = PROJELER[i % len(PROJELER)]
        konu = rng.choice(KONULAR)
        terimler = []
        while len(terimler) < 2:
            t = nadir_terim(rng)
            if t not in kullanilan:
                kullanilan.add(t)
                terimler.append(t)
        t1, t2 = terimler
        baslik = f'{konu.capitalize()} kararı {t1}'
        govde = (f'# {baslik}\n\n{konu.capitalize()} için {t1} yöntemi seçildi. Gerekçe: {t2} ile birlikte '
                 f'bakım yükü azalıyor ve geri dönüş kolay.\n\n' + cumleler(rng, PROJE_SOZCUK, 2))
        out.append({'yol': f'knowledge/{proje}/{baslik}.md', 'proje': proje, 'konu': konu, 't1': t1, 't2': t2,
                    'baslik': baslik, 'govde': govde, 'tur': 'altin',
                    'soru': f'{konu} kararında {t1} ve {t2} neden seçildi'})
    return out


def dikkat_dagitici(j, altinlar, tohum, karisim=None):
    """j. dikkat dağıtıcı not (yalnız kendi tohumuyla): {yol, proje, baslik, govde, tur}."""
    karisim = karisim or KARISIM_VARSAYILAN
    rng = random.Random(f'{tohum}:{j}')
    x, esik, tur = rng.random(), 0.0, 'alakasiz'
    for ad, oran in karisim.items():
        esik += oran
        if x < esik:
            tur = ad
            break
    g = altinlar[rng.randrange(len(altinlar))]
    if tur == 'zor-ayni':
        baslik = f'{g["konu"].capitalize()} kararı {g["t1"]} takip {tarih(rng)} {j}'
        terimler = [g['t1'], g['konu'], rng.choice(PROJE_SOZCUK)] + ([g['t2']] if rng.random() < 0.1 else [])
        satirlar = [rng.choice(KAYIT).format(t=t) for t in terimler]
        satirlar.append(f'{g["konu"].capitalize()} kararı için ek ölçüm bekleniyor.')
        return {'yol': f'knowledge/{g["proje"]}/{baslik}.md', 'proje': g['proje'], 'baslik': baslik, 'tur': tur,
                'govde': f'# {baslik}\n\n' + '\n'.join(f'- {s}' for s in satirlar)}
    if tur == 'zor-yayilmis':
        proje = rng.choice([p for p in PROJELER if p != g['proje']])
        baslik = f'{g["konu"].capitalize()} kararı {g["t1"]} {tarih(rng)} {j}'
        ikinci = f' ve {g["t2"]}' if rng.random() < 0.25 else ''
        govde = (f'# {baslik}\n\n{g["konu"].capitalize()} için {g["t1"]}{ikinci} bu projede de denendi; '
                 f'karar neden seçildiğine göre değişebilir.\n\n- ' + rng.choice(KAYIT).format(t=g['t1']))
        return {'yol': f'knowledge/{proje}/{baslik}.md', 'proje': proje, 'baslik': baslik, 'tur': tur,
                'govde': govde}
    if tur == 'proje':
        proje = rng.choice(PROJELER)
        a, b = rng.sample(PROJE_SOZCUK, 2)
        baslik = f'{a.capitalize()} ve {b} notu {j}'
        return {'yol': f'knowledge/{proje}/{baslik}.md', 'proje': proje, 'baslik': baslik, 'tur': tur,
                'govde': f'# {baslik}\n\n' + cumleler(rng, PROJE_SOZCUK, rng.randint(3, 5))}
    alan = rng.choice(sorted(GENEL))
    a, b = rng.sample(GENEL[alan], 2)
    baslik = f'{a.capitalize()} ve {b} {j}'
    return {'yol': f'knowledge/genel/{alan}/{baslik}.md', 'proje': None, 'baslik': baslik, 'tur': 'alakasiz',
            'govde': f'# {baslik}\n\n' + cumleler(rng, GENEL[alan], rng.randint(3, 5))}


def dosya_adi(yol_):
    klasor, _, ad = yol_.rpartition('/')
    ad = re.sub(r'[\\/:*?"<>|#^\[\]]+', ' ', ad[:-3]).strip(' .')[:90] or 'not'
    return f'{klasor}/{ad}.md'


def not_yaz(kasa, kayit):
    meta = {'title': kayit['baslik'], 'olcek_sentetik': kayit['tur']}
    if kayit['proje']:
        meta['project'] = kayit['proje']
    icerik = '---\n' + json.dumps(meta, ensure_ascii=False) + '\n---\n' + kayit['govde'].rstrip() + '\n'
    hedef = Path(kasa) / kayit['yol']
    hedef.parent.mkdir(parents=True, exist_ok=True)
    hedef.write_bytes(icerik.encode('utf-8'))


def uret(kasa, adet, soru=30, tohum='1', karisim=None):
    """`kasa` klasörüne `adet` not yazar (ilk `soru` tanesi altın). -> (notlar, sorular, rapor)."""
    if adet < 1 or soru < 1:
        raise ValueError('adet ve soru en az 1 olmalı')
    soru = min(soru, adet)
    altinlar = altin_notlar(soru, tohum)
    notlar = list(altinlar)
    for j in range(soru, adet):
        notlar.append(dikkat_dagitici(j, altinlar, tohum, karisim))
    gorulen = set()
    for kayit in notlar:
        kayit['yol'] = dosya_adi(kayit['yol'])
        anahtar, n = kayit['yol'].casefold(), 2
        while anahtar in gorulen:
            kayit['yol'] = kayit['yol'][:-3] + f' {n}.md'
            anahtar, n = kayit['yol'].casefold(), n + 1
        gorulen.add(anahtar)
        not_yaz(kasa, kayit)
    sorular = [{'id': f'o{i:03d}', 'soru': a['soru'], 'proje': a['proje'], 'beklenen': [a['yol']],
                'tur': 'kelime'} for i, a in enumerate(altinlar)]
    rapor = {'adet': adet, 'soru': soru, 'tohum': str(tohum),
             'tur_dagilimi': dict(sorted(Counter(n['tur'] for n in notlar).items())),
             'dizi_sha256': hashlib.sha256('\n'.join(n['yol'] for n in notlar).encode('utf-8')).hexdigest()[:16]}
    return notlar, sorular, rapor
