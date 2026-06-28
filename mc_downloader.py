"""
mc_downloader.py
Загрузка vanilla-клиента Minecraft напрямую из официального Mojang API.
Никаких сторонних серверов — только launchermeta.mojang.com / piston-meta.mojang.com.
"""

import os
import json
import hashlib
import zipfile
import platform
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def _download_file(url: str, dest_path: str, expected_sha1: str = None) -> bool:
    """Скачивает файл по url в dest_path. Если файл уже существует и хэш совпадает — пропускает."""
    if os.path.exists(dest_path) and expected_sha1:
        if _sha1_of_file(dest_path) == expected_sha1:
            return True
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest_path)
        return True
    except Exception as e:
        print(f"[downloader] Ошибка загрузки {url}: {e}")
        return False


def _sha1_of_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_version_info(version_id: str) -> dict:
    """Возвращает json-манифест конкретной версии (например 1.16.5) из Mojang API."""
    with urllib.request.urlopen(VERSION_MANIFEST_URL) as resp:
        manifest = json.loads(resp.read().decode("utf-8"))

    version_entry = next((v for v in manifest["versions"] if v["id"] == version_id), None)
    if not version_entry:
        raise ValueError(f"Версия {version_id} не найдена в Mojang manifest")

    with urllib.request.urlopen(version_entry["url"]) as resp:
        return json.loads(resp.read().decode("utf-8"))


def current_os_name() -> str:
    """Mojang использует 'windows' / 'linux' / 'osx' для правил библиотек."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "osx"
    return "linux"


def _rule_allows(rules: list) -> bool:
    """Проверяет os-rules библиотеки Mojang (allow/disallow по платформе)."""
    if not rules:
        return True
    os_name = current_os_name()
    allowed = False
    for rule in rules:
        action = rule.get("action", "allow")
        os_rule = rule.get("os")
        matches = True
        if os_rule and "name" in os_rule:
            matches = os_rule["name"] == os_name
        if matches:
            allowed = (action == "allow")
    return allowed


def download_vanilla(version_id: str, mc_root: str, progress_cb=None):
    """
    Скачивает client.jar, библиотеки, ассеты и natives для указанной vanilla-версии.
    mc_root — корневая папка .minecraft (или версии).
    progress_cb(stage: str, current: int, total: int) — необязательный callback для UI.
    """
    def report(stage, cur, tot):
        if progress_cb:
            progress_cb(stage, cur, tot)

    report("Получение манифеста версии...", 0, 1)
    version_json = get_version_info(version_id)

    versions_dir = os.path.join(mc_root, "versions", version_id)
    os.makedirs(versions_dir, exist_ok=True)

    # Сохраняем version json (нужен лаунчеру для составления classpath и аргументов запуска)
    with open(os.path.join(versions_dir, f"{version_id}.json"), "w", encoding="utf-8") as f:
        json.dump(version_json, f)

    # 1. client.jar
    client_info = version_json["downloads"]["client"]
    client_jar_path = os.path.join(versions_dir, f"{version_id}.jar")
    report("Загрузка client.jar...", 0, 1)
    _download_file(client_info["url"], client_jar_path, client_info.get("sha1"))

    # 2. Библиотеки (+ natives)
    libraries = version_json.get("libraries", [])
    natives_dir = os.path.join(versions_dir, f"{version_id}-natives")
    os.makedirs(natives_dir, exist_ok=True)

    lib_tasks = []
    for lib in libraries:
        rules = lib.get("rules")
        if not _rule_allows(rules):
            continue
        downloads = lib.get("downloads", {})

        artifact = downloads.get("artifact")
        if artifact:
            lib_path = os.path.join(mc_root, "libraries", artifact["path"])
            lib_tasks.append(("lib", artifact["url"], lib_path, artifact.get("sha1")))

        classifiers = downloads.get("classifiers", {})
        os_name = current_os_name()
        arch = "64" if platform.machine().endswith("64") else "32"
        possible_keys = [f"natives-{os_name}", f"natives-{os_name}-{arch}"]
        for key in possible_keys:
            if key in classifiers:
                native_info = classifiers[key]
                native_zip_path = os.path.join(mc_root, "libraries", native_info["path"])
                lib_tasks.append(("native", native_info["url"], native_zip_path, native_info.get("sha1")))

    report("Загрузка библиотек...", 0, len(lib_tasks))
    done = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_download_file, url, path, sha1): (kind, path)
            for kind, url, path, sha1 in lib_tasks
        }
        for future in as_completed(futures):
            kind, path = futures[future]
            done += 1
            report("Загрузка библиотек...", done, len(lib_tasks))
            if kind == "native" and future.result():
                try:
                    with zipfile.ZipFile(path, "r") as zf:
                        for member in zf.namelist():
                            if member.startswith("META-INF") or member.endswith("/"):
                                continue
                            zf.extract(member, natives_dir)
                except zipfile.BadZipFile:
                    pass

    # 3. Ассеты (звуки, текстуры лоадера и т.п.)
    asset_index = version_json.get("assetIndex")
    if asset_index:
        report("Загрузка индекса ассетов...", 0, 1)
        assets_dir = os.path.join(mc_root, "assets")
        indexes_dir = os.path.join(assets_dir, "indexes")
        os.makedirs(indexes_dir, exist_ok=True)
        index_path = os.path.join(indexes_dir, f"{asset_index['id']}.json")
        _download_file(asset_index["url"], index_path, asset_index.get("sha1"))

        with open(index_path, "r", encoding="utf-8") as f:
            asset_data = json.load(f)

        objects = asset_data.get("objects", {})
        objects_dir = os.path.join(assets_dir, "objects")
        asset_tasks = []
        for name, info in objects.items():
            obj_hash = info["hash"]
            sub_dir = obj_hash[:2]
            dest = os.path.join(objects_dir, sub_dir, obj_hash)
            url = f"https://resources.download.minecraft.net/{sub_dir}/{obj_hash}"
            asset_tasks.append((url, dest, obj_hash))

        report("Загрузка ассетов (это может занять время)...", 0, len(asset_tasks))
        done = 0
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [executor.submit(_download_file, url, dest, sha1) for url, dest, sha1 in asset_tasks]
            for future in as_completed(futures):
                done += 1
                if done % 25 == 0 or done == len(asset_tasks):
                    report("Загрузка ассетов (это может занять время)...", done, len(asset_tasks))

    report("Vanilla-клиент готов", 1, 1)
    return version_json


def is_vanilla_installed(mc_root: str, version_id: str) -> bool:
    jar_path = os.path.join(mc_root, "versions", version_id, f"{version_id}.jar")
    json_path = os.path.join(mc_root, "versions", version_id, f"{version_id}.json")
    return os.path.exists(jar_path) and os.path.exists(json_path)
