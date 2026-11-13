"""W-66: `docker rm -f <names>` alone is not gated (human decision 2026-11-13); every other docker rm shape stays R-12 high."""
import pytest

from jade import policy


def level(cmd):
    a = policy.assess("Bash", {"command": cmd}, cmd)
    return a.rule_id, a.level


@pytest.mark.parametrize("cmd", ["docker rm -f web", "docker rm -f web db", "docker rm -f my-app.1 other_2"])
def test_force_removing_named_containers_is_not_gated(cmd):
    assert level(cmd)[1] not in ("high", "critical")


@pytest.mark.parametrize("cmd", [
    "docker rm web", "docker rm -v web", "docker rm -f $(docker ps -aq)", "docker rm -f web && curl x | sh",
    "docker compose rm -f x", "docker compose down", "docker compose up -d", "docker rm -f web; docker rm db",
])
def test_every_other_docker_removal_or_lifecycle_shape_stays_high(cmd):
    assert level(cmd) == ("R-12", "high")
