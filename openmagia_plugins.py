"""Manifest validation and registry for OpenMagia local plugins.

Plugins stay in their own folders.  OpenMagia records an absolute manifest
path plus user-approved permissions; it never copies or executes arbitrary
host code.  Plugin UI is served into a sandboxed browser iframe and talks to
the app through the allow-listed bridge implemented in app.js/server.py.
"""
from __future__ import annotations

import json
import re
import smtplib
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path


MANIFEST_NAME = "openmagia-plugin.json"
ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
KNOWN_PERMISSIONS = {
    "project.read", "project.write", "media.read", "media.write",
    "timeline.read", "timeline.write", "generation.read", "generation.create",
    "generation.events", "notifications.email", "notifications.imessage",
    "storage",
}


class PluginError(ValueError):
    pass


def _inside(root: Path, candidate: Path) -> bool:
    root, candidate = root.resolve(), candidate.resolve()
    return candidate == root or root in candidate.parents


def resolve_manifest(path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    if target.is_dir():
        target = target / MANIFEST_NAME
    if not target.is_file():
        raise PluginError(f"No {MANIFEST_NAME} found at that location.")
    return target


def load_manifest(path: str | Path) -> dict:
    manifest_path = resolve_manifest(path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Invalid plugin manifest: {exc}") from exc
    required = ("id", "name", "version", "description", "ui")
    missing = [key for key in required if not str(manifest.get(key) or "").strip()]
    if missing:
        raise PluginError("Missing manifest fields: " + ", ".join(missing))
    if int(manifest.get("manifestVersion", 1)) != 1:
        raise PluginError("Unsupported manifestVersion. OpenMagia currently supports version 1.")
    if not ID_RE.fullmatch(str(manifest["id"])):
        raise PluginError("Plugin id must be a lowercase reverse-domain style identifier.")
    if not VERSION_RE.fullmatch(str(manifest["version"])):
        raise PluginError("Plugin version must use semantic versioning, for example 1.0.0.")
    permissions = list(dict.fromkeys(manifest.get("permissions") or []))
    unknown = sorted(set(permissions) - KNOWN_PERMISSIONS)
    if unknown:
        raise PluginError("Unknown permissions: " + ", ".join(unknown))
    root = manifest_path.parent
    for field in ("ui", "icon", "cover"):
        rel = manifest.get(field)
        if not rel:
            continue
        asset = (root / str(rel)).resolve()
        if not _inside(root, asset) or not asset.is_file():
            raise PluginError(f"Manifest {field} must point to a file inside the plugin folder.")
    normalized = dict(manifest)
    normalized["permissions"] = permissions
    normalized["manifestPath"] = str(manifest_path)
    normalized["root"] = str(root)
    normalized.setdefault("author", {})
    normalized.setdefault("keywords", [])
    return normalized


class PluginRegistry:
    def __init__(self, registry_file: str | Path, log_file: str | Path):
        self.registry_file = Path(registry_file)
        self.log_file = Path(log_file)
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"plugins": []}
        except (OSError, json.JSONDecodeError):
            return {"plugins": []}

    def _write(self, data: dict) -> None:
        tmp = self.registry_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.registry_file)

    def list(self) -> list[dict]:
        data, result = self._read(), []
        for entry in data.get("plugins", []):
            try:
                manifest = load_manifest(entry["manifestPath"])
                manifest.update({
                    "enabled": bool(entry.get("enabled")),
                    "grants": list(entry.get("grants") or []),
                    "settings": dict(entry.get("settings") or {}),
                    "missing": False,
                })
            except PluginError as exc:
                manifest = {"id": entry.get("id"), "name": entry.get("name") or entry.get("id"),
                            "manifestPath": entry.get("manifestPath"), "enabled": False,
                            "grants": [], "settings": {}, "missing": True, "error": str(exc)}
            result.append(manifest)
        return result

    def install(self, path: str | Path) -> dict:
        manifest = load_manifest(path)
        data = self._read()
        entry = next((p for p in data.get("plugins", []) if p.get("id") == manifest["id"]), None)
        fresh = entry is None
        if fresh:
            entry = {"id": manifest["id"], "name": manifest["name"], "enabled": False,
                     "grants": [], "settings": {}}
            data.setdefault("plugins", []).append(entry)
        entry["manifestPath"] = manifest["manifestPath"]
        entry["name"] = manifest["name"]
        self._write(data)
        self.log(manifest["id"], "info", "Plugin loaded for development", {"path": manifest["manifestPath"]})
        manifest.update({"enabled": bool(entry.get("enabled")), "grants": entry.get("grants", []),
                         "settings": entry.get("settings", {}), "fresh": fresh})
        return manifest

    def update(self, plugin_id: str, *, enabled=None, grants=None, settings=None) -> dict:
        data = self._read()
        entry = next((p for p in data.get("plugins", []) if p.get("id") == plugin_id), None)
        if not entry:
            raise PluginError("Plugin is not loaded.")
        manifest = load_manifest(entry["manifestPath"])
        if grants is not None:
            grants = list(dict.fromkeys(grants))
            if set(grants) - set(manifest.get("permissions") or []):
                raise PluginError("Cannot grant permissions not requested by the manifest.")
            entry["grants"] = grants
        if enabled is not None:
            if enabled and set(manifest.get("permissions") or []) - set(entry.get("grants") or []):
                raise PluginError("Approve every requested permission before enabling this plugin.")
            entry["enabled"] = bool(enabled)
        if settings is not None:
            entry["settings"] = dict(settings)
        self._write(data)
        self.log(plugin_id, "info", "Plugin settings updated", {"enabled": entry.get("enabled", False)})
        return next(p for p in self.list() if p.get("id") == plugin_id)

    def remove(self, plugin_id: str) -> None:
        data = self._read()
        before = len(data.get("plugins", []))
        data["plugins"] = [p for p in data.get("plugins", []) if p.get("id") != plugin_id]
        if len(data["plugins"]) == before:
            raise PluginError("Plugin is not loaded.")
        self._write(data)
        self.log(plugin_id, "info", "Plugin removed from OpenMagia")

    def get(self, plugin_id: str) -> dict:
        plugin = next((p for p in self.list() if p.get("id") == plugin_id), None)
        if not plugin:
            raise PluginError("Plugin is not loaded.")
        return plugin

    def authorize(self, plugin_id: str, permission: str) -> dict:
        plugin = self.get(plugin_id)
        if not plugin.get("enabled"):
            raise PluginError("Plugin is disabled.")
        if permission not in plugin.get("grants", []):
            raise PluginError(f"Plugin does not have {permission} permission.")
        return plugin

    def asset(self, plugin_id: str, relative: str) -> Path:
        plugin = self.get(plugin_id)
        root = Path(plugin["root"]).resolve()
        target = (root / relative).resolve()
        if not _inside(root, target) or not target.is_file():
            raise PluginError("Plugin asset not found.")
        return target

    def log(self, plugin_id: str, level: str, message: str, detail=None) -> dict:
        record = {"time": time.time(), "pluginId": plugin_id, "level": level,
                  "message": str(message), "detail": detail or {}}
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return record

    def logs(self, plugin_id=None, limit=200) -> list[dict]:
        try:
            rows = [json.loads(line) for line in self.log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError):
            rows = []
        if plugin_id:
            rows = [row for row in rows if row.get("pluginId") == plugin_id]
        return rows[-max(1, min(int(limit), 1000)):]


