from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import time
from dataclasses import dataclass, field
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

import captcha


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)



@dataclass
class ScrapingState:
    current_id: int = 0
    id_min: int = 9_900_000
    id_max: int = 15_000_000
    id_step: int = 1000
    total_ids: int = field(init=False)

    cooldown_seconds: float = 10.0
    reconnect_cooldown_seconds: float = 20.0
    batches_per_account: int = 5
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
        """Set file name based on actual first/last user IDs written to the sheet."""
        first = self.first_user_id
        last = self.last_user_id
        if first is None or last is None:
            first = self.id_min
            last = self.id_max
        self.excel_path = f"users_table_{first}_{last}.xlsx"

    @property
    def save_path(self) -> str:
        """Return the fixed path used for saving the workbook during the run."""
        if self._save_path is None:
            self._save_path = self.excel_path
        return self._save_path

    def reset_save_path(self) -> None:
        """Reset save path so the next run picks up the new excel_path."""
        self._save_path = None

    def set_range(self, id_min: int, id_max: int, id_step: int) -> None:
        """Update scanning range, recalculate progress and target file name."""
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
        """Set current action with optional duration estimate for progress bar."""
        self.current_action = action
        self.action_started_at = time.time()
        self.action_duration_estimate = max(duration_estimate, 0.001)
        self.action_progress = 0.0
        self.action_detail = detail

    def update_action_progress(self) -> None:
        """Update action_progress based on elapsed time vs estimate."""
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
        """ETA based on average batch duration."""
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
            "batches_per_account": self.batches_per_account,
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
    error: Optional[str] = None
    profile: Optional[UserProfile] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_prefix": _mask_token(self.token),
            "username": self.username or "—",
            "user_id": self.user_id,
            "valid": self.valid,
            "error": self.error,
        }


