"""Markdown notları için açıklanabilir kalite karnesi. Komut: `hatir-karne`."""
import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

sys.dont_write_bytecode = True

from ..ortak import beyin_calistir, json_oku, json_yaz, puan_dosyalari, yol
from .gecerlilik import donguleri_bul, valid_to_oku

AGIRLIKLAR = {'dogrulama': 0.30, 'kullanim': 0.20, 'baglanti': 0.15,
              'tazelik': 0.15, 'bulunabilirlik': 0.20}
# beyin_v3.py RETIRED_STATUSES / _status_word ile aynı yedek davranış.
RETIRED_STATUSES = frozenset(('superseded', 'retired', 'archived', 'archive', 'deprecated',
                              'obsolete', 'replaced', 'arsiv', 'arsivlendi', 'eski', 'emekli', 'gecersiz'))
_STATUS_FOLD = str.maketrans('ŞşĞğÜüÇçÖöİIı', 'SsGgUuCcOoiii')


def status_word(record):
    value = record.get('status')
    if not isinstance(value, str) or not value.strip():
        return '' if record.get('kind') == 'task' else 'active'
    match = re.match(r'[^\W_]+', unicodedata.normalize('NFC', value.strip()).translate(_STATUS_FOLD).casefold())
    return match.group(0) if match else ''


def arsivli_mi(note, retired_statuses=RETIRED_STATUSES, status_parser=status_word):
    return status_parser(note['metadata']) in retired_statuses


def beyin_islevleri(vault):
    """Kasanın kendi ayrıştırıcısı; yalnız HATIR_KASA_KODU=1 iken içe aktarılır (kasadaki Python kodu çalışır).

    Kapalıyken (varsayılan) paket içindeki yedek ayrıştırıcı kullanılır. Yalnız fonksiyon import edilir;
    store/sync oluşturulmaz, .pyc yazılmaz."""
    result = {'parse': None, 'resolve_supersedes': resolve_supersedes,
              'retired': RETIRED_STATUSES, 'status_word': status_word}
    scripts = Path(vault) / '.claude' / 'scripts'
    if os.environ.get('HATIR_KASA_KODU') != '1' or not scripts.is_dir():
        return result
    sys.path.insert(0, str(scripts))
    try:
        for name in ('beyin_v3', 'beyin_v3_sync'):
            try:
                module = importlib.import_module(name)
                # Başka vault'tan önceden yüklenmiş modülü kullanma.
                if Path(module.__file__).resolve().parent != scripts.resolve():
                    continue
                if name == 'beyin_v3_sync':
                    result['parse'] = module.parse
                else:
                    result.update(resolve_supersedes=module.resolve_supersedes,
                                  retired=module.RETIRED_STATUSES, status_word=module._status_word)
            except (ImportError, OSError, AttributeError, SyntaxError):
                continue
    finally:
        sys.path.remove(str(scripts))
    return result


def yaml_degeri(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    try:
        return json.loads(value)
    except ValueError:
        if value.startswith(('"', '[', '{')) and not value.startswith('[['):
            raise ValueError('Bozuk YAML değeri')
        return value if value else None


def yedek_parse(text):
    """JSON ve sınırlı YAML: scalar, blok liste ve tek katman facts mapping."""
    lines = text.lstrip('\ufeff').splitlines()
    if not lines or lines[0].strip() != '---':
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == '---')
    except StopIteration as exc:
        raise ValueError('Kapanmamış ön bilgi') from exc
    body = '\n'.join(lines[end + 1:])
    header = '\n'.join(lines[1:end]).strip()
    if header.startswith(('{', '[')):
        metadata = json.loads(header)
        if not isinstance(metadata, dict):
            raise ValueError('Ön bilgi nesne olmalı')
        return metadata, body
    metadata, key, container = {}, None, None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.lstrip().startswith('- ') and key is not None and container in (None, 'list'):
            if container is None:
                metadata[key], container = [], 'list'
            metadata[key].append(yaml_degeri(line.lstrip()[2:]))
            continue
        match = re.fullmatch(r'( *)([^\W\d][\w-]*):[ \t]*(.*)', line)
        if not match:
            raise ValueError('Desteklenmeyen YAML')
        indent, name, value = match.groups()
        if indent:
            if key is None or container not in (None, 'mapping'):
                raise ValueError('Desteklenmeyen YAML girintisi')
            if container is None:
                metadata[key], container = {}, 'mapping'
            if name in metadata[key]:
                raise ValueError('Tekrarlı YAML anahtarı')
            metadata[key][name] = yaml_degeri(value)
        else:
            if name in metadata:
                raise ValueError('Tekrarlı YAML anahtarı')
            metadata[name] = yaml_degeri(value)
            key, container = (name, None) if not value.strip() else (None, None)
    return metadata, body


