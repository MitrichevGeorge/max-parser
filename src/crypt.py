import json
import os
import secrets
import stat
import sys
import unicodedata
from base64 import b64decode, b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, List, NoReturn, TypedDict, List, Dict

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from pydantic import BaseModel, ValidationError
from datetime import datetime

import questionary

__all__ = ["KDFParams", "VaultError", "CorruptVaultError", "InvalidPasswordError", "ClientVault", "VaultModel", "TokenModel"]


DEFAULT_MEMORY_COST: Final[int] = 256 * 1024   # 256 MiB
DEFAULT_TIME_COST: Final[int] = 3
DEFAULT_PARALLELISM: Final[int] = 4

MIN_MEMORY_COST: Final[int] = 64 * 1024        # 64 MiB
MAX_MEMORY_COST: Final[int] = 1024 * 1024      # 1 GiB
MIN_TIME_COST: Final[int] = 2
MAX_TIME_COST: Final[int] = 20
MIN_PARALLELISM: Final[int] = 1
MAX_PARALLELISM: Final[int] = 8

VAULT_FILE: Final[Path] = Path("vault.meta")
SUPPORTED_KDF_ALGORITHM: Final[str] = "argon2id"
STRICT_CHECK: Final[bool] = False

SALT_LEN: Final[int] = 16
NONCE_LEN: Final[int] = 12
KEY_LEN: Final[int] = 32

MAX_PLAINTEXT_SIZE: Final[int] = 512 * 1024      # 512 KiB
MAX_VAULT_FILE_SIZE: Final[int] = 1024 * 1024    # 1 MiB


def bye() -> NoReturn:
    print("bye")
    sys.exit(0)

class VaultError(Exception):
    pass

class CorruptVaultError(VaultError):
    pass

class InvalidPasswordError(VaultError):
    pass

@dataclass(frozen=True, slots=True)
class KDFParams:
    memory_cost: int = DEFAULT_MEMORY_COST
    time_cost: int = DEFAULT_TIME_COST
    parallelism: int = DEFAULT_PARALLELISM

    def __post_init__(self) -> None:
        for name, value in (("memory_cost", self.memory_cost), ("time_cost", self.time_cost), ("parallelism", self.parallelism)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")

        if not (MIN_MEMORY_COST <= self.memory_cost <= MAX_MEMORY_COST):
            raise ValueError(
                f"memory_cost must be in [{MIN_MEMORY_COST}, {MAX_MEMORY_COST}], "
                f"got {self.memory_cost}"
            )
        if not (MIN_TIME_COST <= self.time_cost <= MAX_TIME_COST):
            raise ValueError(
                f"time_cost must be in [{MIN_TIME_COST}, {MAX_TIME_COST}], "
                f"got {self.time_cost}"
            )
        if not (MIN_PARALLELISM <= self.parallelism <= MAX_PARALLELISM):
            raise ValueError(
                f"parallelism must be in [{MIN_PARALLELISM}, {MAX_PARALLELISM}], "
                f"got {self.parallelism}"
            )

def _normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)

def _validate_password(password: str) -> None:
    if not isinstance(password, str) or not password:
        raise VaultError("Password must be a non-empty string")

def _derive_key(password: str, salt: bytes, kdf: KDFParams) -> bytearray:
    normalized = _normalize_password(password)
    raw = hash_secret_raw(
        secret=normalized.encode("utf-8"),
        salt=salt,
        time_cost=kdf.time_cost,
        memory_cost=kdf.memory_cost,
        parallelism=kdf.parallelism,
        hash_len=KEY_LEN,
        type=Type.ID
    )
    return bytearray(raw)

def _zero(key: bytearray | None) -> None:
    if key is None:
        return
    for i in range(len(key)):
        key[i] = 0

def _build_aad(kdf: KDFParams, salt: bytes, nonce: bytes) -> bytes:
    return (
        SUPPORTED_KDF_ALGORITHM.encode("utf-8")
        + kdf.memory_cost.to_bytes(4, "big")
        + kdf.time_cost.to_bytes(4, "big")
        + kdf.parallelism.to_bytes(4, "big")
        + salt
        + nonce
    )

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

