"""Credential references are persisted; literal keys stay in the OS keyring or env."""

import os

import keyring


class Credentials:
    service = "agent-workbench-local-v2"

    def save(self, id, secret):
        if secret:
            try:
                keyring.set_password(self.service, id, secret)
            except Exception:
                raise ValueError(
                    "系统凭据存储不可用，请改用 .env 环境变量引用；密钥未写入数据库"
                ) from None

    def get(self, member):
        if member.get("key_env"):
            return os.getenv(member["key_env"], "")
        try:
            return keyring.get_password(self.service, member["id"]) or ""
        except Exception:
            return ""

    def public(self, member):
        return member | {"key_configured": bool(self.get(member))}
