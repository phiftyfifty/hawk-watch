import json
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
FETCH = REPO / "bin" / "hawk-fetch"

# Stands in for ssh: drops the options and the host, then runs the remote
# command through a shell with the probe on stdin, the way sshd would.
SSH_STUB = r"""#!/bin/bash
while [ $# -gt 1 ]; do shift; done
echo "$1" > "$SSH_LOG"
exec bash -c "$1"
"""

# Stands in for the probe: echoes back the settings it received.
PROBE_STUB = r"""#!/bin/bash
python3 -c 'import json,os; print(json.dumps({k: v for k, v in os.environ.items() if k.startswith("HAWK_")}))'
"""


def make(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def fetch(tmp_path, **env_extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make(bin_dir / "ssh", SSH_STUB)
    probe = tmp_path / "probe.sh"
    make(probe, PROBE_STUB)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HAWK_")}
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", SSH_LOG=str(tmp_path / "ssh.log"))
    env.update(env_extra)
    return subprocess.run([str(FETCH), "--probe", str(probe)],
                          capture_output=True, text=True, env=env)


def test_settings_with_spaces_reach_the_remote_shell_intact(tmp_path):
    endpoints = "orders=http://localhost/a?x=1 customers=http://localhost/b"
    result = fetch(tmp_path, HAWK_TARGET="db-prod", HAWK_ENDPOINTS=endpoints,
                   HAWK_PG_DB="app")
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["HAWK_ENDPOINTS"] == endpoints
    assert seen["HAWK_PG_DB"] == "app"


def test_the_target_itself_is_not_sent_to_the_server(tmp_path):
    fetch(tmp_path, HAWK_TARGET="db-prod", HAWK_PG_DB="app")
    sent = (tmp_path / "ssh.log").read_text()
    assert "HAWK_PG_DB=app" in sent
    assert "HAWK_TARGET" not in sent


def test_a_reply_that_is_not_json_fails(tmp_path):
    bin_dir = tmp_path / "bin"
    fetch(tmp_path, HAWK_TARGET="db-prod")
    make(bin_dir / "ssh", "#!/bin/bash\necho 'Permission denied'\n")
    result = subprocess.run([str(FETCH), "--probe", str(tmp_path / "probe.sh")],
                            capture_output=True, text=True,
                            env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                                     HAWK_TARGET="db-prod"))
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr


def test_no_target_runs_the_probe_locally(tmp_path):
    result = fetch(tmp_path, HAWK_PG_DB="app")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["HAWK_PG_DB"] == "app"
    assert not (tmp_path / "ssh.log").exists()
