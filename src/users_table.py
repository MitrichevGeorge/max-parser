from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

import openpyxl
import uvicorn
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from rich.console import Console
from rich.table import Table
from aiohttp import web
import aiohttp
from classes import UserProfile
from client import Tuiclient
from python_socks.async_.asyncio import Proxy

import captcha


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _login_with_token(token: str, proxy_url: Optional[str] = None) -> Tuiclient:
    if not token or not str(token).strip():
        raise ValueError("Empty token")

    cl = Tuiclient()
    if proxy_url:
        cl.proxy = proxy_url

    await cl._netw_connect()
    cl.token = str(token).strip()
    await cl.finalise_auth()

    profile = getattr(cl, "profile", None)
    if profile is None:
        await cl.disconnect()
        raise RuntimeError("finalise_auth succeeded but profile is empty — auth failed")

    logger.info(
        "Logged in as %s (id=%s)",
        getattr(profile, "get_name", lambda: "?")(),
        getattr(profile, "id", "?"),
    )
    return cl



@dataclass
class ScrapingState:
    current_id: int = 0
    id_min: int = 9_900_000
    id_max: int = 15_000_000
    id_step: int = 1000
    total_ids: int = field(init=False)

    cooldown_seconds: float = 20.0
    reconnect_cooldown_seconds: float = 20.0
    reinit_cooldown_seconds: float = 5.0
    batches_per_account: int = 3
    proxy_url: Optional[str] = None
    proxy_enabled: bool = False
    start_time: float = field(default_factory=time.time)
    last_batch_time: float = field(default_factory=time.time)
    avg_batch_duration: float = 0.0
    batch_count: int = 0
    last_broadcasted_batch_idx: int = 0
    current_account_index: int = -1
    current_account_username: Optional[str] = None

    current_action: str = "idle"
    action_started_at: float = field(default_factory=time.time)
    action_duration_estimate: float = 0.0
    action_progress: float = 0.0
    action_detail: str = ""

    is_running: bool = False
    is_paused: bool = False
    is_saving: bool = False
    last_error: Optional[str] = None

    reinit_logs: List[Dict[str, Any]] = field(default_factory=list)
    batch_logs: List[Dict[str, Any]] = field(default_factory=list)
    max_log_entries: int = 100

    users_scraped: int = 0
    excel_path: str = "users_table.xlsx"

    first_user_id: Optional[int] = None
    last_user_id: Optional[int] = None

    last_iteration_duration: Optional[float] = None
    last_batch_user_count: int = 0

    _save_path: Optional[str] = field(default=None, repr=False)
    account_manager: Optional[Any] = field(default=None, repr=False)

    ws_clients: Set[web.WebSocketResponse] = field(default_factory=set)

    def __post_init__(self):
        self.update_total_ids()
        self.update_excel_path()

    def update_total_ids(self) -> None:
        self.total_ids = max(0, self.id_max - self.id_min)

    def update_excel_path(self) -> None:
        first = self.first_user_id
        last = self.last_user_id
        if first is None or last is None:
            first = self.id_min
            last = self.id_max
        self.excel_path = f"users_table_{first}_{last}.xlsx"

    @property
    def save_path(self) -> str:
        if self._save_path is None:
            self._save_path = self.excel_path
        return self._save_path

    def reset_save_path(self) -> None:
        self._save_path = None

    def set_range(self, id_min: int, id_max: int, id_step: int) -> None:
        self.id_min = id_min
        self.id_max = id_max
        self.id_step = id_step
        self.update_total_ids()
        self.update_excel_path()

    def add_reinit_log(self, message: str) -> None:
        entry = {
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "message": message,
        }
        self.reinit_logs.append(entry)
        if len(self.reinit_logs) > self.max_log_entries:
            self.reinit_logs.pop(0)

    def add_batch_log(self, start_id: int, end_id: int, count: int, duration: float) -> None:
        entry = {
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "message": f"Батч {start_id:,} — {end_id:,}: {count} пользователей за {duration:.2f}с",
            "start_id": start_id,
            "end_id": end_id,
            "count": count,
            "duration": duration,
        }
        self.batch_logs.append(entry)
        if len(self.batch_logs) > self.max_log_entries:
            self.batch_logs.pop(0)

    def set_action(self, action: str, duration_estimate: float = 0.0, detail: str = "") -> None:
        self.current_action = action
        self.action_started_at = time.time()
        self.action_duration_estimate = max(duration_estimate, 0.001)
        self.action_progress = 0.0
        self.action_detail = detail

    def update_action_progress(self) -> None:
        if self.action_duration_estimate <= 0:
            self.action_progress = 0.0
            return
        elapsed = time.time() - self.action_started_at
        self.action_progress = min(100.0, (elapsed / self.action_duration_estimate) * 100)

    @property
    def progress_percent(self) -> float:
        if self.total_ids <= 0:
            return 100.0
        done = self.current_id - self.id_min
        return min(100.0, (done / self.total_ids) * 100)

    @property
    def estimated_remaining_seconds(self) -> float:
        if self.batch_count == 0 or not self.is_running:
            return 0.0
        remaining_ids = self.id_max - self.current_id
        remaining_batches = max(0, remaining_ids / self.id_step)
        return remaining_batches * (self.avg_batch_duration + self.cooldown_seconds)

    def to_dict(self) -> Dict[str, Any]:
        self.update_action_progress()
        return {
            "current_id": self.current_id,
            "id_min": self.id_min,
            "id_max": self.id_max,
            "id_step": self.id_step,
            "progress_percent": round(self.progress_percent, 2),
            "cooldown_seconds": self.cooldown_seconds,
            "reconnect_cooldown_seconds": self.reconnect_cooldown_seconds,
            "reinit_cooldown_seconds": self.reinit_cooldown_seconds,
            "batches_per_account": self.batches_per_account,
            "proxy_url": self.proxy_url,
            "proxy_enabled": self.proxy_enabled,
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "is_saving": self.is_saving,
            "users_scraped": self.users_scraped,
            "estimated_remaining_seconds": round(self.estimated_remaining_seconds, 1),
            "elapsed_seconds": round(time.time() - self.start_time, 1),
            "last_error": self.last_error,
            "reinit_logs": self.reinit_logs[-20:],
            "batch_logs": self.batch_logs[-20:],
            "batch_counts": [log["count"] for log in self.batch_logs[-50:]],
            "last_broadcasted_batch_idx": self.last_broadcasted_batch_idx,
            "iteration_duration": self.last_iteration_duration,
            "excel_path": self.excel_path,
            "current_action": self.current_action,
            "action_progress": round(self.action_progress, 1),
            "action_detail": self.action_detail,
            "action_label": self._action_label(),
            "action_color": self._action_color(),
            "accounts": self.account_manager.to_dict() if self.account_manager else [],
            "current_account_index": self.current_account_index,
            "current_account_username": self.current_account_username,
        }

    def _action_label(self) -> str:
        labels = {
            "idle": "Ожидание",
            "cooldown": "Кулдаун",
            "request": "Запрос к API",
            "parsing": "Парсинг данных",
            "saving": "Сохранение",
            "reinit": "Реинициализация",
        }
        return labels.get(self.current_action, self.current_action)

    def _action_color(self) -> str:
        colors = {
            "idle": "#8b949e",
            "cooldown": "#d29922",
            "request": "#58a6ff",
            "parsing": "#3fb950",
            "saving": "#a371f7",
            "reinit": "#f85149",
        }
        return colors.get(self.current_action, "#8b949e")


def _mask_token(token: str) -> str:
    if len(token) <= 12:
        return token
    return f"{token[:8]}***{token[-4:]}"


@dataclass
class Account:
    token: str
    username: Optional[str] = None
    user_id: Optional[int] = None
    valid: bool = False
    sleeping: bool = False
    error: Optional[str] = None
    profile: Optional[UserProfile] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_prefix": _mask_token(self.token),
            "username": self.username or "—",
            "user_id": self.user_id,
            "valid": self.valid,
            "sleeping": self.sleeping,
            "error": self.error,
        }


class AccountManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.accounts: List[Account] = []
        self._lock = asyncio.Lock()
        self._pending_phone_auth: Optional[Dict[str, Any]] = None
        self._captcha_server: Optional[uvicorn.Server] = None
        self._captcha_server_task: Optional[asyncio.Task] = None
        self._captcha_token_future: Optional[asyncio.Future] = None
        self.loaded_settings: Dict[str, Any] = {}
        self.proxy_url: Optional[str] = None
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.accounts = [
                Account(
                    token=a.get("token", ""),
                    username=a.get("username"),
                    user_id=a.get("user_id"),
                    valid=bool(a.get("valid", False)),
                    sleeping=bool(a.get("sleeping", False)),
                    error=a.get("error"),
                )
                for a in data.get("accounts", [])
            ]
            self.loaded_settings = {
                key: data[key]
                for key in ("id_min", "id_max", "id_step", "cooldown_seconds", "reconnect_cooldown_seconds", "reinit_cooldown_seconds", "batches_per_account", "proxy_url", "proxy_enabled")
                if key in data
            }
        except Exception as exc:
            logger.warning("Failed to load accounts: %s", exc)

    def save(self, state: ScrapingState) -> None:
        data = {
            "accounts": [
                {
                    "token": a.token,
                    "username": a.username,
                    "user_id": a.user_id,
                    "valid": a.valid,
                    "sleeping": a.sleeping,
                    "error": a.error,
                }
                for a in self.accounts
            ],
            "id_min": state.id_min,
            "id_max": state.id_max,
            "id_step": state.id_step,
            "cooldown_seconds": state.cooldown_seconds,
            "reconnect_cooldown_seconds": state.reconnect_cooldown_seconds,
            "reinit_cooldown_seconds": state.reinit_cooldown_seconds,
            "batches_per_account": state.batches_per_account,
            "proxy_url": state.proxy_url,
            "proxy_enabled": state.proxy_enabled,
        }
        try:
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save accounts: %s", exc)

    def to_dict(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self.accounts]

    def valid_count(self) -> int:
        return sum(1 for a in self.accounts if a.valid and not a.sleeping)

    def active_count(self) -> int:
        return self.valid_count()

    def all_sleeping_or_invalid(self) -> bool:
        return self.valid_count() == 0 and len(self.accounts) > 0

    def next_valid_index(self, start: int) -> Optional[int]:
        n = len(self.accounts)
        if n == 0:
            return None
        for offset in range(1, n + 1):
            idx = (start + offset) % n
            acc = self.accounts[idx]
            if acc.valid and not acc.sleeping:
                return idx
        return None

    def mark_sleeping(self, idx: int, reason: str = "") -> None:
        if 0 <= idx < len(self.accounts):
            acc = self.accounts[idx]
            acc.sleeping = True
            if reason:
                acc.error = reason
            logger.warning(
                "Account %s (idx=%d) marked as sleeping: %s",
                acc.username or acc.user_id or acc.token[:12],
                idx,
                reason or "API error",
            )

    async def validate_token(self, token: str) -> Account:
        cl: Optional[Tuiclient] = None
        try:
            cl = await _login_with_token(token, self.proxy_url)
            profile = cl.profile
            return Account(
                token=token,
                username=profile.get_name(),
                user_id=profile.id,
                valid=True,
                sleeping=False,
                profile=profile,
            )
        except Exception as exc:
            logger.exception("Token validation failed")
            return Account(token=token, valid=False, sleeping=False, error=str(exc))
        finally:
            if cl is not None:
                try:
                    await cl.disconnect()
                except Exception:
                    pass

    async def add_account(self, account: Account) -> Account:
        async with self._lock:
            for idx, existing in enumerate(self.accounts):
                if existing.token == account.token:
                    self.accounts[idx] = account
                    break
            else:
                self.accounts.append(account)
            return account

    async def remove_account(self, idx: int) -> bool:
        async with self._lock:
            if 0 <= idx < len(self.accounts):
                self.accounts.pop(idx)
                return True
            return False

    async def start_captcha_solver(self, url: str) -> str:
        captcha.state.captcha_url = url
        captcha.state.token_future = asyncio.get_running_loop().create_future()
        self._captcha_token_future = captcha.state.token_future
        config = uvicorn.Config(
            app=captcha.make_app(),
            host="127.0.0.1",
            port=18765,
            log_level="critical",
            access_log=False,
        )
        self._captcha_server = uvicorn.Server(config)
        self._captcha_server_task = asyncio.create_task(self._captcha_server.serve())
        return "http://127.0.0.1:18765/"

    async def stop_captcha_solver(self) -> None:
        if self._captcha_server and self._captcha_server_task and not self._captcha_server_task.done():
            self._captcha_server.should_exit = True
            self._captcha_server_task.cancel()
            try:
                await self._captcha_server_task
            except (asyncio.CancelledError, Exception):
                pass
        self._captcha_server = None
        self._captcha_server_task = None
        self._captcha_token_future = None
        captcha.state.captcha_url = None
        captcha.state.token_future = None

    def get_pending_phone_auth(self) -> Optional[Dict[str, Any]]:
        return self._pending_phone_auth

    def set_pending_phone_auth(self, pending: Optional[Dict[str, Any]]) -> None:
        self._pending_phone_auth = pending

    async def cleanup(self) -> None:
        await self.stop_captcha_solver()


