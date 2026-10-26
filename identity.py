"""
identity.py - 去中心化身份 (did:key) 与密码学签名模块

遵循 Technocore 官方规范:
1. 生成/加载 Ed25519 密钥对并持久化至本地 JSON 文件。
2. 将 Ed25519 公钥添加 multicodec 前缀 (0xed01) 并通过 Base58btc 编码为 did:key (以 z 开头，如 did:key:z6Mk...)。
3. 提供 single-line sweep 文本清洗与 86 位 unpadded base64url 签名能力。
4. 提供基于 SHA-256 的 DID 分片路径 (did-{shard}/{key}) 算法。
"""

import os
import re
import json
import time
import base64
import hashlib
import unicodedata
from pathlib import Path
from typing import Tuple, Optional

# Base58btc Alphabet (Bitcoin standard)
B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    """Base58btc 编码算法"""
    n = int.from_bytes(raw, "big")
    res = bytearray()
    while n > 0:
        n, rem = divmod(n, 58)
        res.append(B58_ALPHABET[rem])
    # 处理前导 0 字节
    for byte in raw:
        if byte == 0:
            res.append(B58_ALPHABET[0])
        else:
            break
    return res[::-1].decode("ascii")


def b58decode(s: str) -> bytes:
    """Base58btc 解码算法"""
    n = 0
    for char in s.encode("ascii"):
        idx = B58_ALPHABET.find(char)
        if idx == -1:
            raise ValueError(f"Invalid base58 character: {chr(char)}")
        n = n * 58 + idx
    # 转为字节
    res = bytearray()
    while n > 0:
        n, rem = divmod(n, 256)
        res.append(rem)
    res = res[::-1]
    # 处理前导 1
    pad = 0
    for char in s:
        if char == "1":
            pad += 1
        else:
            break
    return bytes(pad) + bytes(res)


# ============================================================================
# RFC 8032 纯 Python Ed25519 实现 (作为无 C 依赖时的原生后备引擎)
# ============================================================================
_b = 256
_q = 2**255 - 19
_l = 2**252 + 27742317777372353535851937790883648493


def _H(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()


def _inv(x: int) -> int:
    return pow(x, _q - 2, _q)


_d = -121665 * _inv(121666) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = 4 * _inv(5) % _q
_Bx = _xrecover(_By)
_B = (_Bx % _q, _By % _q)


def _edwards(P: Tuple[int, int], Q: Tuple[int, int]) -> Tuple[int, int]:
    x1, y1 = P
    x2, y2 = Q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _d * x1 * x2 * y1 * y2) % _q
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _d * x1 * x2 * y1 * y2) % _q
    return (x3, y3)


