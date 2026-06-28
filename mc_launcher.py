"""
mc_launcher.py
Сборка classpath, аргументов запуска и непосредственный старт Minecraft-процесса.
Работает с любым version_id, у которого есть versions/<id>/<id>.json
(подходит и для vanilla, и для Forge, и для Fabric — формат json у всех общий,
так как все они следуют схеме Mojang launcher profile).
"""

import os
import json
import uuid
import subprocess
import platform


def _merge_with_inherits(mc_root: str, version_id: str) -> dict:
    """
    Некоторые версии (Forge/Fabric) указывают inheritsFrom: '1.16.5' —
    значит нужно взять их json + добавить (merge) поля родительской vanilla-версии.
    """
    version_json_path = os.path.join(mc_root, "versions", version_id, f"{version_id}.json")
    with open(version_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    parent_id = data.get("inheritsFrom")
    if not parent_id:
        return data

    parent_path = os.path.join(mc_root, "versions", parent_id, f"{parent_id}.json")
    with open(parent_path, "r", encoding="utf-8") as f:
        parent_data = json.load(f)

    merged = dict(parent_data)
    merged["libraries"] = parent_data.get("libraries", []) + data.get("libraries", [])

    # mainClass, arguments переопределяются дочерней версией, если есть
    for key in ("mainClass", "arguments", "minecraftArguments"):
        if key in data:
            merged[key] = data[key]

    # jar (имя главного client.jar) обычно остаётся от родителя
    merged["jar"] = data.get("jar", parent_data.get("jar", parent_id))
    merged["id"] = data.get("id", version_id)
    return merged


def _rule_allows(rules: list) -> bool:
    if not rules:
        return True
    system = platform.system().lower()
    os_name = "windows" if system == "windows" else ("osx" if system == "darwin" else "linux")
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


def build_classpath(mc_root: str, version_id: str, version_data: dict) -> list:
    """Собирает список путей ко всем .jar библиотекам + сам client jar."""
    jars = []
    for lib in version_data.get("libraries", []):
        rules = lib.get("rules")
        if not _rule_allows(rules):
            continue
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        if artifact and artifact.get("path"):
            jar_path = os.path.join(mc_root, "libraries", artifact["path"])
            if os.path.exists(jar_path):
                jars.append(jar_path)
        elif "name" in lib:
            # Forge-style библиотеки без downloads.artifact (указан только maven name)
            name = lib["name"]
            try:
                group, artifact_name, version = name.split(":")[:3]
                path_part = group.replace(".", "/") + f"/{artifact_name}/{version}/{artifact_name}-{version}.jar"
                jar_path = os.path.join(mc_root, "libraries", path_part)
                if os.path.exists(jar_path):
                    jars.append(jar_path)
            except ValueError:
                pass

    main_jar_name = version_data.get("jar", version_id)
    main_jar = os.path.join(mc_root, "versions", main_jar_name, f"{main_jar_name}.jar")
    if os.path.exists(main_jar):
        jars.append(main_jar)

    return jars


def launch_minecraft(
    mc_root: str,
    version_id: str,
    username: str,
    ram_mb: int = 4096,
    java_path: str = "java",
    window_width: int = 925,
    window_height: int = 530,
):
    """
    Формирует команду запуска и стартует процесс Minecraft.
    Возвращает Popen-объект процесса (можно отслеживать живой/умер).
    """
    version_data = _merge_with_inherits(mc_root, version_id)

    natives_dir = os.path.join(mc_root, "versions", version_id, f"{version_id}-natives")
    if not os.path.exists(natives_dir):
        # для forge/fabric natives обычно лежат у родительской vanilla версии
        parent_id = version_data.get("id")
        # fallback: ищем любую *-natives папку, относящуюся к 1.16.5
        candidate = os.path.join(mc_root, "versions", "1.16.5", "1.16.5-natives")
        if os.path.exists(candidate):
            natives_dir = candidate

    classpath = build_classpath(mc_root, version_id, version_data)
    classpath_str = os.pathsep.join(classpath)

    fake_uuid = uuid.uuid4().hex
    main_class = version_data.get("mainClass", "net.minecraft.client.main.Main")

    jvm_args = [
        java_path,
        f"-Xmx{ram_mb}M",
        f"-Xms{min(ram_mb, 1024)}M",
        f"-Djava.library.path={natives_dir}",
        "-cp", classpath_str,
        main_class,
    ]

    game_args = [
        "--username", username,
        "--version", version_id,
        "--gameDir", mc_root,
        "--assetsDir", os.path.join(mc_root, "assets"),
        "--assetIndex", version_data.get("assetIndex", {}).get("id", "1.16"),
        "--uuid", fake_uuid,
        "--accessToken", "0",
        "--userType", "legacy",
        "--width", str(window_width),
        "--height", str(window_height),
    ]

    full_cmd = jvm_args + game_args

    process = subprocess.Popen(
        full_cmd,
        cwd=mc_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process


def list_installed_versions(mc_root: str) -> list:
    """Возвращает список version_id, у которых есть json профиль (vanilla/forge/fabric)."""
    versions_dir = os.path.join(mc_root, "versions")
    if not os.path.exists(versions_dir):
        return []
    result = []
    for name in os.listdir(versions_dir):
        json_path = os.path.join(versions_dir, name, f"{name}.json")
        if os.path.exists(json_path):
            result.append(name)
    return result