def on_bilgi(text, parser=None):
    """Parser hatası notu düşürmez; yalnız metadata boş sayılır."""
    try:
        metadata, body = (parser or yedek_parse)(text)
        return (metadata if isinstance(metadata, dict) else {}), body
    except (ValueError, TypeError):
        lines = text.lstrip('\ufeff').splitlines()
        if lines and lines[0].strip() == '---':
            end = next((i for i in range(1, len(lines)) if lines[i].strip() == '---'), None)
            if end is not None:
                return {}, '\n'.join(lines[end + 1:])
        return {}, text


def dogrulama_sinyali(value):
    """facts.dogrulama değeri -> 0-1: alan yok/boş 0.1, gözlendi 0.3 (bilinmeyenle aynı,
    ama açıkça anılır), doğrulandı/kaynak/okuma 0.6, kullanıcı 0.9, test/ölçüm/deney 1.0."""
    value = str(value or '').casefold().strip()
    if not value:
        return 0.1
    if any(word in value for word in ('test', 'ölçüm', 'olcum', 'deney')):
        return 1.0
    if 'kullanıcı' in value:
        return 0.9
    if any(word in value for word in ('kaynak', 'okuma')):
        return 0.6
    if any(word in value for word in ('doğrula', 'dogrula')):
        return 0.6
    if any(word in value for word in ('gözlendi', 'gozlendi')):
        return 0.3
    return 0.3


def sayi_sinyali(count):
    return (0.0, 0.5, 0.75, 1.0)[min(max(count, 0), 3)]


def tazelik_sinyali(days):
    return 1.0 if days <= 30 else 0.6 if days <= 90 else 0.3


def agirliklar(signals):
    active = {key: weight for key, weight in AGIRLIKLAR.items() if signals.get(key) is not None}
    total = sum(active.values())
    return {key: weight / total for key, weight in active.items()} if total else {}


def puan_ve_oneriler(signals, eskimis=False, tekrar_ile=()):
    weights = agirliklar(signals)
    score = sum(signals[key] * weight for key, weight in weights.items()) * 100
    suggestions = []
    if eskimis:
        score = min(score, 20)
        suggestions.append('arşivle')
    suggestions.extend(f'birleştir: {source}' for source in tekrar_ile)
    if signals.get('dogrulama', 0) < 0.5:
        suggestions.append('doğrula')
    if signals.get('kullanim') == 0 and signals.get('baglanti') == 0:
        suggestions.append('bağla ya da arşivle')
    if signals.get('bulunabilirlik') == 0:
        suggestions.append('başlık/anahtar kelime güçlendir')
    return round(score, 2), suggestions


def notlari_oku(vault, parser=None):
    parser = parser or beyin_islevleri(vault)['parse']
    notes, warnings = [], []
    for folder in ('knowledge', 'notes'):
        for path in sorted((vault / folder).rglob('*.md')):
            relative = path.relative_to(vault)
            if any(part.casefold() in ('arşiv', 'v3') for part in relative.parts[:-1]):
                continue
            try:
                if not path.resolve().is_relative_to(vault.resolve()):
                    warnings.append(f'Vault dışına yönlenen not atlandı: {relative.as_posix()}')
                    continue
                metadata, body = on_bilgi(path.read_text(encoding='utf-8-sig'), parser)
                notes.append({'not': relative.as_posix(), 'metadata': metadata,
                              'govde': body, 'path': path})
            except (OSError, UnicodeError) as exc:
                warnings.append(f'{relative.as_posix()}: {exc}')
    return notes, warnings


