import pathlib
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
            package_root / "frontend" / "README.md",
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
            "frontend/README.md",
            "tools/signal_evidence_v2.py",
            "tools/signal_review_v2.py",
            "tools/signal_review_v2_runtime.py",
            "deploy/install_or_update.sh",
        ):
            assert_true(name in names, "zip asset missing: " + name)
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
    assert_installer_layout_detection_remains_dual_mode()
    print("signal_audit_upgrade_preservation: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_upgrade_preservation: FAIL - " + str(exc))
        sys.exit(1)
