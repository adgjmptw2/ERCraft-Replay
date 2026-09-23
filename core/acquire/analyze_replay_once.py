#!/usr/bin/env python3
"""Acquire, analyze, validate, and erase one official replay.

The raw replay exists only inside ``acquire_replay``. Persisted output contains
derived local analysis and a non-secret acquisition report, never the session,
signed replay URL, account handle, or raw ``.er`` bytes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse


ROOT = Path(__file__).resolve().parent.parent
ACQUIRE_DIR = ROOT / "acquire"
sys.path.insert(0, str(ACQUIRE_DIR))
sys.path.insert(0, str(ROOT / "decoder"))

from get_replay import acquire_replay, write_report  # noqa: E402
from build_combat_analysis import AnalysisConfig, run_analysis  # noqa: E402
from build_public_combat_analysis import (  # noqa: E402
    write_public_analysis,
)
from replay_compatibility import (  # noqa: E402
    read_replay_client_version,
    require_supported_client_version,
)
from validate_combat_analysis import validate_analysis  # noqa: E402
from validate_public_combat_analysis import (  # noqa: E402
    validate_public_analysis_object,
)


OBJECT_TYPE_ENUM_REPORT = ROOT / "research" / "global-metadata.ObjectType.inspect.json"
INSTALLED_CLIENT_VERSION_FILE = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Eternal Return\EternalReturn_Data\StreamingAssets\version"
)
INSTALLED_GLOBAL_METADATA = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Eternal Return\EternalReturn_Data\il2cpp_data\Metadata\global-metadata.dat"
)


def load_service_account() -> int:
    config_path = ACQUIRE_DIR / "config.local"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if config.get("format") != "ercraft-replay-service-account.v1":
        raise ValueError("fixed service-account config format is invalid")
    user_num = config.get("serviceAccountUserNum")
    if not isinstance(user_num, int) or user_num <= 0:
        raise ValueError("fixed service-account identity is invalid")
    return user_num


def infer_game_data_path(inspect_path: Path) -> Path:
    inspect = json.loads(inspect_path.read_text(encoding="utf-8"))
    url = inspect.get("format", {}).get("gameDataUrl")
    if not isinstance(url, str) or not url:
        raise ValueError("inspect report is missing gameDataUrl")
    filename = Path(urllib.parse.urlparse(url).path).name
    if not filename or filename in {".", ".."}:
        raise ValueError("inspect gameDataUrl has no safe filename")
    path = ACQUIRE_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"replay-version game data is missing: {path}")
    return path


def inspect_client_version(inspect_path: Path) -> str:
    report = json.loads(inspect_path.read_text(encoding="utf-8"))
    version = report.get("format", {}).get("clientVersion")
    if not isinstance(version, str) or not version:
        raise ValueError("inspect report is missing clientVersion")
    return version


def validate_enum_catalog(enum_path: Path, client_version: str) -> dict:
    report = json.loads(enum_path.read_text(encoding="utf-8"))
    if report.get("fieldReportClientVersion") != client_version:
        raise ValueError(
            "enum catalog client version does not match the replay: "
            f"{report.get('fieldReportClientVersion')} != {client_version}"
        )
    if not isinstance(report.get("catalog"), list) or not report["catalog"]:
        raise ValueError("enum catalog is empty or invalid")
    return report


def find_enum_catalog(client_version: str, search_dir: Path = ACQUIRE_DIR) -> Path:
    matches = []
    for path in sorted(search_dir.glob("*.enum-catalog.inspect.json")):
        try:
            validate_enum_catalog(path, client_version)
        except (ValueError, json.JSONDecodeError):
            continue
        matches.append(path.resolve())
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one enum catalog for client {client_version}; "
            f"found {len(matches)}"
        )
    return matches[0]


def installed_metadata_for_version(client_version: str) -> Path | None:
    """Return metadata only when the installed client declares the exact version."""

    if not INSTALLED_CLIENT_VERSION_FILE.is_file() or not INSTALLED_GLOBAL_METADATA.is_file():
        return None
    installed_version = INSTALLED_CLIENT_VERSION_FILE.read_text(
        encoding="utf-8-sig"
    ).strip()
    if installed_version != client_version:
        return None
    return INSTALLED_GLOBAL_METADATA.resolve()


def run_prerequisite_script(script: str, *arguments: object) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "decoder" / script), *(str(item) for item in arguments)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"{script} failed: {detail}")


def replace_replay_source(path: Path, replay_source_label: str) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    if "sourceFile" in report:
        report["sourceFile"] = replay_source_label
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_prerequisites(
    replay_path: Path,
    game_id: int,
    evidence_dir: Path,
    replay_source_label: str,
    timing_sink: dict[str, float] | None = None,
    object_type_enum_report: Path | None = None,
) -> tuple[Path, Path, Path]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    inspect_path = evidence_dir / f"{game_id}.inspect.json"
    field_path = evidence_dir / f"{game_id}.delta-fields.inspect.json"
    spawn_path = evidence_dir / f"{game_id}.spawn-snapshots.inspect.json"

    spawn_arguments: list[object] = [replay_path, "--out", spawn_path]
    selected_object_type_report = object_type_enum_report or OBJECT_TYPE_ENUM_REPORT
    if selected_object_type_report.is_file():
        spawn_arguments.extend(
            ["--object-type-enum-report", selected_object_type_report]
        )

    # These decoders read the same immutable replay but write disjoint reports.
    # Running them together avoids three serial full-file scans without retaining
    # another raw copy or changing any evidence/fallback contract.
    jobs = [
        ("inspect_replay.py", (replay_path, "--out", inspect_path)),
        ("inspect_delta_fields.py", (replay_path, "--out", field_path)),
        ("inspect_spawn_snapshots.py", tuple(spawn_arguments)),
    ]

    def timed_job(script: str, arguments: tuple[object, ...]) -> tuple[str, float]:
        started = time.perf_counter()
        run_prerequisite_script(script, *arguments)
        return script, round(time.perf_counter() - started, 3)

    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = [
            executor.submit(timed_job, script, arguments)
            for script, arguments in jobs
        ]
        # Resolve in a stable order so an error names the same failed decoder
        # regardless of scheduler timing. No alternate decoder is attempted.
        for future in futures:
            script, elapsed = future.result()
            if timing_sink is not None:
                timing_sink[f"prerequisite:{script}"] = elapsed

    inspect = json.loads(inspect_path.read_text(encoding="utf-8"))
    observed_game_id = inspect.get("topLevelFromFirstSnapshot", {}).get("gameId")
    if observed_game_id != game_id:
        raise ValueError(
            f"generated inspect gameId mismatch: expected {game_id}, got {observed_game_id}"
        )
    for path in (inspect_path, field_path, spawn_path):
        replace_replay_source(path, replay_source_label)
    return inspect_path, field_path, spawn_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Acquire and fully analyze one replay without retaining raw bytes"
    )
    parser.add_argument("gameId", type=int)
    parser.add_argument("--inspect", type=Path)
    parser.add_argument("--delta-fields", type=Path)
    parser.add_argument("--enum-catalog", type=Path)
    parser.add_argument("--spawn-snapshots", type=Path)
    parser.add_argument("--generate-prerequisites", action="store_true")
    parser.add_argument("--defer-public", action="store_true", help="Private phase only; caller must export and validate final enriched public result")
    parser.add_argument("--evidence-out-dir", type=Path)
    parser.add_argument("--game-data", type=Path, default=None)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--report-out", required=True, type=Path)
    parser.add_argument("--replay-source-label", default=None)
    parser.add_argument(
        "--replay-user",
        type=int,
        default=None,
        help=(
            "one exact numeric participant for this game; when omitted the fixed "
            "service account is used. No participant fanout is performed"
        ),
    )
    args = parser.parse_args(argv)

    game_id = args.gameId
    out_dir = args.out_dir.resolve()
    if args.generate_prerequisites:
        if (
            args.inspect is not None
            or args.delta_fields is not None
            or args.spawn_snapshots is not None
        ):
            parser.error(
                "--generate-prerequisites cannot be combined with --inspect, "
                "--delta-fields, or --spawn-snapshots"
            )
    elif (
        args.inspect is None
        or args.delta_fields is None
        or args.enum_catalog is None
        or args.spawn_snapshots is None
    ):
        parser.error(
            "--inspect, --delta-fields, --enum-catalog, and --spawn-snapshots "
            "are required unless --generate-prerequisites is used"
        )
    replay_user = args.replay_user
    if replay_user is not None and replay_user <= 0:
        parser.error("--replay-user must be a positive integer")
    if replay_user is None:
        replay_user = load_service_account()
    safe_result: dict | None = None
    validation: dict | None = None
    public_validation: dict | None = None
    pipeline_started = time.perf_counter()
    timings: dict[str, float] = {}

    acquisition_started = time.perf_counter()
    with acquire_replay(game_id, replay_user) as replay:
        timings["acquisition"] = round(time.perf_counter() - acquisition_started, 3)
        replay_path = Path(replay["path"])
        # Reject an unvalidated patch before running any 12.2-specific parser,
        # schema decoder, game-data lookup, or prerequisite generator.
        header_client_version = read_replay_client_version(replay_path)
        require_supported_client_version(header_client_version)
        replay_source_label = args.replay_source_label or f"replay/{game_id}.er"
        exact_metadata_path = installed_metadata_for_version(header_client_version)
        generated_object_type_report: Path | None = None
        generated_enum_path: Path | None = None
        if args.generate_prerequisites:
            evidence_dir = (
                args.evidence_out_dir.resolve()
                if args.evidence_out_dir is not None
                else out_dir / "evidence"
            )
            if header_client_version == "12.3.0" and exact_metadata_path is None:
                raise ValueError(
                    "exact-installed-metadata-unavailable-for-client:12.3.0"
                )
            if exact_metadata_path is not None:
                evidence_dir.mkdir(parents=True, exist_ok=True)
                generated_object_type_report = (
                    evidence_dir / f"{game_id}.ObjectType.inspect.json"
                )
                object_type_started = time.perf_counter()
                run_prerequisite_script(
                    "inspect_il2cpp_metadata.py",
                    exact_metadata_path,
                    "--type-name",
                    "ObjectType",
                    "--out",
                    generated_object_type_report,
                )
                timings["prerequisite:inspect-object-type-metadata"] = round(
                    time.perf_counter() - object_type_started, 3
                )
            prerequisites_started = time.perf_counter()
            inspect_path, field_path, spawn_path = generate_prerequisites(
                replay_path,
                game_id,
                evidence_dir,
                replay_source_label,
                timings,
                object_type_enum_report=generated_object_type_report,
            )
            if exact_metadata_path is not None:
                generated_enum_path = (
                    evidence_dir / f"{game_id}.enum-catalog.inspect.json"
                )
                enum_started = time.perf_counter()
                run_prerequisite_script(
                    "export_observed_enum_catalog.py",
                    exact_metadata_path,
                    field_path,
                    "--out",
                    generated_enum_path,
                )
                timings["prerequisite:export-enum-catalog"] = round(
                    time.perf_counter() - enum_started, 3
                )
            timings["prerequisites"] = round(
                time.perf_counter() - prerequisites_started, 3
            )
        else:
            inspect_path = args.inspect.resolve()
            field_path = args.delta_fields.resolve()
            spawn_path = args.spawn_snapshots.resolve()
            timings["prerequisites"] = 0.0

        client_version = inspect_client_version(inspect_path)
        if client_version != header_client_version:
            raise ValueError(
                "inspect client version does not match replay header: "
                f"{client_version} != {header_client_version}"
            )
        require_supported_client_version(client_version)
        enum_path = (
            args.enum_catalog.resolve()
            if args.enum_catalog is not None
            else (
                generated_enum_path
                if generated_enum_path is not None
                else find_enum_catalog(client_version)
            )
        )
        enum_report = validate_enum_catalog(enum_path, client_version)
        object_type_report_path = (
            generated_object_type_report or OBJECT_TYPE_ENUM_REPORT
        )
        if object_type_report_path.is_file():
            object_type_report = json.loads(
                object_type_report_path.read_text(encoding="utf-8")
            )
            if (
                object_type_report.get("sourceSha256")
                != enum_report.get("metadataSourceSha256")
            ):
                raise ValueError(
                    "ObjectType report and enum catalog do not share the same metadata hash"
                )
        game_data_path = (
            args.game_data.resolve()
            if args.game_data is not None
            else infer_game_data_path(inspect_path)
        )
        from decoder.corpus_runtime_source import CorpusRuntimeSource
        from decoder.replay_schema_inputs import schema_path_for_version
        config = AnalysisConfig(
            game_id=game_id,
            replay_path=replay_path,
            inspect_path=inspect_path,
            enum_path=enum_path,
            spawn_path=spawn_path,
            game_data_path=game_data_path,
            schema_path=schema_path_for_version(client_version),
            out_dir=out_dir,
            replay_source_label=replay_source_label,
            full_decode_path=(CorpusRuntimeSource.path_from_acquisition(replay)
                              if replay.get('replayArchive') else None),
        )
        private_analysis_started = time.perf_counter()
        private_timings: dict[str, float] = {}
        catalog = run_analysis(
            config,
            write_private_html=False,
            timing_sink=private_timings,
        )
        timings.update(private_timings)
        timings["privateAnalysis"] = round(
            time.perf_counter() - private_analysis_started, 3
        )
        private_validation_started = time.perf_counter()
        validation = validate_analysis(config.json_path, None, game_id)
        timings["privateValidation"] = round(
            time.perf_counter() - private_validation_started, 3
        )
        if args.defer_public:
            public_validation = {"status": "deferred", "reason": "caller-validates-final-enriched-result"}
        else:
            public_dir = out_dir / "public"
            field_report = json.loads(field_path.read_text(encoding="utf-8"))
            public_build_started = time.perf_counter()
            rendered_public: dict[str, object] = {}
            public_catalog = write_public_analysis(
                catalog,
                field_report,
                public_dir,
                rendered_output=rendered_public,
            )
            timings["publicBuild"] = round(
                time.perf_counter() - public_build_started, 3
            )
            public_validation_started = time.perf_counter()
            public_validation = validate_public_analysis_object(
                public_catalog,
                rendered_public["html"],
                json.loads(config.json_path.read_text(encoding="utf-8")),
            )
            timings["publicValidation"] = round(
                time.perf_counter() - public_validation_started, 3
            )
        safe_result = {
            "gameId": replay["gameId"],
            "bytes": replay["bytes"],
            "firstSnapshotTick": replay["firstSnapshotTick"],
            "capturedHandleMatchedConfiguredAccount": replay.get(
                "capturedHandleMatchedConfiguredAccount"
            ),
        }

    if safe_result is None or validation is None or public_validation is None:
        raise RuntimeError("analysis did not produce a verified result")
    timings["total"] = round(time.perf_counter() - pipeline_started, 3)
    safe_result["pipelineTimingsSeconds"] = timings
    write_report(str(args.report_out.resolve()), safe_result)
    print(
        json.dumps(
            {
                "status": "ok",
                "analysis": validation,
                "publicAnalysis": public_validation,
                "outputDirectory": str(out_dir),
                "rawReplayRetained": False,
                "sessionRetained": False,
                "pipelineTimingsSeconds": timings,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error).strip() or type(error).__name__
        sys.exit(f"[error] {detail}")