class AccountManager:
    """Stores, validates and rotates scraping accounts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.accounts: List[Account] = []
        self._lock = asyncio.Lock()
        self._pending_phone_auth: Optional[Dict[str, Any]] = None
        self._captcha_server: Optional[uvicorn.Server] = None
        self._captcha_server_task: Optional[asyncio.Task] = None
        self._captcha_token_future: Optional[asyncio.Future] = None
        self.loaded_settings: Dict[str, Any] = {}
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
                    error=a.get("error"),
                )
                for a in data.get("accounts", [])
            ]
            self.loaded_settings = {
                key: data[key]
                for key in ("cooldown_seconds", "reconnect_cooldown_seconds", "batches_per_account")
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
                    "error": a.error,
                }
                for a in self.accounts
            ],
            "cooldown_seconds": state.cooldown_seconds,
            "reconnect_cooldown_seconds": state.reconnect_cooldown_seconds,
            "batches_per_account": state.batches_per_account,
        }
        try:
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save accounts: %s", exc)

    def to_dict(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self.accounts]

    def valid_count(self) -> int:
        return sum(1 for a in self.accounts if a.valid)

    def next_valid_index(self, start: int) -> Optional[int]:
        n = len(self.accounts)
        if n == 0:
            return None
        for offset in range(1, n + 1):
            idx = (start + offset) % n
            if self.accounts[idx].valid:
                return idx
        return None

    async def validate_token(self, token: str) -> Account:
        cl = Tuiclient()
        try:
            await cl._netw_connect()
            cl.token = token
            await cl.finalise_auth()
            profile = cl.profile
            return Account(
                token=token,
                username=profile.get_name(),
                user_id=profile.id,
                valid=True,
                profile=profile,
            )
        except Exception as exc:
            logger.exception("Token validation failed")
            return Account(token=token, valid=False, error=str(exc))
        finally:
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
    """Handles scraper task lifecycle: start, stop and restart with new settings."""

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
    """Wrap scraper to log crashes and broadcast the error state."""
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
    """Wait for `duration` seconds while running and not paused. Return False if stopped/paused."""
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
    init_log_done = False

    async def close_active() -> None:
        nonlocal active_client
        if active_client is not None:
            try:
                await active_client.disconnect()
            except Exception:
                pass
            active_client = None

    def force_switch() -> None:
        nonlocal batches_on_current
        batches_on_current = state.batches_per_account

    async def activate_next_account() -> bool:
        nonlocal active_client, active_account_index, batches_on_current, init_log_done
        await close_active()
        if active_account_index != -1:
            if not await _wait_while_running(
                state, state.reconnect_cooldown_seconds, "reinit", "Переключение аккаунта"
            ):
                return False

        next_idx = manager.next_valid_index(active_account_index)
        if next_idx is None:
            state.last_error = "Нет доступных аккаунтов для продолжения"
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

        cl = Tuiclient()
        if not init_log_done:
            await cl._init_log()
            init_log_done = True
        try:
            await cl._netw_connect()
            cl.token = account.token
            await cl.finalise_auth()
            account.profile = cl.profile
            account.user_id = cl.profile.id
            account.username = cl.profile.get_name()
            account.valid = True
            account.error = None
            active_client = cl
            manager.save(state)
            state.add_reinit_log(f"Аккаунт {account.username} ({account.user_id}) активен")
            await _broadcast_state(state)
            return True
        except Exception as exc:
            logger.exception("Account login failed")
            account.valid = False
            account.error = str(exc)
            manager.save(state)
            state.last_error = f"Ошибка входа в аккаунт {account.username or account.user_id}: {exc}"
            state.add_reinit_log(f"Ошибка входа: {exc}")
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
                ok = await activate_next_account()
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
                state.add_reinit_log(f"Ошибка батча: {exc}")
                await close_active()
                force_switch()
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
                <span id="current-id">0</span>
                <span id="id-max">10,000,000</span>
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

            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
                <div class="flex items-center gap-2">
                    <input type="text" id="token-input" placeholder="Вставьте токен"
                           class="flex-1 bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors">
                    <button onclick="addToken()" class="btn-press flex items-center gap-2 bg-success hover:bg-success/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="key" class="w-4 h-4"></i>
                        Добавить токен
                    </button>
                </div>
                <button onclick="openPhoneModal()" class="btn-press flex items-center justify-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="smartphone" class="w-4 h-4"></i>
                    Войти по номеру
                </button>
            </div>
        </div>

        <div class="bg-card border border-border rounded-xl p-5 md:p-6 mb-5 card-hover">
            <div class="flex items-center gap-2 mb-4">
                <i data-lucide="sliders-horizontal" class="w-5 h-5 text-muted"></i>
                <span class="text-sm font-medium uppercase tracking-wider text-muted">Управление</span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
                <div>
                    <label class="block text-xs text-muted mb-1">ID от</label>
                    <input type="number" id="id-min" value="9950000" min="0" step="1"
                           class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                </div>
                <div>
                    <label class="block text-xs text-muted mb-1">ID до</label>
                    <input type="number" id="id-max" value="15000000" min="0" step="1"
                           class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                </div>
                <div>
                    <label class="block text-xs text-muted mb-1">Шаг батча</label>
                    <input type="number" id="id-step" value="1000" min="1" step="1"
                           class="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors font-mono-nums">
                </div>
                <div class="flex items-end">
                    <button onclick="restartScraper()" id="restart-btn" class="btn-press w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                        <span id="restart-btn-text">Задать диапазон и перезапустить</span>
                    </button>
                </div>
            </div>

            <div class="flex flex-wrap items-center gap-3 border-t border-border pt-4">
                <div class="flex items-center gap-2">
                    <label class="text-sm text-muted">Кулдаун (сек):</label>
                    <input type="number" id="cooldown-input" value="10" min="0" step="0.5"
                           class="w-24 bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors">
                </div>
                <button onclick="updateCooldown()" class="btn-press flex items-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="check" class="w-4 h-4"></i>
                    Применить
                </button>

                <div class="flex items-center gap-2">
                    <label class="text-sm text-muted">Кулдаун переподключения (сек):</label>
                    <input type="number" id="reconnect-cooldown-input" value="20" min="0" step="0.5"
                           class="w-24 bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors">
                </div>
                <button onclick="updateReconnectCooldown()" class="btn-press flex items-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="check" class="w-4 h-4"></i>
                    Применить
                </button>

                <div class="flex items-center gap-2">
                    <label class="text-sm text-muted">Батчей с аккаунта:</label>
                    <input type="number" id="batches-per-account-input" value="5" min="1" step="1"
                           class="w-24 bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors">
                </div>
                <button onclick="updateBatchesPerAccount()" class="btn-press flex items-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="check" class="w-4 h-4"></i>
                    Применить
                </button>

                <button onclick="startScraper()" id="start-btn" class="btn-press flex items-center gap-2 bg-accent hover:bg-accent/80 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="play" class="w-4 h-4"></i>
                    <span id="start-btn-text">Старт</span>
                </button>
                <button onclick="togglePause()" id="pause-btn" class="btn-press flex items-center gap-2 bg-border hover:bg-border/80 text-text px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="pause" class="w-4 h-4"></i>
                    <span>Пауза</span>
                </button>
                <button onclick="saveNow()" class="btn-press flex items-center gap-2 bg-success hover:bg-success/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="save" class="w-4 h-4"></i>
                    <span id="save-btn-text">Сохранить сейчас</span>
                </button>
                <a href="/api/download" download class="btn-press flex items-center gap-2 bg-purple hover:bg-purple/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors no-underline">
                    <i data-lucide="download" class="w-4 h-4"></i>
                    Скачать таблицу
                </a>
                <button onclick="stopScraper()" class="btn-press flex items-center gap-2 bg-danger hover:bg-danger/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <i data-lucide="square" class="w-4 h-4"></i>
                    Стоп
                </button>
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

                const currentIdEl = document.getElementById('current-id');
                const idMaxEl = document.getElementById('id-max');
                if (currentIdEl) currentIdEl.textContent = fmtNum(d.current_id || 0);
                if (idMaxEl) idMaxEl.textContent = fmtNum(d.id_max || 0);

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
                const validAccounts = (d.accounts || []).filter(a => a.valid).length;
                const startBtn = document.getElementById('start-btn');
                if (startBtn) startBtn.disabled = validAccounts === 0 || d.is_running;
                const restartBtn = document.getElementById('restart-btn');
                if (restartBtn) restartBtn.disabled = validAccounts === 0;

                const batchesInput = document.getElementById('batches-per-account-input');
                if (batchesInput && !batchesInput.matches(':focus')) batchesInput.value = d.batches_per_account || 5;

                const idMinEl = document.getElementById('id-min');
                const idMaxEl2 = document.getElementById('id-max');
                const idStepEl = document.getElementById('id-step');
                if (idMinEl && !idMinEl.matches(':focus')) idMinEl.value = d.id_min || 0;
                if (idMaxEl2 && !idMaxEl2.matches(':focus')) idMaxEl2.value = d.id_max || 0;
                if (idStepEl && !idStepEl.matches(':focus')) idStepEl.value = d.id_step || 1000;

                const cooldownInput = document.getElementById('cooldown-input');
                const reconnectInput = document.getElementById('reconnect-cooldown-input');
                if (cooldownInput && !cooldownInput.matches(':focus')) cooldownInput.value = d.cooldown_seconds || 10;
                if (reconnectInput && !reconnectInput.matches(':focus')) reconnectInput.value = d.reconnect_cooldown_seconds || 20;

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

        async function updateCooldown() {
            const val = parseFloat(document.getElementById('cooldown-input').value);
            if (isNaN(val) || val < 0) return;
            await fetch('/api/cooldown', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({seconds: val})
            });
        }

        async function updateReconnectCooldown() {
            const val = parseFloat(document.getElementById('reconnect-cooldown-input').value);
            if (isNaN(val) || val < 0) return;
            await fetch('/api/reconnect-cooldown', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({seconds: val})
            });
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

        async function restartScraper() {
            const id_min = parseInt(document.getElementById('id-min').value);
            const id_max = parseInt(document.getElementById('id-max').value);
            const id_step = parseInt(document.getElementById('id-step').value);
            if (isNaN(id_min) || isNaN(id_max) || isNaN(id_step) || id_min >= id_max || id_step <= 0) {
                alert('Некорректный диапазон: ID от должно быть меньше ID до, шаг батча больше 0.');
                return;
            }
            if (!confirm('Перезапустить скрапер с новым диапазоном? Текущий файл таблицы будет перезаписан.')) return;

            const btnText = document.getElementById('restart-btn-text');
            const btn = document.getElementById('restart-btn');
            if (btn) btn.disabled = true;
            if (btnText) btnText.textContent = 'Перезапуск...';
            try {
                const res = await fetch('/api/restart', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id_min, id_max, id_step})
                });
                const data = await res.json();
                if (btnText) btnText.textContent = data.ok ? 'Перезапущен!' : 'Ошибка';
            } catch {
                if (btnText) btnText.textContent = 'Ошибка';
            }
            setTimeout(() => {
                if (btnText) btnText.textContent = 'Задать диапазон и перезапустить';
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
            const validCount = accounts.filter(a => a.valid).length;
            if (summary) summary.textContent = `${validCount} доступных / ${accounts.length}`;
            if (!container) return;
            if (accounts.length === 0) {
                container.innerHTML = '<div class="text-muted italic text-sm">Аккаунты не настроены</div>';
                return;
            }
            container.innerHTML = accounts.map((a, idx) => {
                const statusColor = a.valid ? 'bg-success' : 'bg-danger';
                const statusText = a.valid ? 'Доступен' : 'Недоступен';
                const errorBlock = a.error ? `<div class="text-xs text-danger truncate mt-1" title="${a.error}">${a.error}</div>` : '';
                return `<div class="bg-bg border ${a.valid ? 'border-success/30' : 'border-danger/30'} rounded-lg p-3 flex flex-col justify-between">
                    <div class="flex items-start justify-between">
                        <div>
                            <div class="text-sm font-semibold text-text">${a.username || 'Аккаунт'}</div>
                            <div class="text-xs text-muted">ID: ${a.user_id || '—'}</div>
                            <div class="text-xs text-muted font-mono">${a.token_prefix || ''}</div>
                        </div>
                        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${a.valid ? 'bg-success/15 text-success' : 'bg-danger/15 text-danger'}">
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
            const token = input?.value?.trim();
            if (!token) return;
            input.value = '';
            try {
                const res = await fetch('/api/accounts', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token})
                });
                const data = await res.json();
                if (!data.ok) alert(data.error || 'Ошибка добавления токена');
            } catch (err) {
                alert('Ошибка сети');
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
            document.getElementById('phone-step-1')?.classList.remove('hidden');
            document.getElementById('phone-step-2')?.classList.add('hidden');
            document.getElementById('phone-step-3')?.classList.add('hidden');
            document.getElementById('phone-error')?.classList.add('hidden');
            document.getElementById('captcha-frame')?.removeAttribute('src');
            lucide.createIcons();
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
                document.getElementById('phone-step-1')?.classList.add('hidden');
                document.getElementById('phone-step-2')?.classList.remove('hidden');
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
                    document.getElementById('phone-step-2')?.classList.add('hidden');
                    document.getElementById('phone-step-3')?.classList.remove('hidden');
                } else if (data.stage === 'error') {
                    clearInterval(phonePollTimer);
                    phonePollTimer = null;
                    showPhoneError(data.error || 'Ошибка авторизации');
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


async def _handle_pause(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    state.is_paused = not state.is_paused
    logger.info("Scraper %s", "paused" if state.is_paused else "resumed")
    await _broadcast_state(state)
    return web.json_response({"ok": True, "paused": state.is_paused})


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
    try:
        data = await request.json()
        id_min = int(data.get("id_min", state.id_min))
        id_max = int(data.get("id_max", state.id_max))
        id_step = int(data.get("id_step", state.id_step))

        if id_min >= id_max or id_step <= 0:
            return web.json_response({"ok": False, "error": "Invalid range"}, status=400)

        state.set_range(id_min, id_max, id_step)
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
        data = await request.json()
        id_min = int(data.get("id_min", state.id_min))
        id_max = int(data.get("id_max", state.id_max))
        id_step = int(data.get("id_step", state.id_step))

        if id_min >= id_max or id_step <= 0:
            return web.json_response({"ok": False, "error": "Invalid range"}, status=400)

        if account_manager.valid_count() == 0:
            return web.json_response({"ok": False, "error": "Нет доступных аккаунтов. Добавьте и проверьте аккаунт."}, status=400)

        state.set_range(id_min, id_max, id_step)
        account_manager.save(state)
        logger.info("Restarting scraper with range %d-%d step %d", id_min, id_max, id_step)
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
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        data = await request.json()
        token = data.get("token", "").strip()
        if not token:
            return web.json_response({"ok": False, "error": "Empty token"}, status=400)
        account = await account_manager.validate_token(token)
        if not account.valid:
            return web.json_response({"ok": False, "error": account.error or "Token validation failed", "account": account.to_dict()}, status=400)
        await account_manager.add_account(account)
        account_manager.save(state)
        await _broadcast_state(state)
        return web.json_response({"ok": True, "account": account.to_dict()})
    except Exception as exc:
        logger.exception("Add token failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def _handle_check_account(request: web.Request) -> web.Response:
    state: ScrapingState = request.app["state"]
    account_manager: AccountManager = request.app["account_manager"]
    try:
        idx = int(request.match_info["idx"])
        if not 0 <= idx < len(account_manager.accounts):
            return web.json_response({"ok": False, "error": "Account not found"}, status=404)
        account = await account_manager.validate_token(account_manager.accounts[idx].token)
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

        cl = Tuiclient()
        try:
            await cl._netw_connect()
            login_token = await cl.check_verify_code(pending["auth_token"], code)
            cl.token = login_token
            await cl.finalise_auth()
            profile = cl.profile
            account = Account(
                token=login_token,
                username=profile.get_name(),
                user_id=profile.id,
                valid=True,
                profile=profile,
            )
            await account_manager.add_account(account)
            account_manager.save(state)
            account_manager.set_pending_phone_auth(None)
            await _broadcast_state(state)
            return web.json_response({"ok": True, "account": account.to_dict()})
        finally:
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
    app.router.add_post("/api/batches-per-account", _handle_batches_per_account)
    app.router.add_post("/api/pause", _handle_pause)
    app.router.add_post("/api/save", _handle_save)
    app.router.add_post("/api/stop", _handle_stop)
    app.router.add_post("/api/start", _handle_start)
    app.router.add_post("/api/range", _handle_range)
    app.router.add_post("/api/restart", _handle_restart)
    app.router.add_get("/api/download", _handle_download)
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
        id_min=9_950_000,
        id_max=15_000_000,
        id_step=1000,
        cooldown_seconds=account_manager.loaded_settings.get("cooldown_seconds", 20.0),
        reconnect_cooldown_seconds=account_manager.loaded_settings.get("reconnect_cooldown_seconds", 20.0),
        batches_per_account=account_manager.loaded_settings.get("batches_per_account", 5),
    )
    state.account_manager = account_manager

    app = create_app(state)

    manager = ScraperManager(state)
    app["manager"] = manager
    app["account_manager"] = account_manager

    broadcast_task = asyncio.create_task(_periodic_broadcast(state))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8081)
    await site.start()

    logger.info("🚀 Dashboard running at http://127.0.0.1:8081")

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