def _scalarmult(P: Tuple[int, int], e: int) -> Tuple[int, int]:
    if e == 0:
        return (0, 1)
    Q = _scalarmult(P, e // 2)
    Q = _edwards(Q, Q)
    if e & 1:
        Q = _edwards(Q, P)
    return Q


def _encodeint(y: int) -> bytes:
    return y.to_bytes(32, "little")


def _encodepoint(P: Tuple[int, int]) -> bytes:
    x, y = P
    b = bytearray(_encodeint(y))
    if x & 1:
        b[31] |= 0x80
    return bytes(b)


def _publickey_pure(sk: bytes) -> bytes:
    h = _H(sk)
    a = 2**(_b - 2) + sum(2**i * ((h[i // 8] >> (i % 8)) & 1) for i in range(3, _b - 2))
    return _encodepoint(_scalarmult(_B, a))


def _signature_pure(m: bytes, sk: bytes, pk: bytes) -> bytes:
    h = _H(sk)
    a = 2**(_b - 2) + sum(2**i * ((h[i // 8] >> (i % 8)) & 1) for i in range(3, _b - 2))
    r = int.from_bytes(_H(h[32:] + m), "little") % _l
    R = _scalarmult(_B, r)
    k = int.from_bytes(_H(_encodepoint(R) + pk + m), "little") % _l
    S = (r + k * a) % _l
    return _encodepoint(R) + _encodeint(S)


# ============================================================================
# Cryptography 模块兼容判断
# ============================================================================
HAS_CRYPTOGRAPHY = False
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


def clean_single_line(text: str) -> str:
    """
    Technocore 要求的 Single-Line Sweep 清洗:
    将所有 C0/C1 控制字符 (换行、回车、Tab 等)、不可见字符、格式化字符替换为空格。
    """
    if not text:
        return ""
    res = []
    for ch in text:
        cat = unicodedata.category(ch)
        # C 开头代表控制字符 (Cc, Cf, Cs, Co, Cn)
        if cat.startswith("C") or ch in "\r\n\t\x00":
            res.append(" ")
        else:
            res.append(ch)
    # 限制单条消息最大 4096 字符
    return "".join(res)[:4096]


class AgentIdentity:
    """Agent 去中心化身份管理类 (did:key)"""

    def __init__(self, name: str, key_dir: str = "agent_keys"):
        self.name = name
        self.key_dir = Path(key_dir)
        self.key_file = self.key_dir / f"{self.name}.json"
        self.nonce_file = self.key_dir / f"{self.name}.nonce"
        
        self.private_key_bytes: bytes = b""
        self.public_key_bytes: bytes = b""
        self.did: str = ""
        self._last_nonce: int = 0
        
        self._load_or_generate_key()
        self._load_persistent_nonce()

    def _load_persistent_nonce(self):
        """从本地文件恢复上次持久化的最新 Nonce，防止进程重启后 Nonce 回退冲突"""
        now_ms = int(time.time() * 1000)
        saved_nonce = 0
        if self.nonce_file.exists():
            try:
                content = self.nonce_file.read_text(encoding="utf-8").strip()
                if content.isdigit():
                    saved_nonce = int(content)
            except Exception:
                pass
        self._last_nonce = max(now_ms, saved_nonce)

    @property
    def mailbox_name(self) -> str:
        """
        生成专属 Mailbox 房间名称 (符合 Technocore Pattern 2 & 3 规范: mb-p-<unguessable>)
        使用 mb-p- 前缀保证既是签名邮箱又不对外公开枚举
        """
        fingerprint = hashlib.sha256(self.did.encode("utf-8")).hexdigest()[:12]
        safe_name = re.sub(r"[^a-z0-9_-]", "", self.name.lower())[:10]
        return f"mb-p-{safe_name}-{fingerprint}"

    def _load_or_generate_key(self):
        """加载已存在的密钥，或自动生成并持久化新密钥"""
        self.key_dir.mkdir(parents=True, exist_ok=True)
        
        if self.key_file.exists():
            try:
                with open(self.key_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.private_key_bytes = bytes.fromhex(data["private_key_hex"])
                self.public_key_bytes = bytes.fromhex(data["public_key_hex"])
                self.did = data.get("did") or self._derive_did(self.public_key_bytes)
                return
            except Exception as e:
                print(f"[{self.name}] ⚠️ 读取现有密钥文件失败 ({e})，正在生成新密钥...")

        # 生成新的 32 字节私钥
        self.private_key_bytes = os.urandom(32)
        if HAS_CRYPTOGRAPHY:
            priv = ed25519.Ed25519PrivateKey.from_private_bytes(self.private_key_bytes)
            pub = priv.public_key()
            self.public_key_bytes = pub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw
            )
        else:
            self.public_key_bytes = _publickey_pure(self.private_key_bytes)

        self.did = self._derive_did(self.public_key_bytes)

        # 持久化到本地 JSON 文件
        payload = {
            "name": self.name,
            "did": self.did,
            "public_key_hex": self.public_key_bytes.hex(),
            "private_key_hex": self.private_key_bytes.hex(),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(self.key_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"[{self.name}] 🔑 已生成并持久化专属密钥: {self.key_file}")
        print(f"[{self.name}] 🆔 身份 DID: {self.did}")

    @staticmethod
    def _derive_did(public_key_bytes: bytes) -> str:
        """
        根据 W3C did:key 与 Multicodec 规范生成 did:key
        Ed25519-pub multicodec 前缀: 0xed, 0x01
        Base58btc 编码后加 'z' 前缀，生成 'did:key:z6Mk...'
        """
        multicodec_prefix = b"\xed\x01"
        raw_did = multicodec_prefix + public_key_bytes
        return "did:key:z" + b58encode(raw_did)

    def get_sharded_did_path(self) -> Tuple[str, str]:
        """
        计算 DID Note 分片存储路径 (符合 Technocore Pattern 3 规范):
        Fingerprint = SHA256(did)[:16]
        shard = fingerprint[:2]
        key = fingerprint[2:16]
        返回: (shard, key)，用于写入 /kv/did-{shard}/{key}
        """
        fingerprint = hashlib.sha256(self.did.encode("utf-8")).hexdigest()[:16]
        shard = fingerprint[:2]
        key = fingerprint[2:16]
        return shard, key

    def _save_persistent_nonce(self):
        """持久化当前 Nonce 到磁盘文件"""
        try:
            self.nonce_file.write_text(str(self._last_nonce), encoding="utf-8")
        except Exception:
            pass

    def set_min_nonce(self, min_nonce: int):
        """
        强制推进 Nonce 下限 (通常在服务端报告 Nonce 碰撞时由外部自愈逻辑调用)
        """
        if min_nonce > self._last_nonce:
            self._last_nonce = min_nonce
            self._save_persistent_nonce()

    def get_next_nonce(self) -> int:
        """
        生成严格单调递增的 Nonce (毫秒时间戳 + 序号防重)，并持久化至磁盘
        """
        now_ms = int(time.time() * 1000)
        if now_ms <= self._last_nonce:
            self._last_nonce += 1
        else:
            self._last_nonce = now_ms
        self._save_persistent_nonce()
        return self._last_nonce

    def sign_message(self, room: str, text: str, nonce: Optional[int] = None) -> Tuple[str, int, str]:
        """
        对即将发送给房间的消息进行 Ed25519 签名
        返回: (cleaned_text, nonce, signature_b64url)
        
        签名规范:
        - 签名载荷: `<room>|<nonce>|<cleaned_text>` (UTF-8)
        - 编码: 86 字符未补齐 Base64url (rstrip('='))
        """
        cleaned = clean_single_line(text)
        if nonce is None:
            nonce = self.get_next_nonce()
        else:
            self._last_nonce = max(self._last_nonce, nonce)

        payload = f"{room}|{nonce}|{cleaned}".encode("utf-8")

        if HAS_CRYPTOGRAPHY:
            priv = ed25519.Ed25519PrivateKey.from_private_bytes(self.private_key_bytes)
            raw_sig = priv.sign(payload)
        else:
            raw_sig = _signature_pure(payload, self.private_key_bytes, self.public_key_bytes)

        sig_b64url = base64.urlsafe_b64encode(raw_sig).rstrip(b"=").decode("ascii")
        return cleaned, nonce, sig_b64url
