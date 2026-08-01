import asyncio
import ast
import json
import traceback
from pathlib import Path

from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
import questionary

from client import Tuiclient
from network import Opcodes
from tools import UniversalEncoder


def build_opcode_completer() -> WordCompleter:
    words: list[str] = []
    for op in Opcodes:
        words.append(str(op.value))
        words.append(op.name)
    return WordCompleter(words, ignore_case=True)


def parse_command(text: str) -> tuple[int, dict]:
    text = text.strip()
    if not text:
        raise ValueError("empty command")

    head, *tail = text.split(maxsplit=1)
    payload_json = tail[0] if tail else "{}"

    try:
        opcode = int(head)
    except ValueError:
        try:
            opcode = int(getattr(Opcodes, head.upper()))
        except AttributeError:
            raise ValueError(f"unknown opcode: {head!r}")

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(payload_json)
        except Exception as exc:
            raise ValueError(f"invalid JSON/Python payload: {exc}")

    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object (dict)")

    return opcode, payload


STYLE = questionary.Style([
    ("qmark", "fg:#673ab7 bold"),
    ("question", "bold"),
    ("answer", "fg:#00bcd4 bold"),
    ("pointer", "fg:#00bcd4 bold"),
    ("highlighted", "fg:#00bcd4 bold"),
    ("selected", "fg:#00bcd4"),
    ("separator", "fg:#6c6c6c"),
    ("instruction", "fg:#6c6c6c"),
    ("text", ""),
    ("disabled", "fg:#6c6c6c italic"),
])


HISTORY_PATH = Path.home() / ".max2tg_cli_history"


async def main():
    completer = build_opcode_completer()
    history = FileHistory(str(HISTORY_PATH))

    with patch_stdout(raw=True):
        q = Tuiclient()
        await q.begin()

        print("Enter commands as: [opcode] [json dict]")
        print("Examples: 49 {\"chatId\": 123}   or   GET_MESSAGES {\"chatId\": 123}")
        print("Type 'exit', 'quit' or press Ctrl+C to leave.\n")

        while True:
            try:
                text = await questionary.text(
                    "Command ->",
                    style=STYLE,
                    completer=completer,
                    history=history,
                ).ask_async()
            except (KeyboardInterrupt, EOFError):
                break

            if text is None:
                break

            text = text.strip()
            if text.lower() in {"exit", "quit", "q"}:
                break
            if not text:
                continue
            if text.lower() in {"login", "signin"}:
                await q.select_account()

            try:
                opcode, payload = parse_command(text)
            except ValueError as exc:
                print(f"[parse error] {exc}")
                continue

            try:
                response = await q.request(opcode, payload)
                print(json.dumps(response, cls=UniversalEncoder, indent=2, ensure_ascii=False))
            except Exception:
                print("[request error]")
                traceback.print_exc()

        print("bye")


if __name__ == "__main__":
    asyncio.run(main())
