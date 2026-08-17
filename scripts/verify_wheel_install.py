#!/usr/bin/env python3
"""Build, install, and smoke the wheel in an isolated virtual environment."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temporary:
    temp = Path(temporary)
    dist = temp / "dist"
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(root), "--wheel-dir", str(dist)], check=True)
    wheel = next(dist.glob("juno_kanban-*.whl"))
    venv = temp / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / "bin/python"
    executable = venv / "bin/juno-kanban"
    subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(dist), str(wheel)], check=True)
    probe = subprocess.check_output([
        str(python), "-c",
        "import json,kanban.cache,kanban.codec,kanban.ledger,kanban.storage; "
        "print(json.dumps({'modules': True}))",
    ], text=True)
    help_result = subprocess.run([str(executable), "--help"], text=True, capture_output=True, check=True)
    assert all(command in help_result.stdout for command in
               ("convert", "compatibility", "archive-pack", "archive-search"))
    archive_help = subprocess.run([str(executable), "archive-pack", "--help"], text=True,
                                  capture_output=True, check=True)
    assert all(action in archive_help.stdout for action in ("plan", "create", "doctor"))
    search_help = subprocess.run([str(executable), "archive-search", "--help"], text=True,
                                 capture_output=True, check=True)
    assert "--projection" in search_help.stdout and "--limit" in search_help.stdout
    for arguments in (("archive-pack", "plan", "--older-tha", "90d", "--report", str(temp / "p")),
                      ("archive-pack", "create", "--pla", str(temp / "p"), "--report", str(temp / "r")),
                      ("archive-search", "--lim", "1"),
                      ("archive-pack", "plan", "--force", "--report", str(temp / "p"))):
        refused = subprocess.run([str(executable), *arguments], text=True, capture_output=True,
                                 stdin=subprocess.DEVNULL)
        assert refused.returncode != 0, arguments
    project = temp / "doctor-project"
    orphan = project / ".juno_task/archive/2026/01/pack-orphan.manifest.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("{}\n", encoding="utf-8")
    environment = dict(os.environ, JUNO_TASK_ROOT=str(project))
    doctor = subprocess.run([str(executable), "archive-pack", "doctor"], cwd=project,
                            env=environment, text=True, capture_output=True,
                            stdin=subprocess.DEVNULL)
    assert doctor.returncode != 0
    assert "incomplete archive artifact triplet" in doctor.stdout
    assert "missing pack, checksum" in doctor.stdout
    print(json.dumps({"wheel": wheel.name, "import_probe": json.loads(probe),
                      "entry_point": True, "archive_public_help": True,
                      "exact_option_refusals": 4, "incomplete_triplet_doctor": True}))
