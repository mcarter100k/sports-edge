"""Positive controls: the isolation harness must be able to catch what it guards."""
import pytest

from conftest import network_probe


def test_network_is_blocked_in_unit_tests():
    with pytest.raises(PermissionError, match="network blocked"):
        network_probe()


def test_each_test_has_its_own_data_dir(isolated):
    from sports_edge import config
    assert config.data_dir() == isolated / "data"
    assert config.logs_dir() == isolated / "logs"
    assert config.state_dir() == isolated / "state"
