#!/usr/bin/env python3
"""Resolve one official replay; optionally archive it before temporary cleanup.

The current BSER session is read from ``.secrets/session.json``. The token is
never printed, TLS verification remains enabled, downloads are bounded and
atomic, and the resulting replay is checked against its magic and gameId.
Transient session/download files are removed. The configured private source
archive survives analysis errors and can be reprocessed by a later parser.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import ssl
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import brotli


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ARCHIVE_ROOT = os.path.join(os.path.dirname(HERE), 'local-corpus', 'replays')
STATE_FILE = os.path.join(HERE, ".secrets", "session.json")
HOST = "https://bser-rest-release.bser.io"
MAX_REPLAY_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
RECORD = struct.Struct("<HHIII")
RECORD_START = 0x410
MAGIC = b"EternalReturnV1\x00"
TLS = ssl.create_default_context()


def load_session_state() -> dict:
    if not os.path.exists(STATE_FILE):
        sys.exit(f"[error] session state is missing: {STATE_FILE}")
    with open(STATE_FILE, encoding="utf-8") as handle:
        state = json.load(handle)
    token = state.get("sessionKey")
    if not isinstance(token, str) or not token.startswith("Session:"):
        sys.exit("[error] captured session state is invalid")
    if not isinstance(state.get("version"), str) or not state["version"].strip():
        sys.exit("[error] captured session is missing X-BSER-Version")
    if not isinstance(state.get("authProvider"), str) or not state["authProvider"].strip():
        sys.exit("[error] captured session is missing X-BSER-AuthProvider")
    return state


def remove_session_state() -> None:
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    secret_dir = os.path.dirname(STATE_FILE)
    try:
        os.rmdir(secret_dir)
    except OSError:
        pass


@contextmanager
def in_memory_session_state():
    """Consume one captured session file and retain it only in this process.

    The on-disk capture is erased before the first backend request. Callers may
    reuse the returned state for a bounded sequence of replay lookups; a 401 is
    terminal and must not trigger another identity/provider fallback.
    """

    try:
        state = load_session_state()
    finally:
        remove_session_state()
    try:
        yield state
    finally:
        state.clear()


def _read_json(response) -> dict:
    payload = response.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("BSER JSON response exceeded the 2 MiB limit")
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("BSER JSON response is not an object")
    return decoded


def api_get(path: str, state: dict) -> tuple[int, dict]:
    request = urllib.request.Request(HOST + path, method="GET")
    request.add_header("X-BSER-Version", state["version"])
    request.add_header("X-BSER-AuthProvider", state["authProvider"])
    request.add_header("X-BSER-SessionKey", state["sessionKey"])
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "BestHTTP/2 v2.4.0")
    try:
        with urllib.request.urlopen(request, timeout=25, context=TLS) as response:
            return response.status, _read_json(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, _read_json(error)
        except Exception:
            return error.code, {}


def find_replay(game_id: int, user_num: int, state: dict) -> tuple[int, dict]:
    return api_get(f"/api/external/findReplayGame/{game_id}/{user_num}", state)


def get_battle_game(game_id: int, state: dict) -> tuple[int, dict]:
    return api_get(f"/api/battle/game/{game_id}", state)


def extract_exact_participant_user_nums(game_id: int, data: dict) -> list[int]:
    """Return only participant ids tied to one exact official battle result.

    The private backend has used ``rst.battleUserGames``, ``rst.userGames``,
    and a direct ``rst`` list. No recursive key search or nearby-game/user
    fallback is allowed.
    """

    rst = data.get("rst")
    if isinstance(rst, dict):
        rows = rst.get("battleUserGames")
        if rows is None:
            rows = rst.get("userGames")
    else:
        rows = rst
    if not isinstance(rows, list) or not 1 <= len(rows) <= 36:
        top_level_keys = sorted(str(key) for key in data.keys())
        rst_keys = sorted(str(key) for key in rst.keys()) if isinstance(rst, dict) else []
        row_container_keys = (
            sorted(str(key) for key in rows.keys())
            if isinstance(rows, dict)
            else []
        )
        raise RuntimeError(
            "official battle result has no bounded participant list "
            f"(topLevelKeys={top_level_keys}, rstType={type(rst).__name__}, "
            f"rstKeys={rst_keys}, participantContainerType={type(rows).__name__}, "
            f"participantContainerKeys={row_container_keys}, cod={data.get('cod')!r}, "
            f"msg={data.get('msg')!r})"
        )

    participant_ids: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("official battle participant row is not an object")
        observed_game_id = row.get("gameId", row.get("gameNum"))
        if (
            isinstance(observed_game_id, bool)
            or not isinstance(observed_game_id, int)
            or observed_game_id != game_id
        ):
            raise RuntimeError("official battle participant row game id mismatch")
        user_num = row.get("userNum")
        if isinstance(user_num, int) and not isinstance(user_num, bool) and user_num > 0:
            if user_num not in participant_ids:
                participant_ids.append(user_num)
    if not participant_ids:
        raise RuntimeError("official battle result has no numeric participant userNum")
    return participant_ids


def _replay_path(data: dict) -> str | None:
    rst = data.get("rst")
    replay_path = rst.get("replayPath") if isinstance(rst, dict) else None
    return replay_path if isinstance(replay_path, str) and replay_path else None


def resolve_replay(game_id: int, service_account_user: int | None, state: dict) -> tuple[str, int, dict]:
    if service_account_user is None or service_account_user <= 0:
        raise RuntimeError(
            "fixed service-account identity is required before exact participant resolution"
        )

    user_num = service_account_user
    captured_handle = state.get("userNum") if isinstance(state.get("userNum"), int) else None
    resolution = {
        "capturedHandleMatchedConfiguredAccount": (
            user_num == captured_handle if captured_handle is not None else None
        ),
    }

    status, data = find_replay(game_id, user_num, state)
    reject_idle_session(data)
    if data.get('cod') == 9300:
        raise RuntimeError('official replay version unsupported (9300)')
    if status == 401:
        sys.exit("[error] 401: session expired; capture a fresh session")
    replay_path = _replay_path(data)
    if status == 200 and replay_path is not None:
        resolution["replayIdentitySource"] = "fixed-service-account"
        resolution["participantCandidateCount"] = 0
        resolution["participantLookupAttemptCount"] = 0
        return replay_path, user_num, resolution

    battle_status, battle_data = get_battle_game(game_id, state)
    reject_idle_session(battle_data)
    if battle_status == 401:
        sys.exit("[error] 401: session expired; capture a fresh session")
    if battle_status != 200:
        raise RuntimeError(
            f"official battle result lookup failed with HTTP {battle_status}"
        )
    try:
        participant_ids = extract_exact_participant_user_nums(game_id, battle_data)
    except RuntimeError:
        # An empty exact result cannot justify trying unrelated identities.
        rst=battle_data.get('rst')
        if not (isinstance(rst,dict) and rst.get('battleUserGames')==[]):raise
        for delay in (2,5):
            time.sleep(delay)
            retry_status,retry_data=find_replay(game_id,user_num,state)
            reject_idle_session(retry_data)
            if retry_status==401:sys.exit('[error] 401: session expired; capture a fresh session')
            if retry_data.get('cod')==9300:raise RuntimeError('official replay version unsupported (9300)')
            retry_path=_replay_path(retry_data)
            if retry_status==200 and retry_path:
                resolution.update(replayIdentitySource='fixed-service-account',participantCandidateCount=0,participantLookupAttemptCount=0)
                return retry_path,user_num,resolution
        raise RuntimeError('official replay temporarily unavailable; exact battle participants absent') from None
    attempted = 0
    statuses: list[int] = []
    for participant_user_num in participant_ids:
        attempted += 1
        participant_status, participant_data = find_replay(
            game_id, participant_user_num, state
        )
        reject_idle_session(participant_data)
        statuses.append(participant_status)
        if participant_status == 401:
            sys.exit("[error] 401: session expired; capture a fresh session")
        participant_path = _replay_path(participant_data)
        if participant_status == 200 and participant_path is not None:
            resolution["replayIdentitySource"] = "exact-battle-game-participant"
            resolution["participantCandidateCount"] = len(participant_ids)
            resolution["participantLookupAttemptCount"] = attempted
            return participant_path, participant_user_num, resolution

    raise RuntimeError(
        "exact official participant replay lookups returned no replay path "
        f"(candidateCount={len(participant_ids)}, attemptCount={attempted}, "
        f"httpStatuses={statuses})"
    )


def reject_idle_session(data: dict) -> None:
    if data.get('cod') == 1102:
        raise RuntimeError('official session expired after inactivity (1102); sign in to the official client and capture a fresh session')


def validate_replay(path: str, expected_game_id: int) -> dict:
    file_size = os.path.getsize(path)
    if file_size < RECORD_START + RECORD.size:
        raise ValueError("replay is too small")

    with open(path, "rb") as replay:
        if replay.read(len(MAGIC)) != MAGIC:
            raise ValueError("invalid EternalReturnV1 magic")

        position = RECORD_START
        inspected = 0
        while position + RECORD.size <= file_size and inspected < 100_000:
            replay.seek(position)
            header = replay.read(RECORD.size)
            if len(header) != RECORD.size:
                break
            kind, version, tick, length, _auxiliary = RECORD.unpack(header)
            payload_start = position + RECORD.size
            payload_end = payload_start + length
            if payload_end > file_size:
                raise ValueError("record length extends beyond end of file")

            if kind == 2 and version == 1:
                payload = replay.read(length)
                decoded = brotli.decompress(payload)
                if len(decoded) < 13:
                    raise ValueError("snapshot payload is too small")
                replay_game_id = struct.unpack_from("<q", decoded, 5)[0]
                if replay_game_id != expected_game_id:
                    raise ValueError(
                        f"gameId mismatch: expected {expected_game_id}, got {replay_game_id}"
                    )
                return {
                    "bytes": file_size,
                    "gameId": replay_game_id,
                    "firstSnapshotTick": tick,
                }

            position = payload_end
            inspected += 1

    raise ValueError("no decodable snapshot record was found")


def download_replay(url: str, output_path: str, game_id: int) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("replay URL must be an absolute HTTPS URL")

    output_path = os.path.abspath(output_path)
    partial_path = output_path + ".part"
    if os.path.exists(output_path):
        raise FileExistsError(f"output already exists: {output_path}")
    if os.path.exists(partial_path):
        raise FileExistsError(f"stale partial download exists: {partial_path}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    total = 0
    try:
        with urllib.request.urlopen(url, timeout=60, context=TLS) as response:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme.lower() != "https":
                raise ValueError("download redirected away from HTTPS")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_REPLAY_BYTES:
                raise ValueError("declared replay size exceeds the 512 MiB limit")
            with open(partial_path, "xb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_REPLAY_BYTES:
                        raise ValueError("replay exceeded the 512 MiB limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())

        validation = validate_replay(partial_path, game_id)
        os.rename(partial_path, output_path)
        validation["path"] = output_path
        return validation
    except Exception:
        if os.path.exists(partial_path):
            os.remove(partial_path)
        raise


@contextmanager
def acquire_replay_with_state(
    game_id: int,
    explicit_user: int | None,
    state: dict,
    *, archive_root=None,
):
    """Yield one verified temporary replay using a caller-held memory session."""

    with tempfile.TemporaryDirectory(prefix="ercraft-replay-") as temporary_dir:
        replay_url, _used_user, resolution = resolve_replay(
            game_id, explicit_user, state
        )
        output_path = os.path.join(temporary_dir, f"{game_id}.er")
        result = download_replay(replay_url, output_path, game_id)
        result.update(resolution)
        # Persist derived decode data only; the original stays in this temporary context.
        destination=(archive_root if archive_root is not None else
                     os.environ.get('ERCRAFT_REPLAY_ARCHIVE_ROOT') or DEFAULT_ARCHIVE_ROOT)
        if destination:
            root=os.path.dirname(HERE)
            if root not in sys.path:sys.path.insert(0,root)
            from decoder.full_replay_corpus import archive_replay
            result['replayArchive']=archive_replay(output_path,destination)
        yield result


@contextmanager
def acquire_replay(game_id: int, explicit_user: int | None = None, *, archive_root=None):
    """Yield a temporary path; preserve derived data only and erase session state.

    A parser should consume ``result["path"]`` only inside this context. The
    path is invalid after the ``with`` block exits.
    """

    with in_memory_session_state() as state:
        with acquire_replay_with_state(game_id, explicit_user, state,archive_root=archive_root) as result:
            yield result


def write_report(path: str, result: dict) -> None:
    """Persist only non-secret verification metadata, never a raw replay URL."""

    output_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    temporary_path = output_path + ".tmp"
    report = {
        "format": "ercraft-replay-acquisition-check.v2",
        "gameId": result["gameId"],
        "bytes": result["bytes"],
        "firstSnapshotTick": result["firstSnapshotTick"],
        "capturedHandleMatchedConfiguredAccount": result.get(
            "capturedHandleMatchedConfiguredAccount"
        ),
        "replayIdentitySource": result.get("replayIdentitySource"),
        "participantCandidateCount": result.get("participantCandidateCount"),
        "participantLookupAttemptCount": result.get(
            "participantLookupAttemptCount"
        ),
        "rawReplayRetained": False,
        "sourceArchiveSha256": (result.get('replayArchive') or {}).get('sourceSha256'),
        "sessionRetained": False,
    }
    timings = result.get("pipelineTimingsSeconds")
    if timings is not None:
        if not isinstance(timings, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, (int, float))
            or value < 0
            for key, value in timings.items()
        ):
            raise ValueError("pipeline timings are invalid")
        report["pipelineTimingsSeconds"] = {
            key: round(float(value), 3) for key, value in timings.items()
        }
    try:
        with open(temporary_path, "x", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, output_path)
    except Exception:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify and archive one official replay; remove transient authentication files"
    )
    parser.add_argument("gameId", type=int)
    parser.add_argument(
        "--user",
        type=int,
        default=None,
        help="fixed official-client service-account userNum; no participant fallback is used",
    )
    parser.add_argument(
        "--report-out",
        default=None,
        help="optional path for a non-secret verification JSON report",
    )
    parser.add_argument('--archive-root',default=os.environ.get('ERCRAFT_REPLAY_ARCHIVE_ROOT') or DEFAULT_ARCHIVE_ROOT)
    args = parser.parse_args()

    with acquire_replay(args.gameId, args.user,archive_root=args.archive_root) as result:
        print(
            f"[verified] gameId={result['gameId']} bytes={result['bytes']} "
            f"firstSnapshotTick={result['firstSnapshotTick']}"
        )
        if args.report_out:
            write_report(args.report_out, result)
            print(f"[report] {os.path.abspath(args.report_out)}")
    print("[cleanup] temporary download and plaintext session removed; private source archive retained")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        sys.exit(f"[error] {error}")
