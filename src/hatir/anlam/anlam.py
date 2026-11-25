"""Not kasası için embedding tabanlı anlam araması (notlara yazmaz). `ara --rerank`: hatir.anlam.rerank."""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from ..ayar import GOMME_ANAHTAR_ENV, url_dogrula
from ..ortak import beyin_calistir, kaynaklar, yol
from ..puanla.karne import beyin_islevleri, on_bilgi, yerine_gecenler

sys.dont_write_bytecode = True

# Ön bilgi ayrıştırma, emekli/eskimiş not mantığı ve beyin çağrısı ortak ve puanla.karne modüllerinden gelir.

# Sağlayıcı kodda varsayılmaz. HATIR_SAGLAYICI > ayar.toml [gomme]. İkisi de yoksa boş.
# gemini / nebius (taban_url'süz) kendi istemcilerini kullanır; başka ad + taban_url OpenAI uyumludur.
SAGLAYICI = ''
MODEL = ''
SORGU_TALIMAT = ''
_TABAN_URL = ''
_ANAHTAR_ENV = ''
_NEBIUS_TALIMAT = 'Instruct: Given a question in Turkish, retrieve the personal note that answers it\nQuery: '
_gemini = None


def saglayici_hazirla(zorunlu=False):
    """Globalleri ortamdan doldur. zorunlu ve sağlayıcı yoksa ValueError."""
    global SAGLAYICI, MODEL, SORGU_TALIMAT, _TABAN_URL, _ANAHTAR_ENV, _gemini
    from hatir.ayar import gomme_coz
    env_ad = os.environ.get('HATIR_SAGLAYICI', '').strip()
    model, taban, anahtar = '', '', ''
    if env_ad:
        model = os.environ.get('HATIR_MODEL', '').strip()
        taban = os.environ.get('HATIR_URL', '').strip()
        anahtar = os.environ.get('HATIR_ANAHTAR_ENV', '').strip()
    else:
        try:
            gomme = gomme_coz()
        except ValueError:
            gomme = None
        if gomme is not None and gomme.saglayici and gomme.saglayici != 'yok':
            env_ad, model, taban, anahtar = gomme.saglayici, gomme.model, gomme.taban_url, gomme.anahtar_env
    if not env_ad:
        SAGLAYICI, MODEL, SORGU_TALIMAT = '', '', ''
        _TABAN_URL, _ANAHTAR_ENV = '', ''
        if zorunlu:
            raise ValueError('gömme sağlayıcısı yok; ayar.toml [gomme] ya da HATIR_SAGLAYICI')
        return
    ad = env_ad.casefold()
    if ad == 'gemini' and not taban:
        from . import gemini as gem
        _gemini = gem
        SAGLAYICI, MODEL, SORGU_TALIMAT = 'gemini', model or gem.MODEL, gem.SORGU_ONEKI
        _TABAN_URL, _ANAHTAR_ENV = '', anahtar or GOMME_ANAHTAR_ENV[ad]
        return
    if ad == 'nebius' and not taban:
        from . import nebius
        SAGLAYICI, MODEL, SORGU_TALIMAT = 'nebius', model or nebius.MODEL, _NEBIUS_TALIMAT
        _TABAN_URL, _ANAHTAR_ENV = '', anahtar or GOMME_ANAHTAR_ENV[ad]
        return
    if not taban or not model:
        raise ValueError(
            f'HATIR_SAGLAYICI bilinmiyor: {ad!r} (taban_url ve model gerekli; gemini | nebius hazırdır)')
    # Önce doğrula: geçersiz URL yarım ayarlanmış global bırakmasın.
    taban = url_dogrula(taban, 'HATIR_URL / taban_url')
    SAGLAYICI, MODEL, SORGU_TALIMAT = ad, model, ''
    _TABAN_URL, _ANAHTAR_ENV = taban, anahtar


PARCA_HEDEF = 1200
PARTI = 32
RRF_K = 60
KLASORLER_VARSAYILAN = ('knowledge', 'notes', 'tasks')


