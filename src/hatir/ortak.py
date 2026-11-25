"""Kasa yardımcıları; anlam ve puanla paketleri tarafından ortak kullanılır."""
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path


def yol(value):
    """Yalnız karşılaştırma anahtarı; kullanıcıya gösterilen yol değildir."""
    return unicodedata.normalize('NFC', str(value).replace('\\', '/').removeprefix('./')).casefold()


def kaynaklar(records):
    """İlk görülen kaynak sırasını koruyarak tekilleştir."""
    unique = {}
    for record in records:
        if isinstance(record, dict) and isinstance(record.get('source'), str) and record['source']:
            unique.setdefault(yol(record['source']), record['source'].replace('\\', '/'))
    return list(unique.values())


def json_yaz(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def json_oku(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def gizli_pencere(stdin=True):
    """Alt süreç ayarları: Windows'ta konsol penceresi açma, stdin'i kapat (2026-10-31).

    Zamanlanmış/konsolsuz (pythonw) koşuda alt süreç konsolu miras alamaz; git.exe'ler
    0xC0000142 (DLL başlatılamadı) ile ölüyordu. CREATE_NO_WINDOW alt sürece gizli bir konsol
    verir, çocukları da onu miras alır. `stdin=False`: çağıran input= veriyor.
    """
    ayar = {'stdin': subprocess.DEVNULL} if stdin else {}
    if sys.platform == 'win32':
        ayar['creationflags'] = subprocess.CREATE_NO_WINDOW
    return ayar


KASA_BETIGI_VARSAYILAN = 'kasa.py'


def kasa_betigi():
    """Kasanın komut satırı betiğinin adı; kurulum HATIR_KASA_BETIGI ile değiştirir."""
    return Path(os.environ.get('HATIR_KASA_BETIGI') or KASA_BETIGI_VARSAYILAN).name


def beyin_calistir(vault, python, args, json_bekle=True):
    # Alt süreçler de .pyc üretmesin. sync açıkça istenirse yazabilir.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    try:
        result = subprocess.run([python, str(vault / kasa_betigi()), *args],
                                cwd=vault, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=90, env=env, **gizli_pencere(), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    error = None
    if result.returncode:
        error = f'Çıkış {result.returncode}: {result.stderr.strip() or result.stdout.strip()}'
    if not json_bekle:
        return {}, error
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError('JSON nesnesi bekleniyor')  # noqa: TRY004  # çağıran ValueError yakalar
        return data, error
    except ValueError as exc:
        return None, error or f'Geçersiz JSON: {exc}; {result.stderr.strip()}'


def puan_dosyalari(folder):
    """Karne JSON'larını karşılaştırmaya/girdi seçimine katma."""
    paths = []
    for path in Path(folder).glob('*.json'):
        try:
            data = json_oku(path)
            if isinstance(data, dict) and isinstance(data.get('sorular'), list) and isinstance(data.get('ozet'), dict):
                paths.append(path)
        except (OSError, ValueError):
            continue
    return sorted(paths, key=lambda p: (p.stat().st_mtime_ns, p.name))