class VaultManager:
    def __init__(self, path: Path = VAULT_FILE) -> None:
        self._path = path

    def exists(self) -> bool:
        return self._path.exists()

    def create(self, password: str, data: VaultModel, kdf: KDFParams | None = None) -> None:
        self._write_encrypted(password, data, kdf)

    def open(self, password: str) -> VaultModel:
        _validate_password(password)
        if STRICT_CHECK:
            self._check_file_permissions()

        meta = self._read_meta()
        self._validate_meta_structure(meta)

        kdf = self._extract_kdf(meta)
        salt = b64decode(meta["salt"])
        nonce = b64decode(meta["nonce"])
        ciphertext = b64decode(meta["ciphertext"])

        if len(salt) != SALT_LEN or len(nonce) != NONCE_LEN:
            raise CorruptVaultError("Invalid salt or nonce length")

        key = _derive_key(password, salt, kdf)

        try:
            aad = _build_aad(kdf, salt, nonce)
            aead = ChaCha20Poly1305(bytes(key))
            plaintext = aead.decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise InvalidPasswordError("Неверный пароль или данные повреждены.") from exc
        finally:
            _zero(key)

        if len(plaintext) > MAX_PLAINTEXT_SIZE:
            raise CorruptVaultError(
                f"Plaintext too large: {len(plaintext)} bytes (max {MAX_PLAINTEXT_SIZE})"
            )

        try:
            data = VaultModel.model_validate_json(plaintext.decode("utf-8"))
        except (ValidationError, UnicodeDecodeError) as exc:
            raise CorruptVaultError("Plaintext is not valid JSON") from exc

        return data

    def save(self, password: str, data: VaultModel, kdf: KDFParams | None = None) -> None:
        if kdf is None and self.exists():
            try:
                meta = self._read_meta()
                self._validate_meta_structure(meta)
                kdf = self._extract_kdf(meta)
            except (CorruptVaultError, VaultError):
                kdf = KDFParams()
        self._write_encrypted(password, data, kdf)

    def _write_encrypted(self, password: str, data: VaultModel, kdf: KDFParams | None = None) -> None:
        _validate_password(password)
        kdf = kdf or KDFParams()

        plaintext = data.model_dump_json().encode("utf-8")
        if len(plaintext) > MAX_PLAINTEXT_SIZE:
            raise VaultError(f"Data too large: {len(plaintext)} bytes (max {MAX_PLAINTEXT_SIZE})")

        if STRICT_CHECK:
            self._check_parent_permissions()

        salt = secrets.token_bytes(SALT_LEN)
        nonce = secrets.token_bytes(NONCE_LEN)
        key = _derive_key(password, salt, kdf)

        try:
            aad = _build_aad(kdf, salt, nonce)
            aead = ChaCha20Poly1305(bytes(key))
            ciphertext = aead.encrypt(nonce, plaintext, aad)

            meta: dict[str, Any] = {
                "kdf": {
                    "algorithm": SUPPORTED_KDF_ALGORITHM,
                    "memory_cost": kdf.memory_cost,
                    "time_cost": kdf.time_cost,
                    "parallelism": kdf.parallelism,
                },
                "salt": b64encode(salt).decode("ascii"),
                "nonce": b64encode(nonce).decode("ascii"),
                "ciphertext": b64encode(ciphertext).decode("ascii"),
            }
            self._atomic_write(meta)
        finally:
            _zero(key)

    def _validate_meta_structure(self, meta: Any) -> None:
        if not isinstance(meta, dict):
            raise CorruptVaultError("Vault metadata must be a JSON object")

        required_top = ("kdf", "salt", "nonce", "ciphertext")
        for field in required_top:
            if field not in meta:
                raise CorruptVaultError(f"Missing metadata field: {field}")

        for field in ("salt", "nonce", "ciphertext"):
            if not isinstance(meta[field], str):
                raise CorruptVaultError(f"'{field}' must be a base64 string")

        kdf = meta["kdf"]
        if not isinstance(kdf, dict):
            raise CorruptVaultError("'kdf' must be a JSON object")

        for field in ("algorithm", "memory_cost", "time_cost", "parallelism"):
            if field not in kdf:
                raise CorruptVaultError(f"Missing KDF field: {field}")

        if not isinstance(kdf["algorithm"], str):
            raise CorruptVaultError("'algorithm' must be a string")

        for field in ("memory_cost", "time_cost", "parallelism"):
            if not _is_int(kdf[field]):
                raise CorruptVaultError(f"'{field}' must be an integer")

    def _extract_kdf(self, meta: dict[str, Any]) -> KDFParams:
        kdf = meta["kdf"]
        algorithm = kdf["algorithm"]

        if algorithm != SUPPORTED_KDF_ALGORITHM:
            raise CorruptVaultError(
                f"Unsupported KDF algorithm: {algorithm!r} "
                f"(expected {SUPPORTED_KDF_ALGORITHM!r})")

        try:
            return KDFParams(memory_cost=kdf["memory_cost"], time_cost=kdf["time_cost"], parallelism=kdf["parallelism"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptVaultError("Invalid KDF parameters") from exc

    def _read_meta(self) -> dict[str, Any]:
        st = self._path.stat()
        if st.st_size > MAX_VAULT_FILE_SIZE:
            raise VaultError(f"Vault file too large: {st.st_size} bytes (max {MAX_VAULT_FILE_SIZE})")

        raw = self._path.read_bytes()
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CorruptVaultError("Vault file is not valid JSON") from exc

    def _atomic_write(self, meta: dict[str, Any]) -> None:
        temp = self._path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            os.chmod(temp, 0o600)
            temp.replace(self._path)
            os.chmod(self._path, 0o600)
        except Exception:
            temp.unlink(missing_ok=True)
            raise

    def _check_file_permissions(self) -> None:
        if not self._path.exists():
            raise VaultError(f"Vault file does not exist: {self._path}")

        if self._path.is_symlink():
            raise VaultError("Vault file must not be a symlink")

        file_mode = stat.S_IMODE(self._path.stat().st_mode)
        if file_mode & 0o077:
            raise VaultError(f"Vault file permissions are too permissive: {oct(file_mode)}")

        self._check_parent_permissions()

    def _check_parent_permissions(self) -> None:
        parent = self._path.parent
        if not parent.exists():
            return

        dir_mode = stat.S_IMODE(parent.stat().st_mode)
        if dir_mode & 0o077:
            raise VaultError(f"Vault directory permissions are too permissive: {oct(dir_mode)}")

class TokenModel(BaseModel):
    token: str
    login_at: datetime
    last_visit_at: datetime
    username: str

class VaultModel(BaseModel):
    tokens: List[TokenModel] = []

class ClientVault:
    def __init__(self) -> None:
        self.vault = VaultManager(Path(".tokens"))
        self.tokens: List[TokenModel] = []
        self._password = ""

    async def init(self):
        if not self.vault.exists():
            self._password = await ask_new_pw()
            initial_data = VaultModel()
            self.vault.create(self._password, initial_data, KDFParams())
            self.tokens = initial_data.tokens
            return

        while True:
            password = await pw_ask("Введите пароль -> ")
            if not password:
                bye()

            try:
                data = self.vault.open(password)
                self._password = password
                self.tokens = data.tokens
                return
            except InvalidPasswordError as err:
                print(err)
            
    def save(self) -> None:
        self.vault.save(self._password, VaultModel(tokens=self.tokens))

async def pw_ask(prompt: str) -> str:
    try:
        result = await questionary.password(prompt).ask_async()
        if not result:
            bye()
        return result
    except EOFError:
        bye()

async def ask_new_pw() -> str:
    while True:
        password = await pw_ask("Придумайте пароль -> ")
        repeat = await pw_ask("Повторите пароль -> ")
        if password != repeat:
            print("Пароли не совпадают")
            continue
        return password

def main() -> int:
    vault = ClientVault()

    for i in vault.tokens:
        print(i.token, i.login_at)

    new = TokenModel(token="token", login_at=datetime.now(), last_visit_at=datetime.now(), username="user")
    vault.tokens.append(new)
    vault.save()

    return 0


if __name__ == "__main__":
    sys.exit(main())