def anilma_deseni(source, wiki=False):
    source = yol(source)
    stem = re.escape(Path(source).stem)
    # Wiki alternatifi önce gelir: içindeki yol/stem aynı anılışta tekrar sayılmaz.
    alternatives = [r'\[\[(?:[^\]|#]*/)?' + stem + r'(?:[|#^][^\]]*)?\]\]']
    alternatives.append(r'(?<![\w/])' + re.escape(source) + r'(?![\w])')
    if not wiki:
        alternatives.append(r'(?<![\w/])' + stem + r'(?![\w])')
    return re.compile('|'.join(alternatives))


def anilma_sayisi(text, source, wiki=False, desen=None):
    """Normalleştirilmiş metinde örtüşmeyen anılışlar; dışarıdan derlenebilir."""
    return sum(1 for _ in (desen or anilma_deseni(source, wiki)).finditer(yol(text)))


def bulunabilirlik_sinyalleri(result):
    values = defaultdict(list)
    k = result.get('k', 5)
    for question in result.get('sorular', []):
        if question.get('hata'):
            continue
        rank = question.get('sira')
        found = rank is not None and rank <= k
        for source in question.get('beklenen', []):
            values[yol(source)].append(int(found))
    return {source: sum(hits) / len(hits) for source, hits in values.items()}


def not_id(note):
    # beyin_v3_sync.py:37 _hash(str) = sha256(str.encode()).hexdigest();
    # :854 record.setdefault('id', 'md-' + _hash(relative)[:24]).
    return note['metadata'].get('id', 'md-' + hashlib.sha256(note['not'].encode('utf-8')).hexdigest()[:24])


def kimlikler(note):
    result = {note['not']}
    ident = not_id(note)
    if isinstance(ident, str) and ident:
        result.add(ident)
    return result


def _trusted(record):
    rejected = record.get('kind') in ('inference', 'preference') and (
        record.get('validity') == 'rejected' or record.get('status') == 'rejected')
    return (record.get('trust') != 'untrusted' and record.get('trusted') is not False
            and record.get('status') != 'untrusted' and record.get('kind') != 'untrusted' and not rejected)


