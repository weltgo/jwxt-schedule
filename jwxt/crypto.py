"""正方等系统登录时用到的 AES / RSA 加密。"""

from __future__ import annotations

import base64


class CryptoUnavailableError(RuntimeError):
    pass


def aes_encrypt_password(password: str, salt: str) -> str:
    """复刻常见正方前端 CryptoJS 的 AES-CBC/Pkcs7 加密。

    前端常见写法：key=salt 的 UTF-8 字节，iv='1234567890123456'，
    加密结果 Base64 作为 mm 字段。
    """
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover
        raise CryptoUnavailableError("需要安装 cryptography：pip install cryptography") from exc

    key = salt.encode("utf-8")
    if len(key) not in (16, 24, 32):
        raise CryptoUnavailableError(f"不支持的 salt 长度：{len(key)}，请按学校前端 JS 调整加密方式")
    iv = b"1234567890123456"
    padder = padding.PKCS7(128).padder()
    data = padder.update(password.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(data) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def rsa_encrypt_password(password: str, modulus_b64: str, exponent_b64: str) -> str:
    """正方 login_getPublicKey 返回的 modulus/exponent 为 Base64。"""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:  # pragma: no cover
        raise CryptoUnavailableError("需要安装 cryptography：pip install cryptography") from exc

    modulus = int.from_bytes(base64.b64decode(modulus_b64), "big")
    exponent = int.from_bytes(base64.b64decode(exponent_b64), "big")
    public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
    ciphertext = public_key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ciphertext).decode("ascii")


__all__ = [
    "CryptoUnavailableError",
    "aes_encrypt_password",
    "rsa_encrypt_password",
]