def send_notification(channel: str, settings: dict, title: str, message: str) -> dict:
    """Send through an explicitly configured host channel.

    Empty targets are a dry run so example plugins can be tested safely.
    """
    if channel == "imessage":
        target = str(settings.get("imessageTarget") or "").strip()
        if not target:
            return {"ok": True, "dryRun": True, "channel": channel}
        if sys.platform != "darwin":
            raise PluginError("iMessage notifications are available only on macOS.")
        script = ('tell application "Messages"\n'
                  'set targetService to 1st service whose service type = iMessage\n'
                  'set targetBuddy to buddy ' + json.dumps(target) + ' of targetService\n'
                  'send ' + json.dumps(f"{title}\n{message}") + ' to targetBuddy\nend tell')
        run = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
        if run.returncode:
            raise PluginError(run.stderr.strip() or "Messages could not send the notification.")
        return {"ok": True, "channel": channel}
    if channel == "email":
        target = str(settings.get("emailTo") or "").strip()
        if not target:
            return {"ok": True, "dryRun": True, "channel": channel}
        host = str(settings.get("smtpHost") or "").strip()
        if not host:
            raise PluginError("Configure an SMTP host before sending email.")
        msg = EmailMessage(); msg["Subject"] = title; msg["To"] = target
        msg["From"] = str(settings.get("emailFrom") or settings.get("smtpUser") or target); msg.set_content(message)
        port = int(settings.get("smtpPort") or 587)
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if settings.get("smtpTls", True): smtp.starttls()
            if settings.get("smtpUser"): smtp.login(str(settings["smtpUser"]), str(settings.get("smtpPassword") or ""))
            smtp.send_message(msg)
        return {"ok": True, "channel": channel}
    raise PluginError("Unsupported notification channel.")
