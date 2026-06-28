"""
modloader_installer.py
Установка Forge, Fabric и OptiFine поверх vanilla 1.16.5.

ВАЖНО (читай это, не только код):
- Fabric устанавливается полностью автоматически через официальный Fabric Meta API
  (meta.fabricmc.net) — никаких файлов скачивать руками не нужно.
- Forge для 1.16.5 не отдаёт готовый "json профиля" через простой API так же легко,
  как Fabric. Официальный способ — скачать forge-installer.jar и запустить его
  с флагом --installClient. Для этого нужен установленный в системе Java
  (мы вызываем java -jar forge-installer.jar --installClient <путь_до_.minecraft>).
  Поэтому Forge активируется через установщик, который скачивается с files.minecraftforge.net.
- OptiFine НЕ распространяется через открытый API с прямыми ссылками на скачивание —
  официальный сайt optifine.net требует прохождения через рекламную страницу/captcha
  и не даёт стабильного прямого URL для автоматического скачивания.
  Поэтому для OptiFine реализован тот же принцип, что и для Forge: пользователь сам
  кладёт скачанный OptiFine-installer.jar (с optifine.net) в папку, а лаунчер запускает
  его автоматической установкой через консоль (java -jar OptiFine_installer.jar).
  Это сделано НЕ потому что "забыли", а потому что автоматическая загрузка с optifine.net
  технически нарушает их защиту от прямых ссылок и может сломаться в любой момент.
"""

import os
import json
import subprocess
import urllib.request

FABRIC_META_BASE = "https://meta.fabricmc.net/v2"
FORGE_FILES_BASE = "https://files.minecraftforge.net/net/minecraftforge/forge"
FORGE_MAVEN_BASE = "https://maven.minecraftforge.net/net/minecraftforge/forge"


# --------------------------------------------------------------------------------------
# FABRIC — полностью автоматическая установка
# --------------------------------------------------------------------------------------

def get_fabric_loader_versions():
    """Список доступных версий Fabric Loader (берём самую новую стабильную по умолчанию)."""
    url = f"{FABRIC_META_BASE}/versions/loader"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data


def install_fabric(mc_root: str, mc_version: str = "1.16.5", loader_version: str = None, progress_cb=None):
    """
    Скачивает json профиля Fabric для mc_version и нужные библиотеки.
    После этого в versions/ появляется папка fabric-loader-<loader>-<mc_version>,
    которую лаунчер сможет выбрать для запуска как обычную версию.
    """
    def report(msg):
        if progress_cb:
            progress_cb(msg, 0, 1)

    if loader_version is None:
        loaders = get_fabric_loader_versions()
        loader_version = loaders[0]["version"]  # первый = самый новый

    report(f"Получение профиля Fabric {loader_version}...")
    profile_url = f"{FABRIC_META_BASE}/versions/loader/{mc_version}/{loader_version}/profile/json"
    with urllib.request.urlopen(profile_url) as resp:
        profile_json = json.loads(resp.read().decode("utf-8"))

    version_id = profile_json["id"]  # например fabric-loader-0.15.x-1.16.5
    version_dir = os.path.join(mc_root, "versions", version_id)
    os.makedirs(version_dir, exist_ok=True)

    with open(os.path.join(version_dir, f"{version_id}.json"), "w", encoding="utf-8") as f:
        json.dump(profile_json, f)

    # Скачиваем библиотеки fabric loader-а (intermediary + loader + и т.д.)
    report("Загрузка библиотек Fabric...")
    for lib in profile_json.get("libraries", []):
        maven_url = lib["url"].rstrip("/")
        name = lib["name"]  # group:artifact:version
        group, artifact, version = name.split(":")
        path_part = group.replace(".", "/") + f"/{artifact}/{version}/{artifact}-{version}.jar"
        full_url = f"{maven_url}/{path_part}"
        dest = os.path.join(mc_root, "libraries", path_part)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                urllib.request.urlretrieve(full_url, dest)
            except Exception as e:
                print(f"[fabric] не удалось скачать {full_url}: {e}")

    report("Fabric установлен")
    return version_id


# --------------------------------------------------------------------------------------
# FORGE — скачивание официального инсталлятора + запуск через Java
# --------------------------------------------------------------------------------------

