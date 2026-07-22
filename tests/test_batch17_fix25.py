"""Batch 17 — FIX-25 release safety (OPS-02/03/04, ARCH-01, TEST-02).

These are CI guards for the deploy path, not runtime behaviour:
  · exactly one Alembic head (a divergent/duplicate migration fails CI before it
    can reach the boot-time `alembic upgrade head`);
  · the image actually runs migrations fail-closed at boot;
  · the rebuildable-prod artifacts are committed."""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"divergent Alembic heads (a bad migration merge): {heads}"


def test_boot_runs_migrations_fail_closed():
    df = _read("deploy/aws/Dockerfile")
    assert 'ENTRYPOINT ["/entrypoint.sh"]' in df
    assert "COPY deploy/aws/entrypoint.sh /entrypoint.sh" in df
    ep = _read("deploy/aws/entrypoint.sh")
    assert ep.startswith("#!/bin/sh")
    assert "set -e" in ep                      # fail-closed
    assert "alembic upgrade head" in ep
    assert "RUN_MIGRATIONS" in ep              # scheduler opt-out


def test_rebuildable_prod_artifacts_committed():
    for rel in ("deploy/aws/Caddyfile", "deploy/aws/user-data.sh",
                "deploy/aws/build_and_push.sh", "deploy/aws/cutover.sh",
                "deploy/aws/smoke.sh", "deploy/aws/ROLLBACK.md",
                ".github/workflows/deploy.yml"):
        assert os.path.exists(os.path.join(ROOT, rel)), f"missing committed deploy artifact: {rel}"


def test_release_scripts_are_valid_shell():
    for rel in ("entrypoint.sh", "build_and_push.sh", "cutover.sh", "smoke.sh", "user-data.sh"):
        p = os.path.join(ROOT, "deploy/aws", rel)
        assert subprocess.run(["bash", "-n", p]).returncode == 0, f"shell syntax error in {rel}"


def test_build_script_tags_by_sha_with_gate():
    b = _read("deploy/aws/build_and_push.sh")
    assert "git rev-parse --short HEAD" in b     # SHA tag
    assert "pre-push gate" in b                   # the no-op-build guard
    assert "git status --porcelain" in b          # refuse to tag a dirty tree


def test_cutover_scheduler_skips_migrations():
    c = _read("deploy/aws/cutover.sh")
    assert "RUN_MIGRATIONS=0" in c                # only web migrates
    assert "--network edge" in c
    assert "report healthy" in c                  # fail-closed verification
