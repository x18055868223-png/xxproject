from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "signal_audit"
PACKAGE = DEPLOY / "package_signal_audit.ps1"
INSTALLER = DEPLOY / "install_or_update.sh"
ENV_EXAMPLE = DEPLOY / "workbench-v20.env.example"
APACHE_PRIVATE = DEPLOY / "apache-private-workbench.conf.example"

REQUIRED_WORKBENCH_MODULES = (
    "astra_workbench_v20.py",
    "astra_workbench_session_v1.py",
    "astra_workbench_reading_v1.py",
    "astra_locked_candidate_store_v1.py",
    "astra_locked_candidate_compare_v1.py",
    "astra_observation_replay_v1.py",
    "astra_map_data_server_v24.py",
    "astra_wpem_reference_v1.py",
    "astra_wpem_results_v1.py",
    "astra_joint_data.py",
    "astra_joint_dataset.py",
    "astra_joint_sources.py",
    "astra_joint_v11_contract.py",
    "astra_joint_v11_data.py",
    "astra_joint_v11_inference.py",
    "astra_joint_v11_model.py",
)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    return path.read_text(encoding="utf-8")


def test_package_and_installer_ship_same_observation_modules():
    package = read(PACKAGE)
    installer = read(INSTALLER)
    for module in REQUIRED_WORKBENCH_MODULES:
        assert_true(module in package, "package script missing module: " + module)
        assert_true(module in installer, "installer missing module: " + module)

    assert_true(
        '"apache-private-workbench.conf.example"' in package,
        "package should ship the private Apache workbench proxy example",
    )
    assert_true(
        "repo_root =" not in package and "git_top_level =" not in package,
        "package manifest should not record local absolute source paths",
    )
    assert_true(
        "local_absolute_paths_not_recorded" in package,
        "package manifest should state its path policy",
    )
    assert_true(
        "Update-PackagedText" in package and "offline/astra-joint-v1" in package,
        "package should sanitize copied source defaults before manifesting the RC bytes",
    )
    assert_true(
        "sanitize_installed_source_defaults" in installer
        and "offline/astra-joint-v1" in installer,
        "installer should sanitize source defaults in the installed tools copy",
    )


def test_installer_service_actions_are_explicit_opt_in():
    installer = read(INSTALLER)
    assert_true(
        'APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS="${APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS:-0}"'
        in installer,
        "installer should default service actions to off",
    )
    first_write = min(
        index for index in (
            installer.find('\ninstall -d "$STATIC_ROOT"'),
            installer.find("\nrsync "),
        ) if index >= 0
    )
    guard_start = installer.find('if [[ "$APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS" == "1" ]]')
    quiesce_call = installer.find("disable_loaded_timer signal-audit-materialize.timer")
    unit_write = installer.find('install -m 0644 "$MATERIALIZE_SERVICE_SRC" /etc/systemd/system')
    assert_true(guard_start >= 0, "installer should guard systemd operations")
    assert_true(
        guard_start < quiesce_call < first_write,
        "unit quiesce should only happen inside the explicit opt-in guard before writes",
    )
    assert_true(
        guard_start < unit_write,
        "systemd unit writes should only happen inside the explicit opt-in guard",
    )
    assert_true(
        "requires APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS=1" in installer,
        "enable/start flags should reject missing service-action opt-in",
    )
    assert_true(
        first_write >= 0 and guard_start < first_write,
        "service-action choice should be made before file writes",
    )


def test_workbench_env_is_private_session_and_zero_llm_by_default():
    env = read(ENV_EXAMPLE)
    assert_true("ASTRA_WORKBENCH_PUBLIC_ORIGIN=" in env,
                "env example should expose the explicit public origin knob")
    assert_true("ASTRA_WORKBENCH_SESSION_TTL_SECONDS=43200" in env,
                "env example should include a bounded session TTL")
    assert_true("ASTRA_WORKBENCH_MAX_SESSIONS=32" in env,
                "env example should bound server-side sessions")
    assert_true("ASTRA_WORKBENCH_DAILY_HTTP_LIMIT=0" in env,
                "default workbench config should allow zero model HTTP calls")
    assert_true(re.search(r"^ASTRA_WORKBENCH_LLM_MODEL=$", env, flags=re.M),
                "default workbench config should not select a provider model")
    assert_true("# LLM_API_KEY=" in env and "\nLLM_API_KEY=" not in env,
                "provider key must stay commented in the example")


def test_private_apache_proxy_is_documented_as_existing_tls_boundary():
    config = read(APACHE_PRIVATE)
    assert_true("existing private TLS vhost" in config,
                "Apache workbench proxy should require an existing private TLS vhost")
    assert_true("does not create users, passwords" in config,
                "Apache workbench proxy should not imply a new account system")
    assert_true("AuthMerging And" in config,
                "Apache workbench proxy must merge with the vhost's existing private auth")
    assert_true("forwarded\n# headers are not an authorization source" in config,
                "Apache workbench proxy should not imply forwarded headers are trusted for auth")
    assert_true("ProxyPass \"/api/workbench/session\"" in config,
                "Apache private proxy should include the session route")
    assert_true("ProxyPass \"/api/workbench/locked-candidates\"" in config,
                "Apache private proxy should include locked candidate routes")
    assert_true("ProxyPass \"/api/workbench/wpem-proof/\"" in config,
                "Apache private proxy should include full-proof route only under private TLS")
    assert_true("Require expr %{HTTPS} == \"on\"" in config,
                "Apache private proxy should refuse non-HTTPS access")


if __name__ == "__main__":
    test_package_and_installer_ship_same_observation_modules()
    test_installer_service_actions_are_explicit_opt_in()
    test_workbench_env_is_private_session_and_zero_llm_by_default()
    test_private_apache_proxy_is_documented_as_existing_tls_boundary()
    print("astra_observation_rc_package: PASS")
