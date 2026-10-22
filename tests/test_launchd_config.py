"""The launchd configuration: every job runs an existing wrapper under /bin/bash,
and install/uninstall cover exactly the jobs that exist."""
import plistlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHD = REPO / "scripts" / "launchd"
LIVE = "/Users/quis/sports-edge"


def plists():
    return sorted(LAUNCHD.glob("*.plist"))


def test_every_plist_runs_an_existing_wrapper_under_bash():
    assert plists()
    for p in plists():
        d = plistlib.loads(p.read_bytes())
        assert d["Label"] == p.stem
        bash, script = d["ProgramArguments"]
        assert bash == "/bin/bash"
        assert script.startswith(f"{LIVE}/scripts/")
        assert (REPO / script[len(LIVE) + 1:]).is_file(), script
        assert d["WorkingDirectory"] == LIVE
        assert d["StandardOutPath"].startswith(f"{LIVE}/logs/")
        assert d["StandardErrorPath"].startswith(f"{LIVE}/logs/")


def test_install_and_uninstall_list_exactly_the_jobs_that_exist():
    labels = {p.stem for p in plists()}
    for name in ("install.sh", "uninstall.sh"):
        text = (LAUNCHD / name).read_text()
        listed = set(re.search(r"for label in ([^;]+);", text).group(1).split())
        assert listed == labels, name


def test_digest_fires_at_7_30_system_time():
    d = plistlib.loads((LAUNCHD / "com.quis.sportsedge.digest.plist").read_bytes())
    assert d["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert d["ProgramArguments"][1] == f"{LIVE}/scripts/run_digest.sh"