def resolve_supersedes(records):
    """beyin_v3.resolve_supersedes yedeği: tam id/yol, sonra tekil dosya adı."""
    trusted = [r for r in records if isinstance(r, dict) and _trusted(r)]
    by_id = {r['id']: r for r in trusted if isinstance(r.get('id'), str)}
    by_path, by_name = defaultdict(list), defaultdict(list)
    for record in trusted:
        source = yol(record.get('source', '')).strip('/')
        key = source.removesuffix('.md')
        if key:
            by_path[key].append(record)
            by_name[key.rsplit('/', 1)[-1]].append(record)
    retired, dead = set(), []
    for record in trusted:
        values = record.get('supersedes', [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for raw in values:
            key = raw.strip() if isinstance(raw, str) else ''
            if key.startswith('[[') and key.endswith(']]'):
                key = re.split(r'[|#^]', key[2:-2], maxsplit=1)[0].strip()
            key = key.replace('\\', '/').strip('/')
            folded = yol(key).removesuffix('.md')
            target, reason = by_id.get(key), None
            if target is None and folded:
                matches = by_path.get(folded, []) or by_name.get(folded.rsplit('/', 1)[-1], [])
                if len(matches) == 1:
                    target = matches[0]
                elif matches:
                    reason = 'ambiguous supersedes reference'
            if target is None:
                reason = reason or 'unresolved supersedes reference'
            elif target is record or target.get('id') == record.get('id'):
                target, reason = None, 'supersedes reference points to the note itself'
            if target is None:
                dead.append({'id': record.get('id'), 'source': record.get('source', ''), 'value': raw, 'reason': reason})
            else:
                retired.add(target['id'])
    return retired, dead


def yerine_gecenler(notes, resolver=None):
    resolver = resolver or resolve_supersedes
    def normalize_refs(value):
        if isinstance(value, str):
            return unicodedata.normalize('NFC', value)
        if isinstance(value, list):
            return [normalize_refs(v) for v in value]
        return value
    records = [{**note['metadata'], 'id': not_id(note),
                'supersedes': normalize_refs(note['metadata'].get('supersedes', [])),
                'source': unicodedata.normalize('NFC', note['not'])} for note in notes]
    by_id = {r['id']: n['not'] for r, n in zip(records, notes)}
    replaced = defaultdict(set)
    # Resolver retired id'leri döndürür. Kaynak atfını korumak için yalnız
    # supersedes taşıyan kayıtların ilişkilerini ayrı ayrı çöz.
    for record in records:
        if not record.get('supersedes'):
            continue
        scoped = [{**r, 'supersedes': record['supersedes'] if r is record else []} for r in records]
        retired, _ = resolver(scoped)
        for ident in retired:
            if ident in by_id:
                replaced[by_id[ident]].add(by_id[record['id']])
    return replaced


def supersedes_donguleri(replaced_by):
    """yerine_gecenler çıktısından (hedef -> kaynaklar) kaynak -> hedef kenarlarıyla döngüler."""
    kenarlar = defaultdict(set)
    for hedef, kaynaklar in replaced_by.items():
        for kaynak in kaynaklar:
            kenarlar[kaynak].add(hedef)
    return donguleri_bul(kenarlar)


def tekrarlar(notes, audit):
    aliases = defaultdict(set)
    for note in notes:
        for key in kimlikler(note):
            aliases[yol(key)].add(note['not'])
    pairs = defaultdict(set)
    for finding in audit.get('findings', []):
        if not isinstance(finding, dict):
            continue
        try:
            jaccard = float(finding.get('jaccard', 0))
        except (TypeError, ValueError):
            continue
        if not jaccard >= 0.5:
            continue
        older, newer = finding.get('older_note'), finding.get('newer_note')
        if not isinstance(older, str) or not isinstance(newer, str):
            continue
        for a in aliases.get(yol(older), ()):
            for b in aliases.get(yol(newer), ()):
                if a != b:
                    pairs[a].add(b)
                    pairs[b].add(a)
    return {key: sorted(value) for key, value in pairs.items()}


def son_degisim(vault, path):
    """Git bilgisi yoksa mtime; alt süreç en fazla 5 saniye."""
    try:
        process = subprocess.run(['git', 'log', '-1', '--format=%cI', '--',
                                  path.relative_to(vault).as_posix()], cwd=vault,
                                 capture_output=True, encoding='utf-8', errors='replace', timeout=5, check=False)
        if process.returncode == 0 and process.stdout.strip():
            return datetime.fromisoformat(process.stdout.strip()), 'git'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, UTC), 'mtime'


def karne_hesapla(notes, receipt_texts, dates, retrieval=None, audit=None, now=None,
                  resolver=None, retired_statuses=RETIRED_STATUSES, status_parser=status_word,
                  bugun=None, replaced_by=None):
    """I/O'dan bağımsız hesap; dates: yol -> (aware datetime, kaynak)."""
    now = now or datetime.now(UTC)
    bugun = bugun or date.today()
    retrieval = {yol(key): value for key, value in (retrieval or {}).items()}
    duplicate_map = tekrarlar(notes, audit or {})
    if replaced_by is None:
        replaced_by = yerine_gecenler(notes, resolver)
    donguler = supersedes_donguleri(replaced_by)
    dongude = {ad for dongu in donguler for ad in dongu}
    bilesen = {ad: i for i, dongu in enumerate(donguler) for ad in dongu}
    receipt_texts = [yol(text) for text in receipt_texts]
    # Arşivli notlar satır üretmez ve gövdeleri bağlantı kaynağı sayılmaz;
    # supersedes çözümü yine tüm notlarla yapılır.
    active = [note for note in notes if not arsivli_mi(note, retired_statuses, status_parser)]
    bodies = {note['not']: yol(note['govde']) for note in active}
    rows = []
    for note in active:
        source, metadata = note['not'], note['metadata']
        facts = metadata.get('facts', {})
        value = facts.get('dogrulama') if isinstance(facts, dict) else None
        usage_pattern = anilma_deseni(source)
        link_pattern = anilma_deseni(source, wiki=True)
        usage = sum(sum(1 for _ in usage_pattern.finditer(text)) for text in receipt_texts)
        links = sum(sum(1 for _ in link_pattern.finditer(text))
                    for other_source, text in bodies.items() if other_source != source)
        changed, origin = dates[source]
        age = max(0, (now - changed).total_seconds() / 86400)
        signals = {'dogrulama': dogrulama_sinyali(value), 'kullanim': sayi_sinyali(usage),
                   'baglanti': sayi_sinyali(links), 'tazelik': tazelik_sinyali(age),
                   'bulunabilirlik': retrieval.get(yol(source))}
        # Motorla aynı: aynı döngüye ait kenar hedefini eskimiş saymaz.
        replacements = sorted(s for s in replaced_by.get(source, set())
                              if source not in bilesen or bilesen.get(s) != bilesen[source])
        obsolete = status_parser(metadata) in retired_statuses or bool(replacements)
        duplicates = duplicate_map.get(source, [])
        score, suggestions = puan_ve_oneriler(signals, obsolete, duplicates)
        gecerlilik = valid_to_oku(metadata, bugun)
        if source in dongude:
            suggestions.append('döngü: döngüdeki supersedes kenarı uygulanmaz (beyin ölü bağ sayar); notlar aramada kalır ama hiçbiri diğerinin yerine geçmez; güncel kalacak notun dışındaki kenarı kaldır')
        if gecerlilik['durum'] == 'gecmis':
            suggestions.append(f"süresi geçmiş: valid_to {gecerlilik['valid_to']}")
        elif gecerlilik['durum'] == 'gecersiz':
            suggestions.append(f"valid_to geçersiz: {gecerlilik['ham']!r}")
        rows.append({'not': source, 'id': not_id(note), 'proje': metadata.get('project'),
                     'dogrulama_degeri': (str(value).strip() or None) if value is not None else None,
                     'puan': score, 'sinyaller': signals, 'agirliklar': agirliklar(signals),
                     'kullanim_sayisi': usage, 'baglanti_sayisi': links,
                     'son_degisim': changed.isoformat(), 'tarih_kaynagi': origin,
                     'eskimis': obsolete, 'yerine_gecen': replacements,
                     'tekrar_ile': duplicates, 'oneriler': suggestions,
                     'supersedes_dongu': source in dongude, 'valid_to': gecerlilik['valid_to'],
                     'valid_to_durum': gecerlilik['durum']})
    return sorted(rows, key=lambda row: (row['puan'], row['not']))


def proje_ozeti(rows):
    projects = defaultdict(list)
    for row in rows:
        projects[str(row['proje'] or '(projesiz)')].append(row)
    return {name: {'not_sayisi': len(items),
                   'ortalama_puan': round(sum(r['puan'] for r in items) / len(items), 2),
                   'oneri_sayilari': dict(Counter(s.split(':', 1)[0] for r in items for s in r['oneriler']))}
            for name, items in sorted(projects.items())}


def markdown(result):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    lines = ['# Not karnesi', '', f"Tarih: {result['tarih']}", '',
             '| Puan | Not | Proje | Sinyaller | Öneri |', '| ---: | --- | --- | --- | --- |']
    for row in result['notlar']:
        signals = ', '.join(f'{key}: {value if value is not None else "yok"}'
                            for key, value in row['sinyaller'].items())
        lines.append('| ' + ' | '.join(cell(v) for v in
                     (row['puan'], row['not'], row['proje'] or '—', signals,
                      '; '.join(row['oneriler']) or '—')) + ' |')
    lines += ['', '## Proje özeti', '', '| Proje | Not | Ortalama puan | Öneri sayıları |',
              '| --- | ---: | ---: | --- |']
    for name, summary in result['projeler'].items():
        counts = ', '.join(f'{key}: {value}' for key, value in summary['oneri_sayilari'].items()) or '—'
        lines.append(f"| {cell(name)} | {summary['not_sayisi']} | {summary['ortalama_puan']} | {cell(counts)} |")
    lines += ['', f"Arşivli olduğu için atlanan not: {result.get('arsivli_atlanan', 0)}"]
    lines += ['', (f"Supersedes döngüsü: {len(result.get('supersedes_donguleri', []))}; "
                  f"süresi geçmiş not: {sum(1 for r in result['notlar'] if r.get('valid_to_durum') == 'gecmis')}")]
    if result['uyarilar']:
        lines += ['', '## Sınırlar', ''] + [f'- {cell(warning)}' for warning in result['uyarilar']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, default=os.environ.get('HATIR_KASA') or None,
                        help='not kasası kökü (varsayılan: HATIR_KASA ortam değişkeni)')
    parser.add_argument('--cikti', type=Path, default=None,
                        help='karne çıktı klasörü (varsayılan: <kasa>/.hatir/sonuclar)')
    parser.add_argument('--puan-sonucu', type=Path)
    parser.add_argument('--bugun', type=date.fromisoformat, default=None,
                        help='valid_to karşılaştırma günü YYYY-MM-DD (varsayılan: bugün)')
    args = parser.parse_args(argv)
    if args.vault is None:
        parser.error('--vault verin ya da HATIR_KASA ortam değişkenini ayarlayın')
    vault = args.vault.resolve()
    if args.cikti is None:
        args.cikti = vault / '.hatir' / 'sonuclar'
    if not vault.is_dir():
        parser.error(f'Vault klasörü yok: {vault}')
    functions = beyin_islevleri(vault)
    notes, warnings = notlari_oku(vault, functions['parse'])
    score_path = args.puan_sonucu
    if score_path is None:
        candidates = puan_dosyalari(args.cikti)
        score_path = candidates[-1] if candidates else None
    retrieval = None
    if score_path:
        try:
            data = json_oku(score_path)
            if not isinstance(data, dict) or not isinstance(data.get('sorular'), list):
                raise ValueError('puanla sonucu bekleniyor')  # noqa: TRY004  # çağıran ValueError yakalar
            retrieval = bulunabilirlik_sinyalleri(data)
        except (OSError, ValueError) as exc:
            parser.error(f'Puan sonucu okunamadı: {exc}')
    else:
        warnings.append('Puan sonucu yok; bulunabilirlik ağırlığı diğer sinyallere dağıtıldı.')
    audit, error = beyin_calistir(vault, sys.executable, ['audit', '--limit', '500', '--json'])
    if audit is None or not isinstance(audit.get('findings'), list):
        warnings.append(f'Tekrar denetimi atlandı: {error or "findings listesi yok"}')
        audit = None
    elif error:
        warnings.append(f'Tekrar denetimi geçerli JSON ile kullanıldı: {error}')
    receipts = []
    for path in sorted((vault / 'receipts').glob('*.md')):
        try:
            if path.resolve().is_relative_to(vault):
                receipts.append(path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError) as exc:
            warnings.append(f'Receipt okunamadı: {path.name}: {exc}')
    dates = {note['not']: son_degisim(vault, note['path']) for note in notes}
    replaced_by = yerine_gecenler(notes, functions['resolve_supersedes'])
    rows = karne_hesapla(notes, receipts, dates, retrieval, audit,
                        resolver=functions['resolve_supersedes'],
                        retired_statuses=functions['retired'], status_parser=functions['status_word'],
                        bugun=args.bugun, replaced_by=replaced_by)
    arsivli_atlanan = sum(1 for note in notes
                          if arsivli_mi(note, functions['retired'], functions['status_word']))
    now = datetime.now().astimezone()
    result = {'tarih': now.isoformat(), 'agirliklar': AGIRLIKLAR,
              # Mutlak yol kullanıcı adını taşıyabilir; çıktıya yalnız dosya adı yazılır.
              'puan_sonucu': Path(score_path).name if score_path else None,
              'audit_kullanildi': audit is not None, 'uyarilar': warnings,
              'arsivli_atlanan': arsivli_atlanan,
              'supersedes_donguleri': supersedes_donguleri(replaced_by),
              'notlar': rows, 'projeler': proje_ozeti(rows)}
    args.cikti.mkdir(parents=True, exist_ok=True)
    stem = args.cikti / ('karne-' + now.strftime('%Y-%m-%d'))
    json_yaz(stem.with_suffix('.json'), result)
    stem.with_suffix('.md').write_text(markdown(result), encoding='utf-8')
    print(f'Not: {len(rows)} | Ortalama: {sum(r["puan"] for r in rows) / len(rows) if rows else 0:.2f}')
    for warning in warnings:
        print(f'Uyarı: {warning}')
    print(f'JSON: {stem.with_suffix(".json")}')
    print(f'Markdown: {stem.with_suffix(".md")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