def klasorler():
    """Dizine girecek kasa klasörleri: HATIR_KLASORLER (virgülle), yoksa avenoxbeyin düzeni."""
    ham = os.environ.get('HATIR_KLASORLER', '')
    secilen = tuple(k.strip().strip('/\\') for k in ham.split(',') if k.strip().strip('/\\'))
    return secilen or KLASORLER_VARSAYILAN


def varsayilan_embed(texts, **kw):
    saglayici_hazirla(zorunlu=True)
    if SAGLAYICI == 'gemini' and not _TABAN_URL:
        return _gemini.embed(texts, **kw)
    if SAGLAYICI == 'nebius' and not _TABAN_URL:
        from . import nebius  # ağ modülü yalnız gerçekten gerekince yüklenir
        return nebius.embed(texts, **kw)
    from . import uyumlu
    return uyumlu.embed(
        texts, taban_url=_TABAN_URL, model=kw.pop('model', None) or MODEL,
        anahtar_env=_ANAHTAR_ENV, **{k: v for k, v in kw.items() if k in ('timeout', 'deneme', 'bekle')})


def onbellek_yolu(vault):
    # Her sağlayıcının önbelleği ayrı: biri dizinlenince ötekinin vektörleri silinmez.
    name = 'vektorler.json' if SAGLAYICI in ('', 'nebius') else f'vektorler-{SAGLAYICI}.json'
    return Path(vault) / '.hatir' / 'anlam' / name


# --- Bağlam cümlesi (contextual retrieval) -------------------------------------
# Her parçanın başına notu konumlandıran 1-2 cümle eklenir, sonra gömülür (Anthropic contextual retrieval'ın
# uyarlaması). Notlar değişmez; cümleler `baglam.json` önbelleğinde parça metninin özetiyle saklanır.
# Bağlam varsayılan kapalıdır; yalnız gemini sağlayıcısında HATIR_BAGLAM=1 ile açılır.
BAGLAM_SURUM = 'baglam-v1'
BAGLAM_ISTEM = ("Aşağıda bir Markdown notun tamamı ve ondan alınan bir parça var. Parçayı arama için konumlandıran, "
                "notun konusunu ve parçanın notta neyi anlattığını söyleyen KISA bir Türkçe bağlam yaz (en çok 2 cümle, "
                "en çok 60 kelime). Notun adını, projeyi, tarihi ve parçada geçmeyen ama notta geçen anahtar kavramları "
                "(araç, kişi dışı adlar, karar) ekle. Yalnız bağlam metnini yaz, başka hiçbir şey yazma.\n\n"
                "<not>\n{belge}\n</not>\n\n<parca>\n{parca}\n</parca>")


def baglam_yolu(vault):
    return Path(vault) / '.hatir' / 'anlam' / 'baglam.json'


def baglam_acik():
    return SAGLAYICI == 'gemini' and os.environ.get('HATIR_BAGLAM', '').strip() == '1'


def varsayilan_baglam(belge, parca):
    """Gemini Flash-Lite ile bağlam cümlesi; sır kalıbı içeren parça gönderilmez (boş bağlam)."""
    if SIR_KALIBI.search(parca) or SIR_KALIBI.search(belge):
        return ''
    return _gemini.uret(BAGLAM_ISTEM.format(belge=belge[:60000], parca=parca))


def baglamli_metin(text, ctx):
    """'Başlık: X\\n<gövde>' -> 'Başlık: X\\nBağlam: <ctx>\\n<gövde>' (gemini.belge_metni başlık kalıbı korunur)."""
    if not ctx:
        return text
    first, _, rest = text.partition('\n')
    return f'{first}\nBağlam: {ctx}\n{rest}'


