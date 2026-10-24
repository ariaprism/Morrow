"""Encrypted manual backups stored as assets on a private GitHub Release."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


API_ROOT = "https://api.github.com"
RELEASE_TAG = "nook-cloud-backups"
MAGIC = b"MORROW-CLOUD-BACKUP\x01"
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


class CloudBackupError(RuntimeError):
    pass


def config():
    return {
        "repo": (os.environ.get("GITHUB_BACKUP_REPO") or "").strip().strip("/"),
        "token": (os.environ.get("GITHUB_BACKUP_TOKEN") or "").strip(),
        "passphrase": os.environ.get("CLOUD_BACKUP_PASSPHRASE") or "",
        "auto_allowed": (os.environ.get("GITHUB_BACKUP_AUTO_ALLOWED") or "").strip().lower()
        in {"1", "true", "yes", "on"},
    }


def configured():
    cfg = config()
    return bool(
        cfg["repo"].count("/") == 1
        and cfg["token"]
        and len(cfg["passphrase"]) >= 16
    )


def _headers(binary=False):
    cfg = config()
    if not configured():
        raise CloudBackupError("云端备份尚未配置完成。")
    return {
        "Accept": "application/octet-stream" if binary else "application/vnd.github+json",
        "Authorization": "Bearer " + cfg["token"],
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/octet-stream" if binary else "application/json",
    }


def _key(passphrase, salt):
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        passphrase.encode("utf-8")
    )


def encrypt_file(source, destination, passphrase):
    salt, nonce = os.urandom(SALT_SIZE), os.urandom(NONCE_SIZE)
    source_size = Path(source).stat().st_size
    header = MAGIC + salt + nonce + struct.pack(">Q", source_size)
    encryptor = Cipher(algorithms.AES(_key(passphrase, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    digest = hashlib.sha256()
    with open(source, "rb") as src, open(destination, "wb") as dst:
        dst.write(header)
        digest.update(header)
        while chunk := src.read(CHUNK_SIZE):
            encrypted = encryptor.update(chunk)
            dst.write(encrypted)
            digest.update(encrypted)
        final = encryptor.finalize()
        dst.write(final)
        digest.update(final)
        dst.write(encryptor.tag)
        digest.update(encryptor.tag)
    return digest.hexdigest()


def decrypt_file(source, destination, passphrase):
    total = Path(source).stat().st_size
    header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE + 8
    if total < header_size + TAG_SIZE:
        raise CloudBackupError("云端备份文件不完整。")
    with open(source, "rb") as src:
        header = src.read(header_size)
        if not header.startswith(MAGIC):
            raise CloudBackupError("这不是Morrow云端备份。")
        offset = len(MAGIC)
        salt = header[offset:offset + SALT_SIZE]
        offset += SALT_SIZE
        nonce = header[offset:offset + NONCE_SIZE]
        offset += NONCE_SIZE
        expected_size = struct.unpack(">Q", header[offset:offset + 8])[0]
        src.seek(total - TAG_SIZE)
        tag = src.read(TAG_SIZE)
        src.seek(header_size)
        remaining = total - header_size - TAG_SIZE
        decryptor = Cipher(algorithms.AES(_key(passphrase, salt)), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        try:
            with open(destination, "wb") as dst:
                while remaining:
                    chunk = src.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise CloudBackupError("云端备份文件不完整。")
                    remaining -= len(chunk)
                    dst.write(decryptor.update(chunk))
                dst.write(decryptor.finalize())
        except Exception as exc:
            Path(destination).unlink(missing_ok=True)
            if isinstance(exc, CloudBackupError):
                raise
            raise CloudBackupError("云端备份无法解密，请检查备份口令。") from exc
    if Path(destination).stat().st_size != expected_size:
        Path(destination).unlink(missing_ok=True)
        raise CloudBackupError("云端备份解密后的大小校验失败。")


def _request(method, path, **kwargs):
    try:
        response = requests.request(
            method, API_ROOT + path, headers=_headers(), timeout=(10, 90), **kwargs
        )
    except requests.RequestException as exc:
        raise CloudBackupError(f"连接 GitHub 失败：{exc}") from exc
    return response


def repository_status():
    cfg = config()
    response = _request("GET", "/repos/" + cfg["repo"])
    if not response.ok:
        raise CloudBackupError(f"GitHub 仓库检查失败（{response.status_code}）。")
    repo = response.json()
    if not repo.get("private"):
        raise CloudBackupError("云端备份仓库必须是私有仓库。")
    return {"repo": cfg["repo"], "private": True}


def _release(create=False):
    cfg = config()
    response = _request("GET", f"/repos/{cfg['repo']}/releases/tags/{RELEASE_TAG}")
    if response.status_code == 404 and create:
        response = _request(
            "POST", f"/repos/{cfg['repo']}/releases",
            json={
                "tag_name": RELEASE_TAG,
                "name": "Morrow 云端备份",
                "body": "Morrow服务器生成的加密云端备份。",
                "draft": False,
                "prerelease": False,
            },
        )
    elif response.status_code == 404:
        return {"assets": []}
    if not response.ok:
        raise CloudBackupError(f"读取 GitHub Release 失败（{response.status_code}）。")
    return response.json()


def list_backups():
    release = _release(create=False)
    assets = [
        {
            "id": item.get("id"), "name": item.get("name") or "",
            "size": int(item.get("size") or 0), "created_at": item.get("created_at") or "",
            "state": item.get("state") or "",
        }
        for item in release.get("assets") or []
        if str(item.get("name") or "").endswith(".nook") and item.get("state") == "uploaded"
    ]
    return sorted(assets, key=lambda item: (item["created_at"], item["id"] or 0), reverse=True)


def upload_backup(path, filename):
    release = _release(create=True)
    upload_url = str(release.get("upload_url") or "").split("{")[0]
    if not upload_url:
        raise CloudBackupError("GitHub 没有返回可用的上传地址。")
    try:
        with open(path, "rb") as source:
            headers = _headers()
            headers["Content-Type"] = "application/octet-stream"
            response = requests.post(
                upload_url, headers=headers, params={"name": filename},
                data=source, timeout=(15, 600),
            )
    except requests.RequestException as exc:
        raise CloudBackupError(f"上传 GitHub 失败：{exc}") from exc
    if not response.ok:
        raise CloudBackupError(f"上传 GitHub 失败（{response.status_code}）：{response.text[:200]}")
    item = response.json()
    return {"id": item.get("id"), "name": item.get("name"), "size": item.get("size")}


def download_backup(asset_id, destination):
    cfg = config()
    try:
        response = requests.get(
            f"{API_ROOT}/repos/{cfg['repo']}/releases/assets/{int(asset_id)}",
            headers=_headers(binary=True), stream=True, allow_redirects=True, timeout=(15, 600),
        )
    except requests.RequestException as exc:
        raise CloudBackupError(f"下载 GitHub 备份失败：{exc}") from exc
    if not response.ok:
        raise CloudBackupError(f"下载 GitHub 备份失败（{response.status_code}）。")
    with open(destination, "wb") as target:
        for chunk in response.iter_content(CHUNK_SIZE):
            if chunk:
                target.write(chunk)
