import pathlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "signal_audit"
PACKAGE = DEPLOY / "package_signal_audit.ps1"
INSTALLER = DEPLOY / "install_or_update.sh"
FREEZE_UNITS = (
    "signal-audit-materialize.timer",
    "signal-audit-materialize.service",
    "signal-audit-llm-review.timer",
    "signal-audit-llm-review.service",
    "astra-workbench-v20.service",
)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def powershell_executable():
    return shutil.which("powershell") or shutil.which("pwsh")


def run_package(output_dir):
    executable = powershell_executable()
    assert_true(executable, "PowerShell is required for package script checks")
    return subprocess.run(
        [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PACKAGE),
            "-OutputDir",
            str(output_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def assert_package_includes_release_assets():
    artifact_root = ROOT / ".artifacts"
    artifact_root.mkdir(exist_ok=True)
    temp_root = pathlib.Path(tempfile.mkdtemp(
        prefix="package-upgrade-",
        dir=artifact_root,
    ))
    try:
        relative_output = temp_root.relative_to(ROOT)
        completed = run_package(relative_output)
        assert_true(
            completed.returncode == 0,
            "package script should succeed in an isolated output dir: "
            + (completed.stderr or completed.stdout),
        )
        package_root = temp_root / "signal-audit-deploy"
        zip_path = temp_root / "signal-audit-deploy.zip"
        for path in (
            package_root / "frontend" / "index.html",
            package_root / "frontend" / "app.js",
            package_root / "frontend" / "VERSION.json",
            package_root / "frontend" / "signal_cards",
            package_root / "tools" / "signal_evidence_v2.py",
            package_root / "tools" / "signal_review_v2.py",
            package_root / "tools" / "signal_review_v2_runtime.py",
            zip_path,
        ):
            assert_true(path.exists(), "packaged asset missing: " + str(path))
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
        for name in (
            "frontend/index.html",
            "frontend/app.js",
            "frontend/VERSION.json",
            "tools/signal_evidence_v2.py",
            "tools/signal_review_v2.py",
            "tools/signal_review_v2_runtime.py",
            "tools/astra_joint_bridge.py",
            "tools/astra_joint_contract.py",
            "tools/astra_joint_data.py",
            "tools/astra_joint_dataset.py",
            "tools/astra_joint_sources.py",
            "tools/astra_joint_projection.py",
            "tools/astra_joint_v11_contract.py",
            "tools/astra_joint_v11_data.py",
            "tools/astra_joint_v11_inference.py",
            "tools/astra_joint_v11_model.py",
            "tools/astra_data_engine_v23.py",
            "tools/astra_data_projection_v23.py",
            "tools/astra_data_retention_v23.py",
            "tools/astra_data_server_v23.py",
            "tools/astra_underwriting_guidance_v20.py",
            "tools/astra_workbench_session_v1.py",
            "tools/astra_workbench_reading_v1.py",
            "tools/astra_evidence_routing_v1.py",
            "tools/astra_map_data_server_v24.py",
            "tools/astra_wpem_reference_v1.py",
            "tools/astra_wpem_results_v1.py",
            "tools/data_product_registry_v23.json",
            "deploy/apache-private-workbench.conf.example",
            "deploy/install_data_engine_v23.sh",
            "deploy/data-engine-maintenance.service",
            "deploy/data-engine-maintenance.timer",
            "deploy/install_or_update.sh",
        ):
            assert_true(name in names, "zip asset missing: " + name)
        assert_true("frontend/README.md" not in names,
                    "frontend local README should not ship in the minimal RC package")
        assert_true("deploy/joint-shadow/README.md" not in names,
                    "joint shadow research notes should not ship in the minimal RC package")
        with zipfile.ZipFile(zip_path) as archive:
            text_by_name = {
                name: archive.read(name).decode("utf-8", errors="replace")
                for name in (
                    "tools/astra_joint_v11_data.py",
                    "tools/astra_joint_sources.py",
                    "tools/astra_workbench_v20.py",
                    "tools/astra_observation_replay_v1.py",
                    "tools/astra_underwriting_real_replay_v20.py",
                )
            }
        joined_package_text = "\n".join(text_by_name.values())
        assert_true("C:\\Users\\Xu" not in joined_package_text and "C:/Users/Xu" not in joined_package_text,
                    "packaged source must not include local user machine paths")
        assert_true(".artifacts" not in joined_package_text,
                    "packaged source defaults must not point at development artifact folders")
        assert_true("tools/signal_llm_review_canary_release.sh" not in names,
                    "retired canary helper must not ship without its runner")
        manifest_path = package_root / "package-manifest.json"
        assert_true(manifest_path.exists(), "package manifest missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = {entry["path"]: entry for entry in manifest.get("files", [])}
        assert_true("package-manifest.json" not in files, "manifest must not hash itself")
        for name in (
            "frontend/VERSION.json",
            "deploy/install_or_update.sh",
            "fmz/neutral_regulation_demo_fmz.py",
        ):
            packaged = package_root / pathlib.PurePosixPath(name)
            entry = files.get(name)
            assert_true(entry, "manifest entry missing: " + name)
            assert_true(entry.get("bytes") == packaged.stat().st_size,
                        "manifest byte count mismatch: " + name)
            assert_true(entry.get("sha256") == hashlib.sha256(packaged.read_bytes()).hexdigest(),
                        "manifest sha256 mismatch: " + name)
        source = manifest.get("source", {})
        source_text = json.dumps(source, ensure_ascii=False)
        assert_true("repo_root" not in source and "git_top_level" not in source,
                    "package source manifest must not record local absolute paths")
        assert_true(".artifacts" not in source_text and not re.search(r"[A-Za-z]:[\\/]", source_text),
                    "package source manifest must not include development artifact or drive paths")
        assert_true(source.get("path_policy") == "local_absolute_paths_not_recorded",
                    "package source manifest should state its local path policy")
        source_state = source.get("source_state")
        assert_true(source_state in {"clean", "dirty", "not_git_root"},
                    "manifest should record clean/dirty/not_git_root source state")
        if source_state == "not_git_root":
            assert_true(source.get("git_root_matches_package_root") is False,
                        "isolated source must not inherit an ancestor repository")
            assert_true(source.get("head") is None,
                        "not_git_root packages must not claim a git head")
        else:
            assert_true(source.get("git_root_matches_package_root") is True,
                        "git source should match the package root before recording HEAD")
            assert_true(source.get("head"), "manifest should record actual git HEAD")
        assert_true(not source.get("release_commit"),
                    "packager must not claim a verified release commit")
        assert_true(source.get("release_commit_verification") == "not_performed_by_packager",
                    "manifest should state that commit verification is external")
        frontend = manifest.get("frontend", {})
        source_frontend = json.loads((DEPLOY / "frontend" / "VERSION.json").read_text(encoding="utf-8"))
        assert_true(frontend.get("frontend_cache_token") == source_frontend["frontend_cache_token"],
                    "manifest should expose the frontend cache token")
        assert_true(frontend.get("local_review_revision") == source_frontend["local_review_revision"],
                    "manifest should expose the frontend local review revision")
        assert_true(manifest.get("protocol", {}).get("review_response_protocol")
                    == "bounded_claim_ids@2.4.7",
                    "manifest should expose bounded protocol identity")
        assert_true(manifest.get("protocol", {}).get("deterministic_financials_schema")
                    == "astra_underwriting_deterministic_financials@2.4.4",
                    "manifest should expose deterministic financials schema identity")
        assert_true(manifest.get("fmz", {}).get("producer_version_reference") == "2.0.2",
                    "manifest should expose FMZ producer reference")
        assert_true(manifest.get("fmz", {}).get("reference_only") is True,
                    "packaged FMZ file must be marked reference-only")
        assert_true(re.search(r"zip_sha256=[0-9a-f]{64}", completed.stdout),
                    "package command should print zip sha256")
        flat_import = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import astra_workbench_v20, astra_workbench_session_v1, astra_workbench_reading_v1, "
                    "astra_underwriting_store_v20, astra_risk_qualification_v244, "
                    "astra_data_retention_v23, astra_evidence_routing_v1, astra_map_data_server_v24, "
                    "astra_wpem_reference_v1, astra_wpem_results_v1"
                ),
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(package_root / "tools")},
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert_true(flat_import.returncode == 0,
                    "packaged tools should support flat imports: "
                    + (flat_import.stderr or flat_import.stdout))
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        try:
            artifact_root.rmdir()
        except OSError:
            pass


def assert_package_rejects_unsafe_output_paths():
    outside = run_package(pathlib.Path("..") / ("package-escape-" + uuid.uuid4().hex))
    outside_text = outside.stdout + outside.stderr
    assert_true(
        outside.returncode != 0
        and "OutputDir must stay under repository root" in outside_text,
        "package script must reject path escape before deleting output",
    )
    source_dir = run_package(pathlib.Path("deploy") / "signal_audit" / "generated")
    source_text = source_dir.stdout + source_dir.stderr
    assert_true(
        source_dir.returncode != 0
        and "OutputDir must not be inside source directory" in source_text,
        "package script must reject output inside deploy source tree",
    )


def assert_installer_preserves_runtime_cards_by_directory():
    script = INSTALLER.read_text(encoding="utf-8")
    block_match = re.search(
        r"frontend_rsync_args=\(-a --delete\)(.*?)\nrsync ",
        script,
        flags=re.S,
    )
    assert_true(block_match, "installer should build rsync args before rsync")
    block = block_match.group(1)
    assert_true(
        'if [[ -d "$STATIC_ROOT/signal_cards" ]]; then' in block,
        "installer should protect an existing signal_cards directory",
    )
    assert_true(
        'if [[ -f "$STATIC_ROOT/signal_cards/index.json" ]]' not in block,
        "installer must not depend on index.json before protecting runtime cards",
    )
    assert_true(
        "--exclude=/signal_cards/" in block
        and "temporarily missing or damaged" in block,
        "installer should exclude runtime cards and document missing-index safety",
    )

    bash = shutil.which("bash")
    if not bash:
        git_bash = pathlib.Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(git_bash) if git_bash.is_file() else None
    assert_true(bash, "Bash is required to exercise actual installer branch selection")
    # Execute the actual installer argument block, without invoking rsync,
    # install or systemctl. This checks the real shell condition against real
    # directories; it does not model that condition a second time in Python.
    shell = ('STATIC_ROOT="$1"\nfrontend_rsync_args=(-a --delete)'
             + block + '\nprintf "__ARG__%s\\n" "${frontend_rsync_args[@]}"\n')
    for case in ("first", "indexed", "missing-index", "damaged-index"):
        with tempfile.TemporaryDirectory() as directory:
            static = pathlib.Path(directory)
            cards = static / "signal_cards"
            if case != "first":
                cards.mkdir()
                (cards / "real-card.json").write_text('{"real":true}', encoding="utf-8")
            if case == "indexed":
                (cards / "index.json").write_text('{"cards":[]}', encoding="utf-8")
            elif case == "damaged-index":
                (cards / "index.json").write_text('{', encoding="utf-8")
            completed = subprocess.run(
                [bash, "-c", shell, "installer-preservation-test", static.as_posix()],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=False,
            )
            assert_true(completed.returncode == 0, "installer block failed: " + completed.stderr)
            args = [line.removeprefix("__ARG__") for line in completed.stdout.splitlines()
                    if line.startswith("__ARG__")]
            assert_true(args[:2] == ["-a", "--delete"], "unexpected rsync options")
            assert_true(("--exclude=/signal_cards/" in args) == (case != "first"),
                        "actual installer preservation branch failed for " + case)


def bash_path(path):
    value = pathlib.Path(path).resolve().as_posix()
    if re.match(r"^[A-Za-z]:/", value):
        return "/" + value[0].lower() + value[2:]
    return value


def run_installer_with_stubs(scenario, extra_env=None):
    bash = shutil.which("bash")
    if not bash:
        git_bash = pathlib.Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(git_bash) if git_bash.is_file() else None
    assert_true(bash, "Bash is required to exercise installer writer ordering")
    with tempfile.TemporaryDirectory() as directory:
        temp = pathlib.Path(directory)
        log_path = temp / "events.log"
        bash_env = temp / "installer-test-env.sh"
        bash_env.write_text(r'''
systemctl() {
echo "SYSTEMCTL $*" >> "$INSTALLER_EVENT_LOG"
local scenario="${INSTALLER_TEST_SCENARIO:-loaded}"
if [[ "${1:-}" == "show" ]]; then
  case "$scenario" in
    show-fail)
      return 31
      ;;
    unknown)
      printf '%s\n' generated
      return 0
      ;;
    not-found)
      printf '%s\n' not-found
      return 0
      ;;
    *)
      printf '%s\n' loaded
      return 0
      ;;
  esac
fi
if [[ "${1:-}" == "stop" && "$scenario" == "stop-fail" ]]; then
  return 32
fi
if [[ "${1:-}" == "disable" && "$scenario" == "disable-fail" ]]; then
  return 33
fi
return 0
}
install() { echo "WRITE install $*" >> "$INSTALLER_EVENT_LOG"; return 0; }
chmod() { echo "WRITE chmod $*" >> "$INSTALLER_EVENT_LOG"; return 0; }
rsync() { echo "WRITE rsync $*" >> "$INSTALLER_EVENT_LOG"; return 0; }
chown() { echo "WRITE chown $*" >> "$INSTALLER_EVENT_LOG"; return 0; }
''', encoding="utf-8", newline="\n")
        env = {
            **os.environ,
            "BASH_ENV": bash_path(bash_env),
            "INSTALLER_EVENT_LOG": bash_path(log_path),
            "INSTALLER_TEST_SCENARIO": scenario,
            "STATIC_ROOT": bash_path(temp / "static"),
            "TOOLS_ROOT": bash_path(temp / "tools"),
            "CONFIG_ROOT": bash_path(temp / "config"),
            "LLM_ENV_FILE": bash_path(temp / "config" / "llm.env"),
            "WORKBENCH_ENV_FILE": bash_path(temp / "config" / "workbench-v20.env"),
            "JSONL_SOURCE": bash_path(temp / "missing.jsonl"),
        }
        if extra_env:
            env.update(extra_env)
        completed = subprocess.run(
            [bash, "--noprofile", "--norc", str(INSTALLER)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        events = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
        return completed, events


def assert_installer_default_does_not_touch_systemd():
    completed, events = run_installer_with_stubs("loaded")
    assert_true(completed.returncode == 0, "stubbed installer should succeed: " + completed.stderr)
    assert_true(not any(event.startswith("SYSTEMCTL ") for event in events),
                "default installer must not inspect, stop, disable, reload, enable, or start systemd units")
    assert_true(any(event.startswith("WRITE ") for event in events),
                "default installer should still prepare files")
    assert_true("service actions disabled" in completed.stdout,
                "default installer should explain the service-action opt-in")


def assert_installer_requires_service_action_opt_in_for_enable_start():
    for name in ("ENABLE_SIGNAL_AUDIT_TIMERS", "START_SIGNAL_AUDIT_TIMERS",
                 "ENABLE_WORKBENCH", "START_WORKBENCH"):
        completed, events = run_installer_with_stubs("loaded", {name: "1"})
        assert_true(completed.returncode != 0,
                    name + " should require APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS=1")
        assert_true(name + " requires APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS=1"
                    in (completed.stdout + completed.stderr),
                    name + " should emit a clear opt-in message")
        assert_true(not events, name + " rejection must happen before writes and systemd")


def assert_installer_opt_in_quiesces_units_before_writes():
    completed, events = run_installer_with_stubs(
        "loaded",
        {"APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS": "1"},
    )
    assert_true(completed.returncode == 0, "stubbed installer should succeed: " + completed.stderr)
    first_write = next((index for index, event in enumerate(events) if event.startswith("WRITE ")), None)
    assert_true(first_write is not None, "stubbed installer should reach a writer")
    before_writes = "\n".join(events[:first_write])
    for unit in FREEZE_UNITS:
        assert_true("SYSTEMCTL show " in before_writes and unit in before_writes,
                    "installer should inspect unit before writes: " + unit)
    for unit in (
        "signal-audit-materialize.service",
        "signal-audit-llm-review.service",
        "astra-workbench-v20.service",
    ):
        assert_true("SYSTEMCTL stop " in before_writes and unit in before_writes,
                    "installer should stop existing unit before writes: " + unit)
    for unit in ("signal-audit-materialize.timer", "signal-audit-llm-review.timer"):
        assert_true("SYSTEMCTL disable --now " + unit in before_writes,
                    "timer disable must happen before writes and must not be swallowed: " + unit)
        assert_true(sum(1 for event in events if event == "SYSTEMCTL disable --now " + unit) == 1,
                    "timer should be disabled once, before file writes: " + unit)


def assert_installer_skips_not_found_units_before_writes():
    completed, events = run_installer_with_stubs(
        "not-found",
        {"APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS": "1"},
    )
    assert_true(completed.returncode == 0,
                "not-found units should be skipped, not treated as failure: " + completed.stderr)
    first_write = next((index for index, event in enumerate(events) if event.startswith("WRITE ")), None)
    assert_true(first_write is not None, "stubbed installer should reach a writer")
    before_writes = events[:first_write]
    for unit in FREEZE_UNITS:
        assert_true(any(event.startswith("SYSTEMCTL show ") and unit in event for event in before_writes),
                    "not-found unit should still be inspected before writes: " + unit)
    assert_true(not any("SYSTEMCTL stop " in event for event in before_writes),
                "not-found units should not be stopped")
    assert_true(not any("SYSTEMCTL disable " in event for event in before_writes),
                "not-found old LLM timer should not be disabled")


def assert_installer_fail_closed_before_writes():
    for scenario in ("show-fail", "unknown", "stop-fail", "disable-fail"):
        completed, events = run_installer_with_stubs(
            scenario,
            {"APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS": "1"},
        )
        assert_true(completed.returncode != 0,
                    "installer should fail closed for systemctl " + scenario)
        assert_true(not any(event.startswith("WRITE ") for event in events),
                    "installer must not write files after " + scenario)
    completed, events = run_installer_with_stubs(
        "not-found",
        {"RUN_INITIAL_LLM_REVIEW": "1"},
    )
    assert_true(completed.returncode != 0,
                "retired RUN_INITIAL_LLM_REVIEW should be rejected before side effects")
    assert_true(not any(event.startswith("WRITE ") or event.startswith("SYSTEMCTL ") for event in events),
                "RUN_INITIAL_LLM_REVIEW rejection must happen before systemd or file writes")


def assert_installer_layout_detection_remains_dual_mode():
    script = INSTALLER.read_text(encoding="utf-8")
    assert_true(
        '"$FRONTEND_SRC/index.html" "$FRONTEND_SRC/app.js" "$FRONTEND_SRC/VERSION.json"'
        in script
        and "missing frontend asset" in script,
        "installer should require VERSION.json with the frontend runtime assets",
    )
    assert_true(
        'if [[ -d "$SCRIPT_DIR/../frontend" && -d "$SCRIPT_DIR/../tools" ]]; then'
        in script,
        "installer should still detect unpacked package layout",
    )
    assert_true(
        'DEPLOY_SRC="$SCRIPT_DIR"' in script
        and 'DEPLOY_SRC="$REPO_ROOT/deploy/signal_audit"' in script,
        "installer should keep package and source-repo deploy asset roots",
    )
    assert_true(
        'FRONTEND_SRC="$REPO_ROOT/deploy/signal_audit/frontend"' in script
        and 'FRONTEND_SRC="$REPO_ROOT/frontend"' in script,
        "installer should keep source-repo and package frontend roots",
    )


def main():
    assert_package_includes_release_assets()
    assert_package_rejects_unsafe_output_paths()
    assert_installer_preserves_runtime_cards_by_directory()
    assert_installer_default_does_not_touch_systemd()
    assert_installer_requires_service_action_opt_in_for_enable_start()
    assert_installer_opt_in_quiesces_units_before_writes()
    assert_installer_skips_not_found_units_before_writes()
    assert_installer_fail_closed_before_writes()
    assert_installer_layout_detection_remains_dual_mode()
    print("signal_audit_upgrade_preservation: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_upgrade_preservation: FAIL - " + str(exc))
        sys.exit(1)