class ScraperManager:
    def __init__(self, state: ScrapingState):
        self.state = state
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        async with self._lock:
            if self._task and not self._task.done():
                return False
            self._task = asyncio.create_task(_run_scraper_wrapper(self.state))
            return True

    async def stop(self) -> None:
        async with self._lock:
            task = self._task
            if task and not task.done():
                self.state.is_running = False
                try:
                    await asyncio.wait_for(task, timeout=30)
                except asyncio.TimeoutError:
                    logger.warning("Scraper did not stop gracefully, cancelling")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self._task = None

    async def restart(self) -> bool:
        await self.stop()
        return await self.start()


def _init_worksheet(ws: Worksheet) -> None:
    ws.title = "Users"
    ws.views.sheetView[0].showGridLines = True

    title_font = Font(name="Segoe UI", size=16, bold=True)
    ws.merge_cells("A1:H1")
    ws["A1"] = "Список пользователей"
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 40

    headers = ["ID", "Имена", "Телефон", "Регистрация", "URL", "Страна", "Статус", "Bio"]
    header_fill = PatternFill(start_color="00838F", end_color="00838F", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        if col_idx == 8:
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        else:
            cell.alignment = header_alignment
    ws.row_dimensions[3].height = 28


def _append_user_row(ws: Worksheet, row_idx: int, user: UserProfile) -> None:
    thin_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="thin", color="E0E0E0"),
    )

    names_list = [f"{n.firstName} {n.lastName}".strip() for n in user.names]
    names_str = ", ".join(names_list) if names_list else "—"
    phone_str = f"+{user.phone}" if user.phone else "—"
    country_str = user.country if user.country else "—"
    reg_time_str = user.registrationTime.strftime("%Y-%m-%d %H:%M") if user.registrationTime else "—"
    bio_str = user.description if user.description else "—"
    url_str = user.baseUrl if user.baseUrl else "—"
    status_str = str(user.accountStatus) if user.accountStatus else "—"

    row_data = [
        user.id,
        names_str,
        phone_str,
        reg_time_str,
        url_str,
        country_str,
        status_str,
        bio_str,
    ]

    for col_idx, val in enumerate(row_data, 1):
        cell = ws.cell(row=row_idx, column=col_idx, value=val)
        cell.border = thin_border
        cell.font = Font(name="Segoe UI", size=10, color="333333")
        if col_idx == 1:
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.number_format = "0"
        elif col_idx in (4, 6, 7):
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif col_idx == 3:
            cell.alignment = Alignment(horizontal="right", vertical="center")
        else:
            cell.alignment = Alignment(horizontal="left", vertical="center")

    ws.row_dimensions[row_idx].height = 22


def _auto_size_columns(ws: Worksheet) -> None:
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row < 3:
                continue
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)


async def _broadcast_state(state: ScrapingState) -> None:
    if not state.ws_clients:
        return
    payload = json.dumps({"type": "state", "data": state.to_dict()})
    dead_clients: Set[web.WebSocketResponse] = set()

    for ws in state.ws_clients:
        try:
            await ws.send_str(payload)
        except Exception:
            dead_clients.add(ws)

    for ws in dead_clients:
        state.ws_clients.discard(ws)


async def _run_scraper_wrapper(state: ScrapingState) -> None:
    try:
        await _run_scraper(state)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Scraper crashed: %s", exc)
        state.last_error = f"Scraper crashed: {exc}"
        state.is_running = False
        state.set_action("idle")
        await _broadcast_state(state)


async def _wait_while_running(
    state: ScrapingState, duration: float, action: str, detail: str = ""
) -> bool:
    state.set_action(action, duration_estimate=duration, detail=detail)
    start = time.time()
    while time.time() - start < duration:
        if not state.is_running:
            return False
        if state.is_paused:
            state.set_action("idle")
            return False
        await asyncio.sleep(0.1)
        await _broadcast_state(state)
    return True


async def _run_scraper(state: ScrapingState) -> None:
    manager = state.account_manager
    if manager is None or manager.valid_count() == 0:
        state.last_error = "Нет доступных аккаунтов. Настройте аккаунты в панели."
        state.is_running = False
        await _broadcast_state(state)
        return

    state.is_running = True
    state.start_time = time.time()
    state.current_id = state.id_min
    state.users_scraped = 0
    state.batch_count = 0
    state.avg_batch_duration = 0.0
    state.last_error = None
    state.last_iteration_duration = None
    state.last_batch_user_count = 0
    state.last_broadcasted_batch_idx = 0
    state.first_user_id = None
    state.last_user_id = None
    state.reinit_logs.clear()
    state.batch_logs.clear()
    state.reset_save_path()
    state.update_excel_path()

    active_client: Optional[Tuiclient] = None
    active_account_index = -1
    batches_on_current = 0

    async def close_active() -> None:
        nonlocal active_client
        try:
            await active_client.disconnect()
        except Exception:
            pass
        active_client = None

    def force_switch() -> None:
        nonlocal batches_on_current
        batches_on_current = state.batches_per_account

    async def activate_next_account() -> bool:
        nonlocal active_client, active_account_index, batches_on_current
        await close_active()
        if active_account_index != -1:
            if not await _wait_while_running(
                state, state.reconnect_cooldown_seconds, "reinit", "Переключение аккаунта"
            ):
                return False

        if state.is_running and state.reinit_cooldown_seconds > 0:
            if not await _wait_while_running(
                state, state.reinit_cooldown_seconds, "reinit", "Кулдаун реинициализации"
            ):
                return False

        if manager.valid_count() == 0:
            state.last_error = "Все аккаунты спящие или недоступны — сохраняю и останавливаюсь"
            state.add_reinit_log("Все аккаунты спящие/недоступны. Остановка скрапера.")
            return False

        next_idx = manager.next_valid_index(active_account_index)
        if next_idx is None:
            state.last_error = "Нет доступных (не спящих) аккаунтов для продолжения"
            state.add_reinit_log("Нет доступных аккаунтов — остановка")
            return False

        active_account_index = next_idx
        batches_on_current = 0
        account = manager.accounts[next_idx]
        state.current_account_index = next_idx
        state.current_account_username = account.username
        state.set_action(
            "reinit",
            duration_estimate=5.0,
            detail=f"Вход: {account.username or account.user_id or 'аккаунт'}",
        )
        await _broadcast_state(state)

        cl: Optional[Tuiclient] = None
        try:
            cl = await _login_with_token(account.token, manager.proxy_url)
            account.profile = cl.profile
            account.user_id = cl.profile.id
            account.username = cl.profile.get_name()
            account.valid = True
            account.sleeping = False
            account.error = None
            active_client = cl
            cl = None
            manager.save(state)
            state.add_reinit_log(f"Аккаунт {account.username} ({account.user_id}) активен")
            await _broadcast_state(state)
            return True
        except Exception as exc:
            logger.exception("Account login failed")
            manager.mark_sleeping(next_idx, f"Ошибка входа: {exc}")
            manager.save(state)
            state.last_error = f"Ошибка входа в аккаунт {account.username or account.user_id}: {exc}"
            state.add_reinit_log(f"Аккаунт усыплён (ошибка входа): {exc}")
            if cl is not None:
                try:
                    await cl.disconnect()
                except Exception:
                    pass
            await _broadcast_state(state)
            return False

    wb = openpyxl.Workbook()
    ws = wb.active
    if ws is None:
        raise RuntimeError("Failed to create worksheet")

    _init_worksheet(ws)
    current_row = 4

    console = Console()
    rich_table = Table(
        title="Users",
        title_style="bold magenta",
        show_header=True,
        header_style="bold cyan",
    )
    _init_rich_table(rich_table)

    try:
        while state.current_id < state.id_max and state.is_running:
            if state.is_paused:
                state.set_action("idle")
                await asyncio.sleep(0.5)
                await _broadcast_state(state)
                continue

            need_switch = active_client is None or batches_on_current >= state.batches_per_account
            if need_switch:
                ok = False
                attempts = 0
                max_attempts = max(1, manager.valid_count() + 1)
                while attempts < max_attempts and state.is_running:
                    ok = await activate_next_account()
                    if ok:
                        break
                    attempts += 1
                    if manager.valid_count() == 0:
                        state.last_error = "Все аккаунты спящие или недоступны — сохраняю и останавливаюсь"
                        state.add_reinit_log("Все аккаунты уснули. Финальное сохранение и стоп.")
                        break
                if not ok or not state.is_running:
                    break

            c_id_max = min(state.current_id + state.id_step, state.id_max)
            id_range = list(range(state.current_id, c_id_max))
            detail = f"IDs {state.current_id:,} — {c_id_max - 1:,}"

            logger.info("Fetching %s with account %s", detail, state.current_account_username)

            iteration_start = time.time()
            if not await _wait_while_running(state, state.cooldown_seconds, "cooldown", detail):
                continue

            state.set_action("request", duration_estimate=2.0, detail=detail)
            await _broadcast_state(state)

            batch_start = time.time()
            new_infos: List[UserProfile] = []
            try:
                new_infos = await active_client.get_infos(id_range)
            except Exception as exc:
                logger.exception("Batch fetch failed")
                state.last_error = str(exc)
                if active_account_index >= 0:
                    manager.mark_sleeping(
                        active_account_index,
                        f"Ошибка API при батче: {exc}",
                    )
                    manager.save(state)
                    state.add_reinit_log(
                        f"Аккаунт {state.current_account_username or active_account_index} "
                        f"усыплён из-за ошибки API: {exc}"
                    )
                else:
                    state.add_reinit_log(f"Ошибка батча: {exc}")
                await close_active()
                force_switch()

                if manager.valid_count() == 0:
                    state.last_error = "Все аккаунты спящие — сохраняю таблицу и останавливаюсь"
                    state.add_reinit_log("Все аккаунты уснули. Финальное сохранение и стоп.")
                    break

                if state.reinit_cooldown_seconds > 0:
                    await _wait_while_running(
                        state, state.reinit_cooldown_seconds, "reinit", "Кулдаун после ошибки"
                    )
                continue

            batch_duration = time.time() - batch_start
            state.batch_count += 1
            state.avg_batch_duration = (
                state.avg_batch_duration * (state.batch_count - 1) + batch_duration
            ) / state.batch_count
            state.last_batch_time = time.time()
            batches_on_current += 1

            users_by_id = {user.id: user for user in new_infos}

            parse_count = len([uid for uid in id_range if uid in users_by_id])
            state.last_batch_user_count = parse_count
            state.add_batch_log(state.current_id, c_id_max - 1, parse_count, batch_duration)
            state.set_action("parsing", duration_estimate=0.5 + parse_count * 0.01, detail=f"{parse_count} пользователей")
            await _broadcast_state(state)

            for uid in id_range:
                if uid not in users_by_id:
                    continue

                user = users_by_id[uid]
                state.users_scraped += 1

                if uid < state.id_min + 10:
                    try:
                        _add_rich_row(rich_table, user)
                    except Exception:
                        pass

                _append_user_row(ws, current_row, user)
                current_row += 1
                if state.first_user_id is None:
                    state.first_user_id = user.id
                state.last_user_id = user.id

                state.update_action_progress()
                if state.users_scraped % 10 == 0:
                    await _broadcast_state(state)

            iteration_duration = time.time() - iteration_start
            state.last_iteration_duration = round(iteration_duration, 2)
            state.last_broadcasted_batch_idx = len(state.batch_logs)

            state.current_id = c_id_max
            state.last_error = None

            if state.batch_count % 10 == 0:
                state.set_action("saving", duration_estimate=1.0, detail="Автосохранение")
                await _broadcast_state(state)
                _auto_size_columns(ws)
                wb.save(state.save_path)
                logger.info("Auto-saved checkpoint: %s", state.save_path)
                await _broadcast_state(state)

            await _broadcast_state(state)

        state.set_action("saving", duration_estimate=1.0, detail="Финальное сохранение")
        await _broadcast_state(state)
        _auto_size_columns(ws)
        state.update_excel_path()
        state.reset_save_path()
        wb.save(state.save_path)
        logger.info("Final save: %s", state.save_path)
        console.print(rich_table)

    finally:
        state.is_running = False
        state.set_action("idle")
        await close_active()
        state.current_account_index = -1
        state.current_account_username = None
        await _broadcast_state(state)


