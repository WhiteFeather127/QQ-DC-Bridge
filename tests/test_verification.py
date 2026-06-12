from __future__ import annotations

import pytest

from bridge.verification import VerificationManager


class TestVerificationManager:
    def test_create_and_verify(self) -> None:
        vm = VerificationManager()
        code = vm.create(
            source_platform="qq",
            source_user_id="user_1",
            target_platform="discord",
            target_user_id="discord_user_1",
        )
        assert len(code) == 6
        assert code.isdigit()

        result = vm.verify(source_platform="qq", source_user_id="user_1", code=code)
        assert result is not None
        target_platform, target_user_id = result
        assert target_platform == "discord"
        assert target_user_id == "discord_user_1"

    def test_verify_wrong_code_allows_retry(self) -> None:
        vm = VerificationManager()
        code = vm.create(
            source_platform="qq",
            source_user_id="user_1",
            target_platform="discord",
            target_user_id="discord_user_1",
        )
        wrong_code = "000000" if code != "000000" else "000001"
        result = vm.verify(source_platform="qq", source_user_id="user_1", code=wrong_code)
        assert result is None

        result = vm.verify(source_platform="qq", source_user_id="user_1", code=code)
        assert result is not None

    def test_verify_expired_code(self) -> None:
        vm = VerificationManager()
        code = vm.create(
            source_platform="qq",
            source_user_id="user_1",
            target_platform="discord",
            target_user_id="discord_user_1",
        )
        key = f"qq:user_1"
        vc = vm._pending[key]
        vc.expires_at = -1

        result = vm.verify(source_platform="qq", source_user_id="user_1", code=code)
        assert result is None

    def test_verify_no_pending_code(self) -> None:
        vm = VerificationManager()
        result = vm.verify(source_platform="qq", source_user_id="nonexistent", code="123456")
        assert result is None

    def test_cancel(self) -> None:
        vm = VerificationManager()
        vm.create(
            source_platform="qq",
            source_user_id="user_1",
            target_platform="discord",
            target_user_id="discord_user_1",
        )
        vm.cancel(source_platform="qq", source_user_id="user_1")
        result = vm.verify(source_platform="qq", source_user_id="user_1", code="000000")
        assert result is None

    def test_cancel_nonexistent_does_not_raise(self) -> None:
        vm = VerificationManager()
        vm.cancel(source_platform="qq", source_user_id="nonexistent")

    def test_verify_consumes_code_on_success(self) -> None:
        vm = VerificationManager()
        code = vm.create(
            source_platform="qq",
            source_user_id="user_1",
            target_platform="discord",
            target_user_id="discord_user_1",
        )
        vm.verify(source_platform="qq", source_user_id="user_1", code=code)
        result = vm.verify(source_platform="qq", source_user_id="user_1", code=code)
        assert result is None

    def test_too_many_attempts_removes_code(self) -> None:
        vm = VerificationManager()
        code = vm.create(
            source_platform="qq",
            source_user_id="user_1",
            target_platform="discord",
            target_user_id="discord_user_1",
        )
        wrong = "000000" if code != "000000" else "000001"
        for _ in range(5):
            result = vm.verify(source_platform="qq", source_user_id="user_1", code=wrong)
            assert result is None

        result = vm.verify(source_platform="qq", source_user_id="user_1", code=code)
        assert result is None

    def test_multiple_users_independent(self) -> None:
        vm = VerificationManager()
        code1 = vm.create(
            source_platform="qq",
            source_user_id="user_a",
            target_platform="discord",
            target_user_id="discord_a",
        )
        code2 = vm.create(
            source_platform="discord",
            source_user_id="user_b",
            target_platform="qq",
            target_user_id="10002",
        )

        result1 = vm.verify(source_platform="qq", source_user_id="user_a", code=code1)
        assert result1 == ("discord", "discord_a")

        result2 = vm.verify(source_platform="discord", source_user_id="user_b", code=code2)
        assert result2 == ("qq", "10002")