def baglam_oku(vault):
    try:
        data = json.loads(baglam_yolu(vault).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def baglam_yaz(vault, data):
    target = baglam_yolu(vault)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.replace(temp, target)


# --- Parçalama ---------------------------------------------------------------

def _birimler(body):
    """Boş satır ve başlık sınırlarından paragraf birimleri."""
    units, current = [], []
    for line in body.splitlines():
        if line.lstrip().startswith('#') and current:
            units.append('\n'.join(current).strip())
            current = []
        if not line.strip():
            if current:
                units.append('\n'.join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        units.append('\n'.join(current).strip())
    return [u for u in units if u]


def _bol(unit, hedef):
    """Hedefi aşan tek birimi satır, gerekirse sabit uzunlukla böl."""
    pieces, current = [], ''
    for line in unit.splitlines():
        while len(line) > hedef:
            if current:
                pieces.append(current)
                current = ''
            pieces.append(line[:hedef])
            line = line[hedef:]
        if current and len(current) + 1 + len(line) > hedef:
            pieces.append(current)
            current = ''
        current = f'{current}\n{line}' if current else line
    if current:
        pieces.append(current)
    return pieces


def parcala(body, baslik, hedef=PARCA_HEDEF):
    """Gövdeyi ~hedef karakterlik parçalara böl; her parçaya `Başlık:` eki ekle."""
    onek = f'Başlık: {baslik}\n'
    chunks, current = [], ''
    for unit in _birimler(body):
        for piece in (_bol(unit, hedef) if len(unit) > hedef else [unit]):
            if current and len(current) + 2 + len(piece) > hedef:
                chunks.append(current)
                current = ''
            current = f'{current}\n\n{piece}' if current else piece
    if current:
        chunks.append(current)
    return [onek + chunk for chunk in chunks] or [onek]


def parca_anahtari(model, text):
    return hashlib.sha256((model + text).encode('utf-8')).hexdigest()


# --- Not toplama ---------------------------------------------------------------

def uzaga_gidebilir(metadata):
    """visibility private ya da remote_allowed false olan not uzak servise gönderilmez."""
    visibility = str(metadata.get('visibility', '')).strip().casefold()
    remote = metadata.get('remote_allowed', True)
    if isinstance(remote, str):
        remote = remote.strip().casefold() not in ('false', 'no', 'hayir', 'hayır', '0')
    return visibility != 'private' and remote is not False


def notlari_topla(vault):
    """Dizine girecek (emekli/eskimiş olmayan) notlar: [{not, baslik, project, govde}]."""
    vault = Path(vault)
    functions = beyin_islevleri(vault)
    notes = []
    for folder in klasorler():
        for path in sorted((vault / folder).rglob('*.md')):
            relative = path.relative_to(vault)
            parts = [unicodedata.normalize('NFC', p).casefold() for p in relative.parts]
            if 'arşiv' in parts[:-1] or relative.parts[:2] == ('knowledge', 'v3'):
                continue
            try:
                if not path.resolve().is_relative_to(vault.resolve()):
                    continue
                metadata, body = on_bilgi(path.read_text(encoding='utf-8-sig'), functions['parse'])
            except (OSError, UnicodeError):
                continue
            notes.append({'not': relative.as_posix(), 'metadata': metadata, 'govde': body})
    replaced = yerine_gecenler(notes, functions['resolve_supersedes'])
    result = []
    for note in notes:
        metadata = note['metadata']
        if functions['status_word'](metadata) in functions['retired'] or replaced.get(note['not']):
            continue
        # Private or remote-disallowed notes never leave the machine.
        if not uzaga_gidebilir(metadata):
            continue
        title = metadata.get('title')
        project = metadata.get('project')
        result.append({'not': note['not'], 'govde': note['govde'],
                       'baslik': title.strip() if isinstance(title, str) and title.strip()
                       else Path(note['not']).stem,
                       'project': project if isinstance(project, str) and project else None})
    return result


# --- Önbellek ---------------------------------------------------------------

def onbellek_oku(vault, model=None):
    if model is None:
        model = MODEL
    try:
        data = json.loads(onbellek_yolu(vault).read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('parcalar'), dict) and isinstance(data.get('notlar'), dict):
            data['parcalar'] = {k: v for k, v in data['parcalar'].items()
                                if isinstance(v, list) and v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)}
            return data
    except (OSError, ValueError):
        pass
    return {'model': model, 'son_dizin': None, 'toplam_token': 0, 'parcalar': {}, 'notlar': {}}


def onbellek_yaz(vault, data):
    """Atomik: geçici dosya + replace."""
    target = onbellek_yolu(vault)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.replace(temp, target)


def dizinle(vault, embed_fn=None, model=None, now=None, log=print, baglam_fn=None):
    """Yeni/değişen parçaları vektörle; silinen notların parçalarını çıkar.

    baglam_fn(belge, parca) -> str: parça başına bağlam cümlesi (None: sağlayıcı gemini ise varsayılan üretici,
    değilse bağlam yok). Bağlam yalnız yeni/değişen parçalar için üretilir; `baglam.json` önbelleğinde kalır."""
    if model is None:
        model = MODEL
    embed_fn = embed_fn or varsayilan_embed
    if baglam_fn is None and baglam_acik():
        baglam_fn = varsayilan_baglam
    cache = onbellek_oku(vault, model)
    baglamlar = baglam_oku(vault) if baglam_fn else {}
    yeni_baglam = 0
    notlar, needed = {}, {}
    sirli = 0
    for note in notlari_topla(vault):
        keys = []
        belge = f"Başlık: {note['baslik']}\nNot: {note['not']}\nProje: {note['project'] or 'genel'}\n\n{note['govde']}"
        for text in parcala(note['govde'], note['baslik']):
            if SIR_KALIBI.search(text):
                # Sır kalıbı taşıyan parça uzak servise gönderilmez; aramada da yer almaz.
                sirli += 1
                continue
            if baglam_fn:
                bkey = parca_anahtari(BAGLAM_SURUM, text)
                if bkey not in baglamlar:
                    try:
                        baglamlar[bkey] = baglam_fn(belge, text) or ''
                    except Exception as exc:  # noqa: BLE001  # bağlam üretilemezse parça bağlamsız gömülür
                        log(f'Bağlam üretilemedi ({type(exc).__name__}); parça bağlamsız')
                        baglamlar[bkey] = ''
                    yeni_baglam += 1
                    if yeni_baglam % 50 == 0:
                        baglam_yaz(vault, baglamlar)
                text = baglamli_metin(text, baglamlar[bkey])
            key = parca_anahtari(model, text)
            keys.append(key)
            needed[key] = text
        notlar[note['not']] = {'project': note['project'], 'parcalar': keys}
    if baglam_fn and yeni_baglam:
        baglam_yaz(vault, baglamlar)
        log(f'Bağlam: {yeni_baglam} yeni cümle')
    if sirli:
        log(f'Uyarı: {sirli} parça sır kalıbı taşıdığı için gönderilmedi (notu temizleyin)')
    missing = [(key, text) for key, text in needed.items() if key not in cache['parcalar']]
    used = 0
    try:
        for start in range(0, len(missing), PARTI):
            batch = missing[start:start + PARTI]
            vectors, tokens = embed_fn([text for _, text in batch], model=model, timeout=60)
            if len(vectors) != len(batch):
                raise RuntimeError(f'{len(batch)} parçaya {len(vectors)} vektör döndü')
            used += tokens
            cache['toplam_token'] += tokens
            for (key, _), vector in zip(batch, vectors):
                cache['parcalar'][key] = [round(float(x), 6) for x in vector]
            log(f'Parti {start // PARTI + 1}: {len(batch)} parça, {tokens} token')
    finally:
        # Yarım kalsa da gelen vektörler kaybolmasın; not eşlemesi yalnız tamamlanınca güncellenir.
        if all(key in cache['parcalar'] for key in needed):
            cache['notlar'] = notlar
            cache['parcalar'] = {k: v for k, v in cache['parcalar'].items() if k in needed}
            cache['model'] = model
            cache['son_dizin'] = (now or datetime.now().astimezone()).isoformat()
        onbellek_yaz(vault, cache)
    stats = {'not': len(notlar), 'parca': len(needed), 'yeni_parca': len(missing), 'sirli_atlanan': sirli,
             'token': used, 'toplam_token': cache['toplam_token']}
    log(f"Dizin: {stats['not']} not, {stats['parca']} parça, {stats['yeni_parca']} yeni; "
        f"token bu koşu {used}, toplam {cache['toplam_token']}")
    return stats


# --- Arama ---------------------------------------------------------------

def kosinus(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


# Sorgu uzak servise gitmeden önce sır kalıbı denetimi: sır kalıplı sorgu ağa çıkmaz.
SIR_KALIBI = re.compile(r'(?<![A-Za-z0-9])(?:sk-|ghp_|gho_|ghs_|ghu_|github_pat_|xoxb-|xoxp-|xoxa-|glpat-)[A-Za-z0-9_-]{16,}'
                        r'|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [^\r\n]*PRIVATE KEY-----'
                        r'|hooks\.slack\.com/services/\S+|(?<![A-Za-z0-9])(?:sk|rk)_live_[A-Za-z0-9]{16,}'
                        r'|(?i:\b(?:password|passwd|parola|şifre|sifre|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*["\']?[^\s"\']{8,})')


def sorgu_vektoru(soru, embed_fn, model=None):
    """10 sn zaman aşımı, 1 yeniden deneme (Nebius'ta ara ara takılma var)."""
    if model is None:
        model = MODEL
    if SIR_KALIBI.search(soru):
        raise RuntimeError('Sorgu sır kalıbı içeriyor; uzak servise gönderilmedi')
    last = None
    for _ in range(2):
        try:
            vectors, _ = embed_fn([SORGU_TALIMAT + soru], model=model, timeout=10, deneme=1)
            return vectors[0]
        except Exception as exc:  # noqa: BLE001  # tek hatalı girdi koşuyu durdurmamalı
            last = exc
    raise RuntimeError(f'Sorgu embedding alınamadı: {last}')


def anlam_ara(cache, sorgu, project=None):
    """Not puanı = parçalarının en yüksek kosinüsü; [(yol, puan)] azalan."""
    rows = []
    for source, info in cache['notlar'].items():
        if project and info.get('project') not in (project, None):
            continue
        scores = [kosinus(sorgu, cache['parcalar'][key]) for key in info['parcalar']
                  if key in cache['parcalar']]
        if scores:
            rows.append((source, max(scores)))
    return sorted(rows, key=lambda row: (-row[1], row[0]))


def rrf(*siralar, k=RRF_K):
    """Reciprocal Rank Fusion; her sıra bir kaynak listesi. [(kaynak, puan)] azalan."""
    scores, names = {}, {}
    for sira in siralar:
        for rank, source in enumerate(sira, 1):
            scores[yol(source)] = scores.get(yol(source), 0.0) + 1 / (k + rank)
            names.setdefault(yol(source), source)
    order = sorted(scores, key=lambda key: (-scores[key], key))
    return [(names[key], scores[key]) for key in order]


def beyin_sirasi(vault, soru, project, python):
    args = ['context', soru] + (['--project', project] if project else []) + ['--no-sync', '--json']
    data, error = beyin_calistir(Path(vault), python, args)
    records = data.get('records') if data else None
    if not isinstance(records, list):
        raise RuntimeError(error or 'beyin JSON records listesi yok')  # noqa: TRY004  # çağıran ValueError yakalar
    return kaynaklar(records)


def ara(vault, soru, project=None, k=10, karma=False, embed_fn=None, python=None, model=None):
    """{'records': [...], 'yontem': 'anlam'|'karma'[, 'degraded': True]}; karma dışında hata fırlatır."""
    if model is None:
        model = MODEL
    embed_fn = embed_fn or varsayilan_embed
    python = python or sys.executable
    degraded = False
    try:
        cache = onbellek_oku(vault, model)
        if not cache['notlar']:
            raise RuntimeError('Dizin yok; önce `anlam.py dizin` çalıştırın')
        if cache.get('model') != model:
            raise RuntimeError(f"Dizin eski modelle ({cache.get('model')}); yeniden anlam.py dizin çalıştırın")
        emb = anlam_ara(cache, sorgu_vektoru(soru, embed_fn, model), project)
    except (TypeError, KeyError, ValueError) as exc:
        if not karma:
            raise RuntimeError(f'Önbellek/arama hatası: {type(exc).__name__}') from None
        emb, degraded = None, True
    except RuntimeError:
        if not karma:
            raise
        emb, degraded = None, True
    if not karma:
        ranked = emb
    else:
        try:
            lexical = beyin_sirasi(vault, soru, project, python)
        except RuntimeError:
            if emb is None:
                raise
            lexical, degraded = None, True
        if emb is None:
            ranked = [(s, 1 / (RRF_K + i)) for i, s in enumerate(lexical, 1)]
        elif lexical is None:
            ranked = emb
        else:
            # İki liste de k'ye kesilir: beyin ~5 kayıt döndürür; dense tam liste verilirse lexical'daki her
            # not dense'te de bulunup iki puan toplar ve dense'in tek başına bulduğu not ilk k'ye giremez
            # (ölçümde yetenek setinde belirgin artış, gerileme setinde değişiklik yok).
            ranked = rrf(lexical[:k], [s for s, _ in emb[:k]])
    out = {'records': [{'source': s, 'score': float(p)} for s, p in ranked[:k]],
           'yontem': 'karma' if karma else 'anlam'}
    if degraded:
        out['degraded'] = True
    return out


def durum(vault):
    cache = onbellek_oku(vault)
    return {'not': len(cache['notlar']), 'parca': len(cache['parcalar']), 'model': cache['model'],
            'son_dizin': cache['son_dizin'], 'toplam_token': cache['toplam_token']}


def main(argv=None, embed_fn=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='komut', required=True)
    for name in ('dizin', 'ara', 'durum'):
        p = sub.add_parser(name)
        p.add_argument('--vault', type=Path, default=os.environ.get('HATIR_KASA') or None,
                       help='beyin vault kökü (varsayılan: HATIR_KASA ortam değişkeni)')
        if name == 'ara':
            p.add_argument('soru')
            p.add_argument('--project')
            p.add_argument('--k', type=int, default=10)
            p.add_argument('--karma', action='store_true')
            p.add_argument('--rerank', action='store_true',
                           help='sözcük + anlam adaylarını [yeniden_siralama] modeliyle sırala (düşerse karma)')
            p.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.vault is None:
        parser.error('--vault verin ya da HATIR_KASA ortam değişkenini ayarlayın')
    vault = args.vault.resolve()
    if args.komut == 'ara' and args.k < 1:
        parser.error('--k en az 1 olmalı')
    rerank_modu = args.komut == 'ara' and args.rerank
    on_uyari = []
    try:
        saglayici_hazirla(zorunlu=embed_fn is None and args.komut != 'durum' and not rerank_modu)
    except ValueError as exc:
        if not rerank_modu:
            print(f'Hata: {exc}', file=sys.stderr)
            return 1
        on_uyari.append(f'gömme ayarı: {exc}')
    if args.komut == 'dizin':
        try:
            dizinle(vault, embed_fn)
        except Exception as exc:  # noqa: BLE001  # tek hatalı girdi koşuyu durdurmamalı
            print(f'Hata: {exc}', file=sys.stderr)
            return 1
    elif args.komut == 'durum':
        for key, value in durum(vault).items():
            print(f'{key}: {value}')
    else:
        try:
            if rerank_modu:
                # Yerel içe aktarma döngüyü önler: rerank bu modülü kullanır.
                from . import rerank
                result = rerank.ara(vault, args.soru, args.project, args.k, embed_fn)
                result['uyari'] = on_uyari + result['uyari']
            else:
                result = ara(vault, args.soru, args.project, args.k, args.karma, embed_fn)
        except RuntimeError as exc:
            print(f'Hata: {exc}', file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for row in result['records']:
                print(f"{row['score']:.4f}  {row['source']}")
            if rerank_modu:
                print(f"[{result['yontem']}{', degraded' if result['degraded'] else ''}]"
                      + ''.join(f'\nUyarı: {u}' for u in result['uyari']))
            elif result.get('degraded'):
                print('Uyarı: yalnız tek yöntem sonucu (degraded)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