def _init_rich_table(table: Table) -> None:
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Имена", style="white")
    table.add_column("Телефон", justify="right")
    table.add_column("Регистрация", justify="center", style="blue")
    table.add_column("url", justify="center", style="blue")
    table.add_column("Страна", justify="center", style="yellow")
    table.add_column("Статус", justify="center")
    table.add_column("Bio", style="green")


def _add_rich_row(table: Table, user: UserProfile) -> None:
    names_list = [f"{n.firstName} {n.lastName}".strip() for n in user.names]
    names_str = ", ".join(names_list) if names_list else "—"
    phone_str = f"+{user.phone}" if user.phone else "—"
    country_str = user.country if user.country else "—"
    reg_time_str = user.registrationTime.strftime("%Y-%m-%d %H:%M") if user.registrationTime else "—"
    bio_str = user.description if user.description else "—"
    url_str = user.baseUrl if user.baseUrl else "—"
    status_str = str(user.accountStatus) if user.accountStatus else "—"

    table.add_row(
        str(user.id),
        names_str,
        phone_str,
        reg_time_str,
        url_str,
        country_str,
        status_str,
        bio_str,
    )


_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>User Scraper Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        bg: '#0f1117',
                        card: '#161b22',
                        border: '#30363d',
                        text: '#c9d1d9',
                        muted: '#8b949e',
                        accent: '#58a6ff',
                        success: '#3fb950',
                        warn: '#d29922',
                        danger: '#f85149',
                        purple: '#a371f7',
                    }
                }
            }
        }
    </script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; }

        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #161b22; }
        ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #484f58; }

        .progress-glow {
            box-shadow: 0 0 12px rgba(88, 166, 255, 0.4), 0 0 24px rgba(63, 185, 80, 0.2);
        }

        @keyframes pulse-dot {
            0%, 100% { opacity: 1; transform: scale(1); box-shadow: 0 0 0 0 currentColor; }
            50% { opacity: 0.6; transform: scale(0.85); box-shadow: 0 0 0 6px transparent; }
        }
        .animate-pulse-dot {
            animation: pulse-dot 1.5s ease-in-out infinite;
        }

        .card-hover {
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .card-hover:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }

        .btn-press:active {
            transform: scale(0.96);
        }

        .font-mono-nums {
            font-variant-numeric: tabular-nums;
            font-feature-settings: "tnum";
        }
    </style>