def get_recommended_forge_build(mc_version: str = "1.16.5") -> str:
    """
    Возвращает строку версии Forge вида '1.16.5-36.2.39' (рекомендованная сборка).
    Берём из promotions_slim.json — официальный публичный файл Forge с рекомендованными версиями.
    """
    url = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    promos = data.get("promos", {})
    key_recommended = f"{mc_version}-recommended"
    key_latest = f"{mc_version}-latest"
    build = promos.get(key_recommended) or promos.get(key_latest)
    if not build:
        raise ValueError(f"Не найдена сборка Forge для {mc_version}")
    return f"{mc_version}-{build}"


def download_forge_installer(mc_version: str, dest_dir: str, progress_cb=None) -> str:
    """Скачивает forge-<version>-installer.jar и возвращает путь к нему."""
    full_version = get_recommended_forge_build(mc_version)
    installer_name = f"forge-{full_version}-installer.jar"
    url = f"{FORGE_MAVEN_BASE}/{full_version}/{installer_name}"
    dest_path = os.path.join(dest_dir, installer_name)

    if progress_cb:
        progress_cb(f"Загрузка Forge installer ({full_version})...", 0, 1)

    os.makedirs(dest_dir, exist_ok=True)
    urllib.request.urlretrieve(url, dest_path)
    return dest_path


def run_forge_installer(installer_path: str, mc_root: str, java_path: str = "java") -> tuple:
    """
    Запускает forge-installer.jar в режиме --installClient <mc_root>.
    Это официальный, единственный поддерживаемый способ автоматической установки Forge.
    Возвращает (success: bool, log: str).
    """
    cmd = [java_path, "-jar", installer_path, "--installClient", mc_root]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, cwd=mc_root
        )
        success = result.returncode == 0
        log = result.stdout + "\n" + result.stderr
        return success, log
    except FileNotFoundError:
        return False, "Java не найдена. Установи Java (см. README) и повтори."
    except subprocess.TimeoutExpired:
        return False, "Установка Forge заняла слишком много времени (timeout)."


def install_forge(mc_root: str, mc_version: str = "1.16.5", java_path: str = "java", progress_cb=None):
    """Полный цикл: скачать установщик Forge -> запустить его -> вернуть найденный version_id."""
    tmp_dir = os.path.join(mc_root, "_forge_tmp")
    installer_path = download_forge_installer(mc_version, tmp_dir, progress_cb)

    if progress_cb:
        progress_cb("Установка Forge (запуск installer.jar)...", 0, 1)

    success, log = run_forge_installer(installer_path, mc_root, java_path)
    if not success:
        raise RuntimeError(f"Установка Forge не удалась:\n{log}")

    # После установки Forge сам создаёт versions/<mc_version>-forge-<build>/
    versions_dir = os.path.join(mc_root, "versions")
    forge_version_id = None
    if os.path.exists(versions_dir):
        for name in os.listdir(versions_dir):
            if "forge" in name.lower() and mc_version in name:
                forge_version_id = name

    if progress_cb:
        progress_cb("Forge установлен", 1, 1)

    return forge_version_id


# --------------------------------------------------------------------------------------
# OPTIFINE — пользователь скачивает installer.jar сам, лаунчер только запускает его
# --------------------------------------------------------------------------------------

def run_optifine_installer(installer_path: str, mc_root: str, java_path: str = "java") -> tuple:
    """
    OptiFine installer.jar при запуске без аргументов открывает СВОЁ собственное окно
    с кнопкой Install — это поведение самого OptiFine, обойти его нельзя без нарушения
    их защиты. Поэтому мы просто запускаем installer.jar, а пользователь нажимает Install
    в открывшемся окошке (один раз на каждую новую версию OptiFine).
    """
    cmd = [java_path, "-jar", installer_path]
    try:
        subprocess.Popen(cmd, cwd=mc_root)
        return True, "Окно установки OptiFine открыто. Нажми Install в нём."
    except FileNotFoundError:
        return False, "Java не найдена. Установи Java и повтори."


def find_optifine_version(mc_root: str, mc_version: str = "1.16.5") -> str:
    """После установки OptiFine появляется как versions/<mc_version>-OptiFine_HD_...­/"""
    versions_dir = os.path.join(mc_root, "versions")
    if not os.path.exists(versions_dir):
        return None
    for name in os.listdir(versions_dir):
        if "optifine" in name.lower() and mc_version in name:
            return name
    return None
