from __future__ import annotations

import json

import pytest

from bridge.bind_manager import BindError, BindManager


class TestBindManager:
    def test_bind_and_get_counterpart(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        assert bm.get_counterpart("qq", "10001") == "discord_user_1"
        assert bm.get_counterpart("discord", "discord_user_1") == "10001"

    def test_bind_raises_on_duplicate_source(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        with pytest.raises(BindError):
            bm.bind(qq_id="10001", discord_id="discord_user_2")

    def test_bind_raises_on_duplicate_target(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        with pytest.raises(BindError):
            bm.bind(qq_id="10002", discord_id="discord_user_1")

    def test_is_bound(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        assert not bm.is_bound("qq", "10001")
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        assert bm.is_bound("qq", "10001")
        assert bm.is_bound("discord", "discord_user_1")

    def test_unbind_by_qq(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        assert bm.unbind("qq", "10001") is True
        assert bm.get_counterpart("qq", "10001") is None
        assert bm.get_counterpart("discord", "discord_user_1") is None

    def test_unbind_by_discord(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        assert bm.unbind("discord", "discord_user_1") is True
        assert bm.get_counterpart("qq", "10001") is None
        assert bm.get_counterpart("discord", "discord_user_1") is None

    def test_unbind_unbound_user_returns_false(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        assert bm.unbind("qq", "99999") is False
        assert bm.unbind("discord", "nonexistent") is False

    def test_get_counterpart_unknown_platform(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        assert bm.get_counterpart("unknown", "x") is None

    def test_get_all_bindings(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        bm.bind(qq_id="10002", discord_id="discord_user_2")
        bindings = bm.get_all_bindings()
        assert len(bindings) == 2
        assert {"qq": "10001", "discord": "discord_user_1"} in bindings
        assert {"qq": "10002", "discord": "discord_user_2"} in bindings

    def test_bidirectional_mapping_consistency(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")
        assert bm._qq_to_discord["10001"] == "discord_user_1"
        assert bm._discord_to_qq["discord_user_1"] == "10001"

    def test_persistence_save_and_load(self, tmp_path) -> None:
        bm1 = BindManager(data_dir=str(tmp_path))
        bm1.bind(qq_id="10001", discord_id="discord_user_1")
        bm1.bind(qq_id="10002", discord_id="discord_user_2")

        bm2 = BindManager(data_dir=str(tmp_path))
        assert bm2.get_counterpart("qq", "10001") == "discord_user_1"
        assert bm2.get_counterpart("qq", "10002") == "discord_user_2"
        assert bm2.get_counterpart("discord", "discord_user_1") == "10001"

    def test_persistence_overwrite_same_day(self, tmp_path) -> None:
        bm1 = BindManager(data_dir=str(tmp_path))
        bm1.bind(qq_id="10001", discord_id="discord_user_1")

        bm2 = BindManager(data_dir=str(tmp_path))
        assert bm2.get_counterpart("qq", "10001") == "discord_user_1"

        bm2.bind(qq_id="10003", discord_id="discord_user_3")
        bm3 = BindManager(data_dir=str(tmp_path))
        assert bm3.get_counterpart("qq", "10001") == "discord_user_1"
        assert bm3.get_counterpart("qq", "10003") == "discord_user_3"