</head>
<body class="bg-bg text-text min-h-screen p-4 md:p-6 lg:p-8">
    <div class="max-w-7xl mx-auto">
        <div class="flex items-center gap-3 mb-6">
            <i data-lucide="search" class="w-7 h-7 text-accent"></i>
            <h1 class="text-2xl md:text-3xl font-semibold tracking-tight">User Scraper Dashboard</h1>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 mb-5 card-hover">
            <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-2">
                    <i data-lucide="bar-chart-3" class="w-5 h-5 text-accent"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Общий прогресс</span>
                </div>
                <span class="text-3xl md:text-4xl font-bold text-accent font-mono-nums" id="progress-text">0%</span>
            </div>
            <div class="w-full h-3 bg-border rounded-full overflow-hidden">
                <div id="progress-bar" class="h-full rounded-full transition-all duration-300 ease-out progress-glow"
                     style="width: 0%; background: linear-gradient(90deg, #58a6ff, #3fb950);"></div>
            </div>
            <div class="flex justify-between mt-2 text-sm text-muted">
                <span id="progress-min">0</span>
                <span id="progress-current" class="text-accent font-medium">0</span>
                <span id="progress-max">0</span>
            </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
            <!-- Current Action Card -->
            <div id="action-card" class="bg-card border border-border rounded-xl p-5 card-hover hidden">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-2">
                        <span id="action-dot" class="w-3 h-3 rounded-full animate-pulse-dot"></span>
                        <span id="action-label" class="text-sm font-medium uppercase tracking-wider text-muted">Ожидание</span>
                    </div>
                    <span id="action-percent" class="text-2xl font-bold font-mono-nums">0%</span>
                </div>
                <div class="w-full h-2 bg-border rounded-full overflow-hidden mb-2">
                    <div id="action-progress-fill" class="h-full rounded-full transition-all duration-300 ease-out"
                         style="width: 0%;"></div>
                </div>
                <p id="action-detail" class="text-sm text-muted truncate"></p>
            </div>

            <div class="bg-card border border-border rounded-xl p-5 card-hover">
                <div class="flex items-center gap-2 mb-3">
                    <i data-lucide="clock" class="w-5 h-5 text-warn"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Оставшееся время</span>
                </div>
                <div class="text-3xl font-bold text-warn font-mono-nums" id="eta">—</div>
                <p class="text-sm text-muted mt-2">Прошло: <span id="elapsed" class="text-text">0с</span></p>
            </div>

            <div class="bg-card border border-border rounded-xl p-5 card-hover">
                <div class="flex items-center gap-2 mb-3">
                    <i data-lucide="users" class="w-5 h-5 text-success"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Собрано пользователей</span>
                </div>
                <div class="text-3xl font-bold text-success font-mono-nums" id="users-scraped">0</div>
            </div>

            <div class="bg-card border border-border rounded-xl p-5 card-hover">
                <div class="flex items-center gap-2 mb-3">
                    <i data-lucide="activity" class="w-5 h-5 text-purple"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Статус</span>
                </div>
                <span id="status-badge" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold bg-danger/15 text-danger">
                    <span class="w-2 h-2 rounded-full bg-danger"></span>
                    Остановлен
                </span>
                <p id="last-error" class="text-sm text-muted mt-2 truncate"></p>
                <p id="current-account" class="text-sm text-muted mt-2 truncate">Текущий аккаунт: <span class="text-text">—</span></p>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
            <div class="bg-card border border-border rounded-xl p-5 md:p-6 card-hover">
                <div class="flex items-center gap-2 mb-4">
                    <i data-lucide="trending-up" class="w-5 h-5 text-accent"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Длительность итераций (сек)</span>
                </div>
                <div class="relative h-64 w-full">
                    <canvas id="iteration-chart"></canvas>
                </div>
            </div>

            <div class="bg-card border border-border rounded-xl p-5 md:p-6 card-hover">
                <div class="flex items-center gap-2 mb-4">
                    <i data-lucide="users" class="w-5 h-5 text-success"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Пользователей за батч</span>
                </div>
                <div class="relative h-64 w-full">
                    <canvas id="users-chart"></canvas>
                </div>
            </div>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 mb-5 card-hover" id="accounts-card">
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2">
                    <i data-lucide="shield-user" class="w-5 h-5 text-accent"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Аккаунты</span>
                </div>
                <span id="accounts-summary" class="text-xs text-muted">0 доступных</span>
            </div>

            <div id="accounts-list" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-4">
                <div class="text-muted italic text-sm">Аккаунты не настроены</div>
            </div>

            <div class="space-y-3 mb-3">
                <div>
                    <label class="block text-xs text-muted mb-1">Токены (каждый с новой строки — можно вставить сразу несколько)</label>
                    <textarea id="token-input" rows="4" placeholder="token1&#10;token2&#10;token3"
                              class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono resize-y"></textarea>
                </div>
                <div class="flex flex-wrap items-center gap-3">
                    <button onclick="addToken()" id="add-token-btn" class="btn-press flex items-center gap-2 bg-success hover:bg-success/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="key" class="w-4 h-4"></i>
                        <span id="add-token-btn-text">Добавить токены</span>
                    </button>
                    <button onclick="openPhoneModal()" class="btn-press flex items-center justify-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="smartphone" class="w-4 h-4"></i>
                        Войти по номеру
                    </button>
                </div>
            </div>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 mb-5 card-hover">
            <div class="flex items-center gap-2 mb-4">
                <i data-lucide="sliders-horizontal" class="w-5 h-5 text-muted"></i>
                <span class="text-sm font-medium uppercase tracking-wider text-muted">Настройки скрапера</span>
            </div>

            <form onsubmit="event.preventDefault(); updateRange();" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                        <label class="block text-xs text-muted mb-1">ID от</label>
                        <input type="number" id="id-min" value="9950000" min="1" step="1"
                               class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                    </div>
                    <div>
                        <label class="block text-xs text-muted mb-1">ID до</label>
                        <input type="number" id="id-max" value="15000000" min="1" step="1"
                               class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                    </div>
                    <div>
                        <label class="block text-xs text-muted mb-1">Шаг батча</label>
                        <input type="number" id="id-step" value="1000" min="1" step="1"
                               class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                    </div>
                </div>

                <div class="flex flex-wrap items-center gap-3 pt-2">
                    <button onclick="updateRange()" id="range-btn" type="button" class="btn-press flex items-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="save" class="w-4 h-4"></i>
                        <span id="range-btn-text">Сохранить настройки</span>
                    </button>

                    <button onclick="startScraper()" id="start-btn" type="button" class="btn-press flex items-center gap-2 bg-success hover:bg-success/80 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="play" class="w-4 h-4"></i>
                        <span id="start-btn-text">Старт</span>
                    </button>

                    <button onclick="restartScraper()" id="restart-btn" type="button" class="btn-press flex items-center gap-2 bg-accent hover:bg-accent/80 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                        <span id="restart-btn-text">Перезапустить</span>
                    </button>

                    <button onclick="togglePause()" id="pause-btn" type="button" class="btn-press flex items-center gap-2 bg-border hover:bg-border/80 text-text px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="pause" class="w-4 h-4"></i>
                        <span>Пауза</span>
                    </button>

                    <button onclick="saveNow()" type="button" class="btn-press flex items-center gap-2 bg-border hover:bg-border/80 text-text px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="download" class="w-4 h-4"></i>
                        <span>Сохранить Excel</span>
                    </button>

                    <a href="/api/download" download class="btn-press flex items-center gap-2 bg-purple hover:bg-purple/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors no-underline">
                        <i data-lucide="file-spreadsheet" class="w-4 h-4"></i>
                        Скачать таблицу
                    </a>

                    <button onclick="stopScraper()" type="button" class="btn-press flex items-center gap-2 bg-danger hover:bg-danger/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="square" class="w-4 h-4"></i>
                        Стоп
                    </button>
                </div>
            </form>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 mb-5 card-hover">
            <div class="flex items-center gap-2 mb-4">
                <i data-lucide="timer" class="w-5 h-5 text-warn"></i>
                <span class="text-sm font-medium uppercase tracking-wider text-muted">Настройки кулдаунов</span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
                <div>
                    <label class="block text-xs text-muted mb-1">Между батчами (сек)</label>
                    <input type="number" id="cooldown-input" value="20" min="0" step="0.5"
                           class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                </div>
                <div>
                    <label class="block text-xs text-muted mb-1">При переключении аккаунта (сек)</label>
                    <input type="number" id="reconnect-cooldown-input" value="20" min="0" step="0.5"
                           class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                </div>
                <div>
                    <label class="block text-xs text-muted mb-1">При реинициализации (сек)</label>
                    <input type="number" id="reinit-cooldown-input" value="5" min="0" step="0.5"
                           class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                </div>
            </div>

            <div class="flex flex-wrap items-center gap-3">
                <button onclick="updateCooldowns()" id="cooldowns-save-btn" type="button" class="btn-press flex items-center gap-2 bg-warn hover:bg-warn/80 text-bg px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="save" class="w-4 h-4"></i>
                    <span id="cooldowns-save-btn-text">Сохранить кулдауны</span>
                </button>

                <div class="flex items-center gap-2">
                    <label class="text-xs text-muted">Батчей с одного аккаунта:</label>
                    <input type="number" id="batches-per-account-input" value="3" min="1" step="1"
                           class="w-20 bg-bg border border-border rounded-lg px-2 py-1.5 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                    <button onclick="updateBatchesPerAccount()" type="button" class="btn-press flex items-center gap-1 bg-border hover:bg-border/80 text-text px-3 py-1.5 rounded-lg text-xs font-medium transition-colors">
                        <i data-lucide="check" class="w-3 h-3"></i>
                    </button>
                </div>
            </div>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 mb-5 card-hover">
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2">
                    <i data-lucide="globe" class="w-5 h-5 text-purple"></i>
                    <span class="text-sm font-medium uppercase tracking-wider text-muted">Настройки прокси</span>
                </div>
                <span id="proxy-status-badge" class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-danger/15 text-danger">
                    <span class="w-1.5 h-1.5 rounded-full bg-danger"></span>Не используется
                </span>
            </div>

            <div class="space-y-4">
                <div class="flex flex-wrap items-center gap-4">
                    <label class="inline-flex items-center gap-2 cursor-pointer">
                        <input type="radio" name="proxy-mode" id="proxy-mode-off" value="off" checked onchange="toggleProxyMode()"
                               class="accent-accent w-4 h-4">
                        <span class="text-sm text-text">Не использовать прокси</span>
                    </label>
                    <label class="inline-flex items-center gap-2 cursor-pointer">
                        <input type="radio" name="proxy-mode" id="proxy-mode-on" value="on" onchange="toggleProxyMode()"
                               class="accent-accent w-4 h-4">
                        <span class="text-sm text-text">Использовать SOCKS прокси</span>
                    </label>
                </div>

                <div id="proxy-url-row" class="hidden">
                    <label class="block text-xs text-muted mb-1">URL SOCKS прокси</label>
                    <input type="text" id="proxy-url-input" placeholder="socks5://user:pass@host:port"
                           class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                    <p class="text-xs text-muted mt-1">Поддерживаются socks4://, socks5://, socks5h://</p>
                </div>

                <div id="proxy-results" class="hidden bg-bg border border-border rounded-lg p-3">
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div class="flex items-center justify-between">
                            <span class="text-xs text-muted">Пинг до прокси</span>
                            <span id="proxy-ping" class="text-sm font-mono-nums font-medium text-text">—</span>
                        </div>
                        <div class="flex items-center justify-between">
                            <span class="text-xs text-muted">Пинг до WS через прокси</span>
                            <span id="ws-ping" class="text-sm font-mono-nums font-medium text-text">—</span>
                        </div>
                    </div>
                    <p id="proxy-test-error" class="text-xs text-danger mt-2 hidden"></p>
                </div>

                <div class="flex flex-wrap items-center gap-3">
                    <button onclick="testProxy()" id="proxy-test-btn" type="button" class="btn-press flex items-center gap-2 bg-purple hover:bg-purple/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="activity" class="w-4 h-4"></i>
                        <span id="proxy-test-btn-text">Проверить прокси</span>
                    </button>

                    <button onclick="saveProxy()" id="proxy-save-btn" type="button" class="btn-press flex items-center gap-2 bg-success hover:bg-success/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="save" class="w-4 h-4"></i>
                        <span id="proxy-save-btn-text">Сохранить прокси</span>
                    </button>
                </div>
            </div>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 mb-5 card-hover">
            <div class="flex items-center gap-2 mb-4">
                <i data-lucide="list-checks" class="w-5 h-5 text-success"></i>
                <span class="text-sm font-medium uppercase tracking-wider text-muted">Лог батчей</span>
            </div>
            <div id="batch-log-container" class="max-h-72 overflow-y-auto font-mono text-xs space-y-1">
                <div class="text-muted italic py-2">Пока нет успешных батчей</div>
            </div>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 card-hover">
            <div class="flex items-center gap-2 mb-4">
                <i data-lucide="scroll-text" class="w-5 h-5 text-muted"></i>
                <span class="text-sm font-medium uppercase tracking-wider text-muted">Лог реинитов</span>
            </div>
            <div id="log-container" class="max-h-72 overflow-y-auto font-mono text-xs space-y-1">
                <div class="text-muted italic py-2">Лог пуст — пока нет реинитов</div>
            </div>
        </div>
    </div>

    <div id="phone-modal" class="fixed inset-0 z-50 hidden">
        <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" onclick="closePhoneModal()"></div>
        <div class="absolute inset-x-4 top-10 md:inset-x-auto md:left-1/2 md:-translate-x-1/2 md:w-[600px] max-h-[90vh] overflow-y-auto bg-card border border-border rounded-xl shadow-2xl p-6">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-semibold text-text">Вход по номеру телефона</h3>
                <button onclick="closePhoneModal()" class="text-muted hover:text-text transition-colors">
                    <i data-lucide="x" class="w-5 h-5"></i>
                </button>
            </div>

            <div id="phone-step-1" class="space-y-3">
                <label class="block text-sm text-muted">Номер телефона</label>
                <input type="tel" id="phone-input" placeholder="+79991234567"
                       class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors">
                <p class="text-xs text-muted">После нажатия кнопки откроется капча во фрейме. Решите её, чтобы получить SMS-код.</p>
                <button onclick="initPhoneAuth()" id="phone-init-btn" class="btn-press w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="send" class="w-4 h-4"></i>
                    <span id="phone-init-btn-text">Получить код</span>
                </button>
            </div>

            <div id="phone-step-2" class="hidden space-y-3">
                <div class="flex items-center gap-2 text-sm text-warn">
                    <span class="w-2 h-2 rounded-full bg-warn animate-pulse"></span>
                    <span id="phone-status-text">Решите капчу во фрейме...</span>
                </div>
                <iframe id="captcha-frame" class="w-full h-[500px] rounded-lg border border-border bg-bg"></iframe>
                <button onclick="cancelPhoneAuth()" class="btn-press w-full flex items-center justify-center gap-2 bg-danger hover:bg-danger/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    Отмена
                </button>
            </div>

            <div id="phone-step-3" class="hidden space-y-3">
                <label class="block text-sm text-muted">Код из SMS</label>
                <input type="text" id="phone-code-input" placeholder="1234"
                       class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors">
                <button onclick="verifyPhoneCode()" id="phone-verify-btn" class="btn-press w-full flex items-center justify-center gap-2 bg-success hover:bg-success/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <span id="phone-verify-btn-text">Войти</span>
                </button>
            </div>

            <div id="phone-error" class="hidden mt-3 p-3 rounded-lg bg-danger/10 text-danger text-sm"></div>
        </div>
    </div>

    <script>
        lucide.createIcons();

        const ctx = document.getElementById('iteration-chart').getContext('2d');
        const iterationChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Длительность (сек)',
                    data: [],
                    borderColor: '#58a6ff',
                    backgroundColor: 'rgba(88, 166, 255, 0.1)',
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#58a6ff',
                    pointBorderColor: '#0f1117',
                    pointBorderWidth: 2,
                    fill: true,
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#161b22',
                        borderColor: '#30363d',
                        borderWidth: 1,
                        titleColor: '#8b949e',
                        bodyColor: '#c9d1d9',
                        padding: 10,
                        cornerRadius: 8,
                        displayColors: false,
                        callbacks: {
                            title: (items) => 'Итерация ' + items[0].label,
                            label: (item) => item.raw.toFixed(2) + ' сек'
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: '#30363d', drawBorder: false },
                        ticks: { color: '#8b949e', font: { size: 11 } }
                    },
                    y: {
                        grid: { color: '#30363d', drawBorder: false },
                        ticks: { color: '#8b949e', font: { size: 11 } },
                        beginAtZero: true
                    }
                }
            }
        });

        const usersCtx = document.getElementById('users-chart').getContext('2d');
        const usersChart = new Chart(usersCtx, {
            type: 'bar',
            data: {
                labels: [],
                datasets: [{
                    label: 'Пользователей',
                    data: [],
                    backgroundColor: 'rgba(63, 185, 80, 0.6)',
                    borderColor: '#3fb950',
                    borderWidth: 1,
                    borderRadius: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#161b22',
                        borderColor: '#30363d',
                        borderWidth: 1,
                        titleColor: '#8b949e',
                        bodyColor: '#c9d1d9',
                        padding: 10,
                        cornerRadius: 8,
                        displayColors: false,
                        callbacks: {
                            title: (items) => 'Итерация ' + items[0].label,
                            label: (item) => item.raw + ' пользователей'
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: '#30363d', drawBorder: false },
                        ticks: { color: '#8b949e', font: { size: 11 } }
                    },
                    y: {
                        grid: { color: '#30363d', drawBorder: false },
                        ticks: { color: '#8b949e', font: { size: 11 } },
                        beginAtZero: true
                    }
                }
            }
        });

        const ws = new WebSocket(`ws://${location.host}/ws`);
        let isPaused = false;
        let lastProcessedBatchIdx = 0;
        let iterationDurations = [];
        let batchUserCounts = [];
        const MAX_CHART_POINTS = 50;

        function fmtTime(sec) {
            if (sec < 60) return Math.round(sec) + 'с';
            const m = Math.floor(sec / 60);
            const s = Math.round(sec % 60);
            if (m < 60) return m + 'м ' + s + 'с';
            const h = Math.floor(m / 60);
            return h + 'ч ' + (m % 60) + 'м ' + s + 'с';
        }

        function fmtNum(n) {
            return n.toLocaleString('ru-RU');
        }

        function updateChart(duration) {
            iterationDurations.push(duration);
            if (iterationDurations.length > MAX_CHART_POINTS) {
                iterationDurations.shift();
            }
            iterationChart.data.labels = iterationDurations.map((_, i) => i + 1);
            iterationChart.data.datasets[0].data = iterationDurations;
            iterationChart.update('none');
        }

        function updateUsersChart(counts) {
            batchUserCounts = counts.slice(-MAX_CHART_POINTS);
            usersChart.data.labels = batchUserCounts.map((_, i) => i + 1);
            usersChart.data.datasets[0].data = batchUserCounts;
            usersChart.update('none');
        }

        const actionColors = {
            cooldown: { bg: '#d29922', gradient: 'linear-gradient(90deg, #d29922, #e3b341)' },
            parsing: { bg: '#3fb950', gradient: 'linear-gradient(90deg, #3fb950, #56d364)' },
            request: { bg: '#58a6ff', gradient: 'linear-gradient(90deg, #58a6ff, #79c0ff)' },
            saving: { bg: '#a371f7', gradient: 'linear-gradient(90deg, #a371f7, #bc8cff)' },
            reinit: { bg: '#f85149', gradient: 'linear-gradient(90deg, #f85149, #ff7b72)' },
            idle: { bg: '#30363d', gradient: '#30363d' }
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type !== 'state') return;
                const d = msg.data;

                const progressText = document.getElementById('progress-text');
                const progressBar = document.getElementById('progress-bar');
                if (progressText) progressText.textContent = (d.progress_percent || 0) + '%';
                if (progressBar) progressBar.style.width = (d.progress_percent || 0) + '%';

                const progressMinEl = document.getElementById('progress-min');
                const progressCurrentEl = document.getElementById('progress-current');
                const progressMaxEl = document.getElementById('progress-max');
                if (progressMinEl) progressMinEl.textContent = fmtNum(d.id_min || 0);
                if (progressCurrentEl) progressCurrentEl.textContent = fmtNum(d.current_id || 0);
                if (progressMaxEl) progressMaxEl.textContent = fmtNum(d.id_max || 0);

                const etaEl = document.getElementById('eta');
                const elapsedEl = document.getElementById('elapsed');
                if (etaEl) etaEl.textContent = d.is_running ? fmtTime(d.estimated_remaining_seconds || 0) : '—';
                if (elapsedEl) elapsedEl.textContent = fmtTime(d.elapsed_seconds || 0);

                const usersScrapedEl = document.getElementById('users-scraped');
                if (usersScrapedEl) usersScrapedEl.textContent = fmtNum(d.users_scraped || 0);

                const badge = document.getElementById('status-badge');
                if (badge) {
                    if (d.is_running && !d.is_paused) {
                        badge.className = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold bg-success/15 text-success';
                        badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-success animate-pulse"></span>Выполняется';
                    } else if (d.is_paused) {
                        badge.className = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold bg-warn/15 text-warn';
                        badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-warn"></span>Пауза';
                    } else {
                        badge.className = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold bg-danger/15 text-danger';
                        badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-danger"></span>Остановлен';
                    }
                }

                const lastErrorEl = document.getElementById('last-error');
                if (lastErrorEl) lastErrorEl.textContent = d.last_error || '';

                const currentAccountEl = document.getElementById('current-account');
                if (currentAccountEl) {
                    const span = currentAccountEl.querySelector('span');
                    if (span) span.textContent = d.is_running && d.current_account_username ? `${d.current_account_username} (${d.current_account_index + 1})` : '—';
                }

                renderAccounts(d.accounts || []);
                const validAccounts = (d.accounts || []).filter(a => a.valid && !a.sleeping).length;
                const startBtn = document.getElementById('start-btn');
                if (startBtn) startBtn.disabled = validAccounts === 0 || d.is_running;
                const restartBtn = document.getElementById('restart-btn');
                if (restartBtn) restartBtn.disabled = validAccounts === 0;

                const batchesInput = document.getElementById('batches-per-account-input');
                if (batchesInput && !batchesInput.matches(':focus')) batchesInput.value = d.batches_per_account || 3;

                const idMinEl = document.getElementById('id-min');
                const idMaxEl2 = document.getElementById('id-max');
                const idStepEl = document.getElementById('id-step');
                if (idMinEl && !idMinEl.matches(':focus')) idMinEl.value = d.id_min || 9950000;
                if (idMaxEl2 && !idMaxEl2.matches(':focus')) idMaxEl2.value = d.id_max || 15000000;
                if (idStepEl && !idStepEl.matches(':focus')) idStepEl.value = d.id_step || 1000;

                const cooldownInput = document.getElementById('cooldown-input');
                const reconnectInput = document.getElementById('reconnect-cooldown-input');
                const reinitInput = document.getElementById('reinit-cooldown-input');
                if (cooldownInput && !cooldownInput.matches(':focus')) cooldownInput.value = d.cooldown_seconds ?? 20;
                if (reconnectInput && !reconnectInput.matches(':focus')) reconnectInput.value = d.reconnect_cooldown_seconds ?? 20;
                if (reinitInput && !reinitInput.matches(':focus')) reinitInput.value = d.reinit_cooldown_seconds ?? 5;

                renderProxyState(d);

                const pauseBtn = document.getElementById('pause-btn');
                const pauseSpan = pauseBtn?.querySelector('span');
                const pauseIcon = pauseBtn?.querySelector('i');
                if (pauseBtn && pauseSpan) {
                    if (d.is_paused) {
                        pauseSpan.textContent = 'Продолжить';
                        if (pauseIcon) {
                            pauseIcon.setAttribute('data-lucide', 'play');
                            pauseIcon.classList.remove('lucide-pause');
                            pauseIcon.classList.add('lucide-play');
                        }
                    } else {
                        pauseSpan.textContent = 'Пауза';
                        if (pauseIcon) {
                            pauseIcon.setAttribute('data-lucide', 'pause');
                            pauseIcon.classList.remove('lucide-play');
                            pauseIcon.classList.add('lucide-pause');
                        }
                    }
                    lucide.createIcons();
                }
                isPaused = d.is_paused;

                const actionCard = document.getElementById('action-card');
                const actionLabel = document.getElementById('action-label');
                const actionDot = document.getElementById('action-dot');
                const actionDetail = document.getElementById('action-detail');
                const actionPercent = document.getElementById('action-percent');
                const actionFill = document.getElementById('action-progress-fill');

                if (d.is_running && d.current_action && d.current_action !== 'idle') {
                    if (actionCard) actionCard.classList.remove('hidden');
                    const color = d.action_color || '#8b949e';
                    const colors = actionColors[d.current_action] || actionColors.idle;

                    if (actionLabel) {
                        actionLabel.textContent = d.action_label || d.current_action;
                        actionLabel.style.color = color;
                    }
                    if (actionDot) {
                        actionDot.style.background = color;
                        actionDot.style.color = color;
                    }
                    if (actionDetail) actionDetail.textContent = d.action_detail || '';
                    if (actionPercent) {
                        actionPercent.textContent = Math.round(d.action_progress || 0) + '%';
                        actionPercent.style.color = color;
                    }
                    if (actionFill) {
                        actionFill.style.width = (d.action_progress || 0) + '%';
                        actionFill.style.background = colors.gradient;
                    }
                } else {
                    if (actionCard) actionCard.classList.add('hidden');
                }

                if (d.last_broadcasted_batch_idx !== lastProcessedBatchIdx) {
                    lastProcessedBatchIdx = d.last_broadcasted_batch_idx;
                    if (d.iteration_duration !== undefined && d.iteration_duration !== null) {
                        updateChart(d.iteration_duration);
                    }
                    if (d.batch_counts && d.batch_counts.length > 0) {
                        updateUsersChart(d.batch_counts);
                    }
                }

                const batchLogContainer = document.getElementById('batch-log-container');
                if (batchLogContainer && d.batch_logs && d.batch_logs.length > 0) {
                    batchLogContainer.innerHTML = d.batch_logs.map((l) => {
                        return `<div class="log-entry-anim flex gap-3 py-1.5 border-b border-border/50">
                            <span class="text-muted whitespace-nowrap shrink-0">${l.timestamp || ''}</span>
                            <span class="text-success">${l.message || ''}</span>
                        </div>`;
                    }).join('');
                    batchLogContainer.scrollTop = batchLogContainer.scrollHeight;
                }

                const logContainer = document.getElementById('log-container');
                if (logContainer && d.reinit_logs && d.reinit_logs.length > 0) {
                    logContainer.innerHTML = d.reinit_logs.map((l, i) => {
                        let cls = 'text-text';
                        const msg = (l.message || '').toString();
                        if (msg.includes('Error') || msg.includes('RuntimeError')) cls = 'text-danger';
                        else if (msg.includes('reinit')) cls = 'text-warn';
                        return `<div class="log-entry-anim flex gap-3 py-1.5 border-b border-border/50 ${i === d.reinit_logs.length - 1 ? '' : ''}">
                            <span class="text-muted whitespace-nowrap shrink-0">${l.timestamp || ''}</span>
                            <span class="${cls}">${msg}</span>
                        </div>`;
                    }).join('');
                    logContainer.scrollTop = logContainer.scrollHeight;
                }
            } catch (err) {
                console.error('Dashboard update error:', err);
            }
        };

        ws.onopen = () => {
            console.log('[Dashboard] WebSocket connected');
            restorePhoneAuthState();
        };

        ws.onerror = (err) => {
            console.error('[Dashboard] WebSocket error:', err);
        };

        ws.onclose = () => {
            console.log('[Dashboard] WebSocket closed');
            const badge = document.getElementById('status-badge');
            if (badge) {
                badge.className = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold bg-danger/15 text-danger';
                badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-danger"></span>Disconnected';
            }
            const actionCard = document.getElementById('action-card');
            if (actionCard) actionCard.classList.add('hidden');
        };

        function renderProxyState(d) {
            const offRadio = document.getElementById('proxy-mode-off');
            const onRadio = document.getElementById('proxy-mode-on');
            const urlRow = document.getElementById('proxy-url-row');
            const urlInput = document.getElementById('proxy-url-input');
            const badge = document.getElementById('proxy-status-badge');
            const enabled = d.proxy_enabled && d.proxy_url;
            if (offRadio) offRadio.checked = !enabled;
            if (onRadio) onRadio.checked = !!enabled;
            if (urlRow) urlRow.classList.toggle('hidden', !enabled && !(onRadio?.checked));
            if (urlInput && !urlInput.matches(':focus') && d.proxy_url) urlInput.value = d.proxy_url;
            if (badge) {
                if (enabled) {
                    badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-success/15 text-success';
                    badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-success animate-pulse"></span>Активен';
                } else {
                    badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-danger/15 text-danger';
                    badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-danger"></span>Не используется';
                }
            }
        }

        function toggleProxyMode() {
            const on = document.getElementById('proxy-mode-on')?.checked;
            document.getElementById('proxy-url-row')?.classList.toggle('hidden', !on);
        }

        function getProxyFormState() {
            const enabled = document.getElementById('proxy-mode-on')?.checked;
            const url = document.getElementById('proxy-url-input')?.value?.trim() || '';
            return { enabled, url };
        }

        function showProxyTestResult({ok, proxy_ping_ms, ws_ping_ms, error}) {
            const results = document.getElementById('proxy-results');
            const proxyPing = document.getElementById('proxy-ping');
            const wsPing = document.getElementById('ws-ping');
            const errEl = document.getElementById('proxy-test-error');
            if (results) results.classList.remove('hidden');
            if (proxyPing) proxyPing.textContent = proxy_ping_ms != null ? proxy_ping_ms + ' мс' : '—';
            if (wsPing) wsPing.textContent = ws_ping_ms != null ? ws_ping_ms + ' мс' : '—';
            if (errEl) {
                if (error) {
                    errEl.textContent = error;
                    errEl.classList.remove('hidden');
                } else {
                    errEl.classList.add('hidden');
                }
            }
        }

        async function testProxy() {
            const { enabled, url } = getProxyFormState();
            if (!enabled || !url) {
                alert('Включите режим «Использовать SOCKS прокси» и введите URL');
                return;
            }
            const btn = document.getElementById('proxy-test-btn');
            const btnText = document.getElementById('proxy-test-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Проверка...';
            try {
                const res = await fetch('/api/proxy/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({proxy_url: url})
                });
                const data = await res.json();
                showProxyTestResult(data);
                if (!data.ok) alert(data.error || 'Проверка не пройдена');
            } catch (err) {
                showProxyTestResult({ok: false, error: 'Ошибка сети: ' + err.message});
            } finally {
                if (btnText) btnText.textContent = 'Проверить прокси';
                if (btn) btn.disabled = false;
            }
        }

        async function saveProxy() {
            const { enabled, url } = getProxyFormState();
            const btn = document.getElementById('proxy-save-btn');
            const btnText = document.getElementById('proxy-save-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Сохранение...';
            try {
                const res = await fetch('/api/proxy', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({proxy_enabled: enabled, proxy_url: url})
                });
                const data = await res.json();
                if (!data.ok) alert(data.error || 'Ошибка сохранения');
                if (btnText) btnText.textContent = data.ok ? 'Сохранено!' : 'Ошибка';
                if (data.ok) {
                    showProxyTestResult({ok: true});
                    document.getElementById('proxy-results')?.classList.add('hidden');
                }
            } catch (err) {
                if (btnText) btnText.textContent = 'Ошибка';
            }
            setTimeout(() => {
                if (btnText) btnText.textContent = 'Сохранить прокси';
                if (btn) btn.disabled = false;
            }, 2000);
        }

        async function updateCooldowns() {
            const cooldown = parseFloat(document.getElementById('cooldown-input').value);
            const reconnect = parseFloat(document.getElementById('reconnect-cooldown-input').value);
            const reinit = parseFloat(document.getElementById('reinit-cooldown-input').value);
            if (isNaN(cooldown) || cooldown < 0 || isNaN(reconnect) || reconnect < 0 || isNaN(reinit) || reinit < 0) {
                alert('Все кулдауны должны быть числами ≥ 0');
                return;
            }
            const btn = document.getElementById('cooldowns-save-btn');
            const btnText = document.getElementById('cooldowns-save-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Сохранение...';
            try {
                const res = await fetch('/api/cooldowns', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        cooldown_seconds: cooldown,
                        reconnect_cooldown_seconds: reconnect,
                        reinit_cooldown_seconds: reinit
                    })
                });
                const data = await res.json();
                if (!data.ok) alert(data.error || 'Ошибка');
                if (btnText) btnText.textContent = data.ok ? 'Сохранено!' : 'Ошибка';
            } catch {
                if (btnText) btnText.textContent = 'Ошибка';
            }
            setTimeout(() => {
                if (btnText) btnText.textContent = 'Сохранить кулдауны';
                if (btn) btn.disabled = false;
            }, 2000);
        }

        async function togglePause() {
            await fetch('/api/pause', {method: 'POST'});
        }

        async function saveNow() {
            const btnText = document.getElementById('save-btn-text');
            const btn = btnText?.parentElement;
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Сохраняю...';
            try {
                const res = await fetch('/api/save', {method: 'POST'});
                const data = await res.json();
                if (btnText) btnText.textContent = data.ok ? 'Сохранено!' : 'Ошибка';
            } catch {
                if (btnText) btnText.textContent = 'Ошибка';
            }
            setTimeout(() => {
                if (btnText) btnText.textContent = 'Сохранить сейчас';
                if (btn) btn.disabled = false;
            }, 2000);
        }

        function readRange() {
            const id_min = parseInt(document.getElementById('id-min').value);
            const id_max = parseInt(document.getElementById('id-max').value);
            const id_step = parseInt(document.getElementById('id-step').value);
            return {id_min, id_max, id_step};
        }

        function validateRange({id_min, id_max, id_step}) {
            if (isNaN(id_min) || isNaN(id_max) || isNaN(id_step)) {
                return 'Введите числовые значения диапазона';
            }
            if (id_min < 1 || id_max < 1 || id_step < 1) {
                return 'Значения должны быть ≥ 1';
            }
            if (id_min >= id_max) {
                return 'ID от должно быть меньше ID до';
            }
            if (id_step > (id_max - id_min)) {
                return 'Шаг не может быть больше диапазона';
            }
            return null;
        }

        async function updateRange() {
            const range = readRange();
            const error = validateRange(range);
            if (error) {
                alert(error);
                return;
            }
            const btn = document.getElementById('range-btn');
            const btnText = document.getElementById('range-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Сохранение...';
            try {
                const res = await fetch('/api/range', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(range)
                });
                const data = await res.json();
                if (!data.ok) alert(data.error || 'Ошибка');
                if (btnText) btnText.textContent = data.ok ? 'Сохранено!' : 'Ошибка';
            } catch {
                if (btnText) btnText.textContent = 'Ошибка';
            }
            setTimeout(() => {
                if (btnText) btnText.textContent = 'Сохранить настройки';
                if (btn) btn.disabled = false;
            }, 2000);
        }

        async function restartScraper() {
            const range = readRange();
            const error = validateRange(range);
            if (error) {
                alert(error);
                return;
            }
            if (!confirm('Перезапустить скрапер? Текущий файл таблицы будет перезаписан.')) return;

            const btnText = document.getElementById('restart-btn-text');
            const btn = document.getElementById('restart-btn');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Перезапуск...';
            try {
                const res = await fetch('/api/restart', {method: 'POST'});
                const data = await res.json();
                if (!data.ok) alert(data.error || 'Ошибка перезапуска');
                if (btnText) btnText.textContent = data.ok ? 'Перезапущен!' : 'Ошибка';
            } catch {
                if (btnText) btnText.textContent = 'Ошибка';
            }
            setTimeout(() => {
                if (btnText) btnText.textContent = 'Перезапустить';
                if (btn) btn.disabled = false;
            }, 2000);
        }

        async function stopScraper() {
            if (!confirm('Остановить скрапер? Прогресс сохранится.')) return;
            await fetch('/api/stop', {method: 'POST'});
        }

        function renderAccounts(accounts) {
            const container = document.getElementById('accounts-list');
            const summary = document.getElementById('accounts-summary');
            const activeCount = accounts.filter(a => a.valid && !a.sleeping).length;
            const sleepingCount = accounts.filter(a => a.sleeping).length;
            if (summary) {
                let text = `${activeCount} доступных / ${accounts.length}`;
                if (sleepingCount > 0) text += ` (${sleepingCount} спящих)`;
                summary.textContent = text;
            }
            if (!container) return;
            if (accounts.length === 0) {
                container.innerHTML = '<div class="text-muted italic text-sm">Аккаунты не настроены</div>';
                return;
            }
            container.innerHTML = accounts.map((a, idx) => {
                let statusColor, statusText, borderCls, badgeCls;
                if (a.sleeping) {
                    statusColor = 'bg-warn';
                    statusText = 'Спящий';
                    borderCls = 'border-warn/30';
                    badgeCls = 'bg-warn/15 text-warn';
                } else if (a.valid) {
                    statusColor = 'bg-success';
                    statusText = 'Доступен';
                    borderCls = 'border-success/30';
                    badgeCls = 'bg-success/15 text-success';
                } else {
                    statusColor = 'bg-danger';
                    statusText = 'Недоступен';
                    borderCls = 'border-danger/30';
                    badgeCls = 'bg-danger/15 text-danger';
                }
                const errorBlock = a.error ? `<div class="text-xs text-danger truncate mt-1" title="${a.error}">${a.error}</div>` : '';
                return `<div class="bg-bg border ${borderCls} rounded-lg p-3 flex flex-col justify-between">
                    <div class="flex items-start justify-between">
                        <div>
                            <div class="text-sm font-semibold text-text">${a.username || 'Аккаунт'}</div>
                            <div class="text-xs text-muted">ID: ${a.user_id || '—'}</div>
                            <div class="text-xs text-muted font-mono">${a.token_prefix || ''}</div>
                        </div>
                        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${badgeCls}">
                            <span class="w-1.5 h-1.5 rounded-full ${statusColor}"></span>${statusText}
                        </span>
                    </div>
                    ${errorBlock}
                    <div class="flex gap-2 mt-3">
                        <button onclick="checkAccount(${idx})" class="btn-press flex-1 flex items-center justify-center gap-1 bg-border hover:bg-border/80 text-text px-2 py-1.5 rounded-md text-xs font-medium transition-colors">
                            <i data-lucide="refresh-cw" class="w-3 h-3"></i> Проверить
                        </button>
                        <button onclick="removeAccount(${idx})" class="btn-press flex items-center justify-center gap-1 bg-danger/15 hover:bg-danger/25 text-danger px-2 py-1.5 rounded-md text-xs font-medium transition-colors">
                            <i data-lucide="trash-2" class="w-3 h-3"></i>
                        </button>
                    </div>
                </div>`;
            }).join('');
            lucide.createIcons();
        }

        async function addToken() {
            const input = document.getElementById('token-input');
            const raw = input?.value?.trim();
            if (!raw) return;
            const tokens = raw.replace(/\\r/g, '').split(String.fromCharCode(10)).map(t => t.trim()).filter(Boolean);
            if (tokens.length === 0) return;

            const btn = document.getElementById('add-token-btn');
            const btnText = document.getElementById('add-token-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = tokens.length > 1 ? `Добавляю ${tokens.length}...` : 'Добавляю...';

            try {
                const res = await fetch('/api/accounts', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token: raw})
                });
                const data = await res.json();
                if (data.results) {
                    const msg = `Добавлено: ${data.added}, ошибок: ${data.failed}`;
                    if (data.failed > 0) {
                        const errs = data.results.filter(r => !r.ok).map(r => r.token_prefix + ': ' + (r.error || '')).join(String.fromCharCode(10));
                        alert(msg + String.fromCharCode(10, 10) + 'Ошибки:' + String.fromCharCode(10) + errs);
                    }
                } else if (!data.ok) {
                    alert(data.error || 'Ошибка добавления токена');
                }
                input.value = '';
            } catch (err) {
                alert('Ошибка сети');
            } finally {
                if (btnText) btnText.textContent = 'Добавить токены';
                if (btn) btn.disabled = false;
            }
        }

        async function checkAccount(idx) {
            try {
                await fetch(`/api/accounts/${idx}/check`, {method: 'POST'});
            } catch (err) {
                alert('Ошибка сети');
            }
        }

        async function removeAccount(idx) {
            if (!confirm('Удалить этот аккаунт?')) return;
            try {
                await fetch(`/api/accounts/${idx}`, {method: 'DELETE'});
            } catch (err) {
                alert('Ошибка сети');
            }
        }

        async function updateBatchesPerAccount() {
            const val = parseInt(document.getElementById('batches-per-account-input').value);
            if (isNaN(val) || val < 1) return;
            await fetch('/api/batches-per-account', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({batches: val})
            });
        }

        async function startScraper() {
            const btn = document.getElementById('start-btn');
            const btnText = document.getElementById('start-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Запуск...';
            try {
                const res = await fetch('/api/start', {method: 'POST'});
                const data = await res.json();
                if (btnText) btnText.textContent = data.ok ? 'Запущен' : 'Ошибка';
                if (!data.ok) alert(data.error || 'Ошибка запуска');
            } catch {
                if (btnText) btnText.textContent = 'Ошибка';
            }
            setTimeout(() => {
                if (btnText) btnText.textContent = 'Старт';
                if (btn) btn.disabled = false;
            }, 2000);
        }

        function openPhoneModal() {
            document.getElementById('phone-modal')?.classList.remove('hidden');
            lucide.createIcons();
        }

        function showPhoneStep(step) {
            document.getElementById('phone-step-1')?.classList.toggle('hidden', step !== 1);
            document.getElementById('phone-step-2')?.classList.toggle('hidden', step !== 2);
            document.getElementById('phone-step-3')?.classList.toggle('hidden', step !== 3);
        }

        function closePhoneModal() {
            document.getElementById('phone-modal')?.classList.add('hidden');
            cancelPhoneAuth();
        }

        let phonePollTimer = null;

        async function initPhoneAuth() {
            const input = document.getElementById('phone-input');
            const phone = input?.value?.trim();
            if (!/^\\+7\\d{10}$/.test(phone)) {
                showPhoneError('Введите номер в формате +79991234567');
                return;
            }
            const btn = document.getElementById('phone-init-btn');
            const btnText = document.getElementById('phone-init-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Отправка...';
            try {
                const res = await fetch('/api/auth/phone/init', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone})
                });
                const data = await res.json();
                if (!data.ok) {
                    showPhoneError(data.error || 'Ошибка');
                    if (btn) btn.disabled = false;
                    if (btnText) btnText.textContent = 'Получить код';
                    return;
                }
                showPhoneStep(2);
                const frame = document.getElementById('captcha-frame');
                if (frame && data.solver_url) frame.src = data.solver_url;
                phonePollTimer = setInterval(pollPhoneStatus, 1000);
            } catch (err) {
                showPhoneError('Ошибка сети');
                if (btn) btn.disabled = false;
                if (btnText) btnText.textContent = 'Получить код';
            }
        }

        async function pollPhoneStatus() {
            try {
                const res = await fetch('/api/auth/phone/status');
                const data = await res.json();
                if (data.stage === 'code') {
                    clearInterval(phonePollTimer);
                    phonePollTimer = null;
                    showPhoneStep(3);
                } else if (data.stage === 'error') {
                    clearInterval(phonePollTimer);
                    phonePollTimer = null;
                    showPhoneError(data.error || 'Ошибка авторизации');
                }
            } catch {}
        }

        async function restorePhoneAuthState() {
            try {
                const res = await fetch('/api/auth/phone/status');
                const data = await res.json();
                if (data.stage === 'idle' || data.stage === 'error') return;
                openPhoneModal();
                document.getElementById('phone-error')?.classList.add('hidden');
                if (data.phone) {
                    const phoneInput = document.getElementById('phone-input');
                    if (phoneInput) phoneInput.value = data.phone;
                }
                if (data.stage === 'captcha') {
                    showPhoneStep(2);
                    const frame = document.getElementById('captcha-frame');
                    if (frame && data.solver_url) frame.src = data.solver_url;
                    if (!phonePollTimer) phonePollTimer = setInterval(pollPhoneStatus, 1000);
                } else if (data.stage === 'code') {
                    showPhoneStep(3);
                }
            } catch {}
        }

        async function verifyPhoneCode() {
            const input = document.getElementById('phone-code-input');
            const code = input?.value?.trim();
            if (!code) return;
            const btn = document.getElementById('phone-verify-btn');
            const btnText = document.getElementById('phone-verify-btn-text');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Вход...';
            try {
                const res = await fetch('/api/auth/phone/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code})
                });
                const data = await res.json();
                if (data.ok) {
                    closePhoneModal();
                } else {
                    showPhoneError(data.error || 'Ошибка входа');
                    if (btn) btn.disabled = false;
                    if (btnText) btnText.textContent = 'Войти';
                }
            } catch {
                showPhoneError('Ошибка сети');
                if (btn) btn.disabled = false;
                if (btnText) btnText.textContent = 'Войти';
            }
        }

        async function cancelPhoneAuth() {
            if (phonePollTimer) {
                clearInterval(phonePollTimer);
                phonePollTimer = null;
            }
            try {
                await fetch('/api/auth/phone/cancel', {method: 'POST'});
            } catch {}
        }

        function showPhoneError(message) {
            const el = document.getElementById('phone-error');
            if (!el) return;
            el.textContent = message;
            el.classList.remove('hidden');
        }
    </script>
</body>
</html>
"""


async def _handle_index(request: web.Request) -> web.Response:
    return web.Response(text=_DASHBOARD_HTML, content_type="text/html")


async def _handle_ws(request: web.Request) -> web.WebSocketResponse:
    state: ScrapingState = request.app["state"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    state.ws_clients.add(ws)
    logger.info("WebSocket client connected: %s", request.remote)

    await ws.send_json({"type": "state", "data": state.to_dict()})

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                pass
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("WS error: %s", ws.exception())
    finally:
        state.ws_clients.discard(ws)
        logger.info("WebSocket client disconnected: %s", request.remote)

    return ws


async def _handle_cooldown(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        seconds = float(data.get("seconds", 10))
        state.cooldown_seconds = max(0, seconds)
        account_manager.save(state)
        logger.info("Cooldown updated to %.1f seconds", state.cooldown_seconds)
        await _broadcast_state(state)
        return web.json_response({"ok": True, "cooldown": state.cooldown_seconds})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def _handle_reconnect_cooldown(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        seconds = float(data.get("seconds", 20))
        state.reconnect_cooldown_seconds = max(0, seconds)
        account_manager.save(state)
        logger.info("Reconnect cooldown updated to %.1f seconds", state.reconnect_cooldown_seconds)
        await _broadcast_state(state)
        return web.json_response({"ok": True, "reconnect_cooldown": state.reconnect_cooldown_seconds})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def _handle_reinit_cooldown(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        seconds = float(data.get("seconds", 5))
        state.reinit_cooldown_seconds = max(0, seconds)
        account_manager.save(state)
        logger.info("Reinit cooldown updated to %.1f seconds", state.reinit_cooldown_seconds)
        await _broadcast_state(state)
        return web.json_response({"ok": True, "reinit_cooldown": state.reinit_cooldown_seconds})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def _handle_cooldowns(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        cooldown = data.get("cooldown_seconds")
        reconnect = data.get("reconnect_cooldown_seconds")
        reinit = data.get("reinit_cooldown_seconds")
        if cooldown is not None:
            state.cooldown_seconds = max(0, float(cooldown))
        if reconnect is not None:
            state.reconnect_cooldown_seconds = max(0, float(reconnect))
        if reinit is not None:
            state.reinit_cooldown_seconds = max(0, float(reinit))
        account_manager.save(state)
        logger.info(
            "Cooldowns updated: batch=%.1f reconnect=%.1f reinit=%.1f",
            state.cooldown_seconds,
            state.reconnect_cooldown_seconds,
            state.reinit_cooldown_seconds,
        )
        await _broadcast_state(state)
        return web.json_response({
            "ok": True,
            "cooldown_seconds": state.cooldown_seconds,
            "reconnect_cooldown_seconds": state.reconnect_cooldown_seconds,
            "reinit_cooldown_seconds": state.reinit_cooldown_seconds,
        })
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def _handle_pause(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    state.is_paused = not state.is_paused
    logger.info("Scraper %s", "paused" if state.is_paused else "resumed")
    await _broadcast_state(state)
    return web.json_response({"ok": True, "paused": state.is_paused})


_SOCKS_SCHEME_RE = re.compile(r"^(socks4|socks5|socks5h)://", re.IGNORECASE)


def _parse_socks_url(url: str) -> tuple[str, int]:
    """Extract host and port from a SOCKS URL for TCP ping."""
    parsed = urlsplit(url)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        raise ValueError("Не удалось определить хост или порт прокси")
    return host, port


async def _ping_proxy_host(proxy_url: str, timeout: float = 10.0) -> tuple[bool, float, Optional[str]]:
    """TCP-connect to the proxy host:port and return (ok, ms, error)."""
    try:
        host, port = _parse_socks_url(proxy_url)
        start = time.time()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        elapsed = (time.time() - start) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, round(elapsed, 1), None
    except Exception as exc:
        return False, 0.0, f"Прокси недоступен: {exc}"


async def _ping_ws_through_proxy(proxy_url: str, timeout: float = 20.0) -> tuple[bool, float, Optional[str]]:
    """Connect to oneme WS through the proxy and return (ok, ms, error)."""
    cl = Tuiclient()
    cl.proxy = proxy_url
    start = time.time()
    try:
        await asyncio.wait_for(cl._netw_connect(), timeout=timeout)
        elapsed = (time.time() - start) * 1000
        try:
            await cl.disconnect()
        except Exception:
            pass
        return True, round(elapsed, 1), None
    except Exception as exc:
        return False, 0.0, f"WS через прокси недоступен: {exc}"


async def _handle_proxy_get(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    return web.json_response({
        "ok": True,
        "proxy_url": state.proxy_url,
        "proxy_enabled": state.proxy_enabled,
    })


async def _handle_proxy_test(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        proxy_url = (data.get("proxy_url") or "").strip()
        if not proxy_url:
            return web.json_response({"ok": False, "error": "Введите URL прокси"}, status=400)
        if not _SOCKS_SCHEME_RE.match(proxy_url):
            return web.json_response(
                {"ok": False, "error": "URL должен начинаться с socks4://, socks5:// или socks5h://"},
                status=400,
            )

        proxy_ok, proxy_ms, proxy_error = await _ping_proxy_host(proxy_url)
        if not proxy_ok:
            return web.json_response({"ok": False, "proxy_ping_ms": None, "ws_ping_ms": None, "error": proxy_error})

        ws_ok, ws_ms, ws_error = await _ping_ws_through_proxy(proxy_url)
        if not ws_ok:
            return web.json_response({"ok": False, "proxy_ping_ms": proxy_ms, "ws_ping_ms": None, "error": ws_error})

        return web.json_response({
            "ok": True,
            "proxy_ping_ms": proxy_ms,
            "ws_ping_ms": ws_ms,
        })
    except Exception as exc:
        logger.exception("Proxy test failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_proxy_set(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        enabled = bool(data.get("proxy_enabled", False))
        url = (data.get("proxy_url") or "").strip()

        if enabled:
            if not url:
                return web.json_response({"ok": False, "error": "Введите URL прокси"}, status=400)
            if not _SOCKS_SCHEME_RE.match(url):
                return web.json_response(
                    {"ok": False, "error": "URL должен начинаться с socks4://, socks5:// или socks5h://"},
                    status=400,
                )

        state.proxy_enabled = enabled
        state.proxy_url = url if enabled else None
        account_manager.proxy_url = state.proxy_url
        account_manager.save(state)
        logger.info("Proxy updated: enabled=%s url=%s", enabled, state.proxy_url)
        await _broadcast_state(state)
        return web.json_response({
            "ok": True,
            "proxy_enabled": state.proxy_enabled,
            "proxy_url": state.proxy_url,
        })
    except Exception as exc:
        logger.exception("Proxy set failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_save(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    if state.is_saving:
        return web.json_response({"ok": False, "error": "Save already in progress"}, status=409)

    state.is_saving = True
    await _broadcast_state(state)

    try:
        path = Path(state.save_path)
        logger.info("Manual save requested: path=%s exists=%s users_scraped=%s", path, path.exists(), state.users_scraped)
        if not path.exists():
            logger.error("Manual save failed: workbook not found at %s", path)
            return web.json_response({"ok": False, "error": f"Workbook not found: {path.name}"}, status=404)

        wb = openpyxl.load_workbook(path)
        ws = wb.active
        if ws is None:
            return web.json_response({"ok": False, "error": "No active worksheet"}, status=500)

        _auto_size_columns(ws)
        wb.save(path)
        logger.info("Manual save successful: %s", path)
        return web.json_response({"ok": True, "path": str(path)})
    except Exception as exc:
        logger.exception("Manual save failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    finally:
        state.is_saving = False
        await _broadcast_state(state)


async def _handle_stop(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    manager: ScraperManager = request.app["manager"]
    account_manager: AccountManager = request.app["account_manager"]
    state.is_running = False
    logger.info("Stop signal received")
    await manager.stop()
    await account_manager.cleanup()
    state.current_account_index = -1
    state.current_account_username = None
    await _broadcast_state(state)
    return web.json_response({"ok": True})


async def _handle_range(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        id_min = int(data.get("id_min", state.id_min))
        id_max = int(data.get("id_max", state.id_max))
        id_step = int(data.get("id_step", state.id_step))

        if id_min < 1 or id_max < 1 or id_step < 1:
            return web.json_response({"ok": False, "error": "Значения должны быть положительными числами"}, status=400)
        if id_min >= id_max:
            return web.json_response({"ok": False, "error": "ID от должно быть меньше ID до"}, status=400)
        if id_step > (id_max - id_min):
            return web.json_response({"ok": False, "error": "Шаг не может быть больше диапазона"}, status=400)

        state.set_range(id_min, id_max, id_step)
        account_manager.save(state)
        logger.info("Range updated to %d-%d step %d", id_min, id_max, id_step)
        await _broadcast_state(state)
        return web.json_response({
            "ok": True,
            "range": {"id_min": id_min, "id_max": id_max, "id_step": id_step},
        })
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def _handle_restart(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    manager: ScraperManager = request.app["manager"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        if account_manager.valid_count() == 0:
            return web.json_response({"ok": False, "error": "Нет доступных аккаунтов. Добавьте и проверьте аккаунт."}, status=400)

        state.set_range(state.id_min, state.id_max, state.id_step)
        account_manager.save(state)
        logger.info("Restarting scraper with range %d-%d step %d", state.id_min, state.id_max, state.id_step)
        await _broadcast_state(state)

        started = await manager.restart()
        return web.json_response({"ok": started})
    except Exception as exc:
        logger.error("Restart failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_start(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    manager: ScraperManager = request.app["manager"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        if account_manager.valid_count() == 0:
            return web.json_response({"ok": False, "error": "Нет доступных аккаунтов. Добавьте и проверьте аккаунт."}, status=400)
        started = await manager.start()
        return web.json_response({"ok": started})
    except Exception as exc:
        logger.error("Start failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_download(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    path = Path(state.excel_path)
    if not path.exists():
        return web.json_response({"ok": False, "error": "File not found"}, status=404)

    try:
        data = path.read_bytes()
        return web.Response(
            body=data,
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Content-Disposition": f'attachment; filename="{path.name}"',
                "Content-Length": str(len(data)),
            },
        )
    except Exception as exc:
        logger.error("Download failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_accounts(request: web.Request) -> web.Response:
    account_manager: AccountManager = request.app["account_manager"]
    return web.json_response({"ok": True, "accounts": account_manager.to_dict()})


async def _handle_add_token(request: web.Request) -> web.Response:
    """Add one or many tokens. Accepts:
    - {"token": "single"}
    - {"tokens": ["t1", "t2", ...]}
    - {"token": "line1\\nline2\\n..."}  (multiline string)
    """
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()

        tokens: List[str] = []
        if "tokens" in data and isinstance(data["tokens"], list):
            tokens = [str(t).strip() for t in data["tokens"] if str(t).strip()]
        else:
            raw = data.get("token", "")
            if isinstance(raw, str):
                # Support multiline paste
                tokens = [line.strip() for line in raw.splitlines() if line.strip()]
            elif raw:
                tokens = [str(raw).strip()]

        if not tokens:
            return web.json_response({"ok": False, "error": "Empty token(s)"}, status=400)

        results: List[Dict[str, Any]] = []
        added = 0
        failed = 0

        for token in tokens:
            account = await account_manager.validate_token(token)
            if account.valid:
                account.sleeping = False
                await account_manager.add_account(account)
                added += 1
                results.append({"ok": True, "account": account.to_dict()})
            else:
                failed += 1
                results.append({
                    "ok": False,
                    "error": account.error or "Token validation failed",
                    "token_prefix": _mask_token(token),
                    "account": account.to_dict(),
                })

        account_manager.save(state)
        await _broadcast_state(state)

        # Single-token backward compatible response
        if len(tokens) == 1:
            r = results[0]
            if r["ok"]:
                return web.json_response({"ok": True, "account": r["account"]})
            return web.json_response(
                {"ok": False, "error": r.get("error"), "account": r.get("account")},
                status=400,
            )

        return web.json_response({
            "ok": failed == 0,
            "added": added,
            "failed": failed,
            "total": len(tokens),
            "results": results,
        })
    except Exception as exc:
        logger.exception("Add token(s) failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_check_account(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        idx = int(request.match_info["idx"])
        if not 0 <= idx < len(account_manager.accounts):
            return web.json_response({"ok": False, "error": "Account not found"}, status=404)
        account = await account_manager.validate_token(account_manager.accounts[idx].token)
        if account.valid:
            account.sleeping = False  # wake up on successful re-check
        await account_manager.add_account(account)
        account_manager.save(state)
        await _broadcast_state(state)
        return web.json_response({"ok": account.valid, "account": account.to_dict(), "error": account.error})
    except Exception as exc:
        logger.exception("Check account failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_remove_account(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        idx = int(request.match_info["idx"])
        removed = await account_manager.remove_account(idx)
        if not removed:
            return web.json_response({"ok": False, "error": "Account not found"}, status=404)
        account_manager.save(state)
        await _broadcast_state(state)
        return web.json_response({"ok": True})
    except Exception as exc:
        logger.exception("Remove account failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_batches_per_account(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        batches = int(data.get("batches", state.batches_per_account))
        if batches < 1:
            return web.json_response({"ok": False, "error": "Must be at least 1"}, status=400)
        state.batches_per_account = batches
        account_manager.save(state)
        await _broadcast_state(state)
        return web.json_response({"ok": True, "batches_per_account": state.batches_per_account})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def _phone_auth_waiter(account_manager: AccountManager, state: ScrapingState) -> None:
    pending = account_manager.get_pending_phone_auth()
    if not pending:
        return
    future = pending.get("future")
    if not future:
        return
    try:
        token = await asyncio.wait_for(future, timeout=300)
    except asyncio.TimeoutError:
        await account_manager.stop_captcha_solver()
        account_manager.set_pending_phone_auth({
            "phone": pending.get("phone"),
            "stage": "error",
            "auth_token": None,
            "error": "Капча не решена за 5 минут",
        })
        await _broadcast_state(state)
        return
    except Exception as exc:
        await account_manager.stop_captcha_solver()
        account_manager.set_pending_phone_auth({
            "phone": pending.get("phone"),
            "stage": "error",
            "auth_token": None,
            "error": str(exc),
        })
        await _broadcast_state(state)
        return

    await account_manager.stop_captcha_solver()
    cl = Tuiclient()
    cl.proxy = account_manager.proxy_url
    try:
        await cl._netw_connect()
        auth_token = await cl.send_verify_code(pending["phone"], token)
        account_manager.set_pending_phone_auth({
            "phone": pending["phone"],
            "stage": "code",
            "auth_token": auth_token,
            "error": None,
        })
        state.add_reinit_log(f"Код отправлен на {pending['phone']}")
    except Exception as exc:
        account_manager.set_pending_phone_auth({
            "phone": pending.get("phone"),
            "stage": "error",
            "auth_token": None,
            "error": str(exc),
        })
    finally:
        try:
            await cl.disconnect()
        except Exception:
            pass
    await _broadcast_state(state)


async def _handle_phone_init(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        phone = data.get("phone", "").strip()
        if not re.match(r"^\+7\d{10}$", phone):
            return web.json_response({"ok": False, "error": "Номер должен быть в формате +79991234567"}, status=400)
        if account_manager.get_pending_phone_auth():
            return web.json_response({"ok": False, "error": "Уже идёт процесс входа"}, status=409)

        cl = Tuiclient()
        cl.proxy = account_manager.proxy_url
        captcha_url = ""
        try:
            await cl._netw_connect()
            captcha_url = await cl.get_captcha_url(phone)
        except Exception as exc:
            return web.json_response({"ok": False, "error": f"Ошибка капчи: {exc}"}, status=500)
        finally:
            try:
                await cl.disconnect()
            except Exception:
                pass

        solver_url = await account_manager.start_captcha_solver(captcha_url)
        pending: Dict[str, Any] = {
            "phone": phone,
            "stage": "captcha",
            "auth_token": None,
            "error": None,
            "solver_url": solver_url,
            "future": account_manager._captcha_token_future,
        }
        account_manager.set_pending_phone_auth(pending)
        asyncio.create_task(_phone_auth_waiter(account_manager, state))
        await _broadcast_state(state)
        return web.json_response({"ok": True, "solver_url": solver_url, "stage": "captcha"})
    except Exception as exc:
        logger.exception("Phone init failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_phone_status(request: web.Request) -> web.Response:
    account_manager: AccountManager = request.app["account_manager"]
    pending = account_manager.get_pending_phone_auth()
    if not pending:
        return web.json_response({"ok": True, "stage": "idle"})
    return web.json_response({
        "ok": True,
        "stage": pending.get("stage"),
        "phone": pending.get("phone"),
        "error": pending.get("error"),
        "solver_url": pending.get("solver_url"),
        "auth_token": bool(pending.get("auth_token")),
    })


async def _handle_phone_verify(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        code = data.get("code", "").strip()
        pending = account_manager.get_pending_phone_auth()
        if not pending or pending.get("stage") != "code" or not pending.get("auth_token"):
            return web.json_response({"ok": False, "error": "Сначала запросите код"}, status=400)

        cl: Optional[Tuiclient] = None
        try:
            # Step 1: exchange SMS code for login token
            cl = Tuiclient()
            if account_manager.proxy_url:
                cl.proxy = account_manager.proxy_url
            await cl._netw_connect()
            login_token = await cl.check_verify_code(pending["auth_token"], code)
            await cl.disconnect()
            cl = None

            # Step 2: full login with the received token (same flow as token paste)
            cl = await _login_with_token(login_token, account_manager.proxy_url)
            profile = cl.profile
            account = Account(
                token=login_token,
                username=profile.get_name(),
                user_id=profile.id,
                valid=True,
                sleeping=False,
                profile=profile,
            )
            await account_manager.add_account(account)
            account_manager.save(state)
            account_manager.set_pending_phone_auth(None)
            await _broadcast_state(state)
            return web.json_response({"ok": True, "account": account.to_dict()})
        finally:
            if cl is not None:
                try:
                    await cl.disconnect()
                except Exception:
                    pass
    except Exception as exc:
        logger.exception("Phone verify failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_phone_cancel(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        await account_manager.stop_captcha_solver()
        account_manager.set_pending_phone_auth(None)
        await _broadcast_state(state)
        return web.json_response({"ok": True})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


def create_app(state: ScrapingState) -> web.Application:
    app = web.Application()
    app["state"] = state

    app.router.add_get("/", _handle_index)
    app.router.add_get("/ws", _handle_ws)
    app.router.add_post("/api/cooldown", _handle_cooldown)
    app.router.add_post("/api/reconnect-cooldown", _handle_reconnect_cooldown)
    app.router.add_post("/api/reinit-cooldown", _handle_reinit_cooldown)
    app.router.add_post("/api/cooldowns", _handle_cooldowns)
    app.router.add_post("/api/batches-per-account", _handle_batches_per_account)
    app.router.add_post("/api/pause", _handle_pause)
    app.router.add_post("/api/save", _handle_save)
    app.router.add_post("/api/stop", _handle_stop)
    app.router.add_post("/api/start", _handle_start)
    app.router.add_post("/api/range", _handle_range)
    app.router.add_post("/api/restart", _handle_restart)
    app.router.add_get("/api/download", _handle_download)
    app.router.add_get("/api/proxy", _handle_proxy_get)
    app.router.add_post("/api/proxy/test", _handle_proxy_test)
    app.router.add_post("/api/proxy", _handle_proxy_set)
    app.router.add_get("/api/accounts", _handle_accounts)
    app.router.add_post("/api/accounts", _handle_add_token)
    app.router.add_post("/api/accounts/{idx}/check", _handle_check_account)
    app.router.add_delete("/api/accounts/{idx}", _handle_remove_account)
    app.router.add_post("/api/auth/phone/init", _handle_phone_init)
    app.router.add_get("/api/auth/phone/status", _handle_phone_status)
    app.router.add_post("/api/auth/phone/verify", _handle_phone_verify)
    app.router.add_post("/api/auth/phone/cancel", _handle_phone_cancel)

    return app


async def _periodic_broadcast(state: ScrapingState) -> None:
    while True:
        await asyncio.sleep(0.1)
        if state.is_running:
            await _broadcast_state(state)


async def main() -> None:
    accounts_path = Path(__file__).resolve().parent.parent / "accounts.json"
    account_manager = AccountManager(accounts_path)

    state = ScrapingState(
        id_min=account_manager.loaded_settings.get("id_min", 9_950_000),
        id_max=account_manager.loaded_settings.get("id_max", 15_000_000),
        id_step=account_manager.loaded_settings.get("id_step", 1000),
        cooldown_seconds=account_manager.loaded_settings.get("cooldown_seconds", 20.0),
        reconnect_cooldown_seconds=account_manager.loaded_settings.get("reconnect_cooldown_seconds", 20.0),
        reinit_cooldown_seconds=account_manager.loaded_settings.get("reinit_cooldown_seconds", 5.0),
        batches_per_account=account_manager.loaded_settings.get("batches_per_account", 3),
        proxy_url=account_manager.loaded_settings.get("proxy_url"),
        proxy_enabled=account_manager.loaded_settings.get("proxy_enabled", False),
    )
    state.account_manager = account_manager
    account_manager.proxy_url = state.proxy_url if state.proxy_enabled else None

    app = create_app(state)

    manager = ScraperManager(state)
    app["manager"] = manager
    app["account_manager"] = account_manager

    port = int(os.environ.get("DASHBOARD_PORT", "8081"))
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")

    broadcast_task = asyncio.create_task(_periodic_broadcast(state))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info("🚀 Dashboard running at http://%s:%d", host, port)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        broadcast_task.cancel()
        await account_manager.cleanup()
        await manager.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
