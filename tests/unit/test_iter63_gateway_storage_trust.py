"""Iteration 63 storage trust: OS-enforced isolation of Gateway storage.

User decision 2026-11-09: the Gateway runs as its own OS user, its storage is
writable only by that user and startup refuses otherwise.
"""

import os
import stat

import pytest

from bt_api_py.gateway_storage_trust import (
    GatewayStorageTrustError,
    assert_gateway_storage_isolated,
)

pytestmark = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX permission model")


def _isolated(tmp_path):
    root = tmp_path / "gateway"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _code(directories, files=(), **kwargs):
    with pytest.raises(GatewayStorageTrustError) as error:
        assert_gateway_storage_isolated(directories, files, **kwargs)
    return error.value.code


def test_isolated_directory_and_sqlite_files_pass(tmp_path):
    root = _isolated(tmp_path)
    journal = root / "orders.sqlite"
    journal.write_bytes(b"")
    (root / "orders.sqlite-wal").write_bytes(b"")
    assert_gateway_storage_isolated([root], [journal, root / "absent.sqlite"])


@pytest.mark.parametrize("mode", [0o770, 0o707, 0o777])
def test_directory_writable_by_others_is_refused(tmp_path, mode):
    root = _isolated(tmp_path)
    os.chmod(root, mode)
    try:
        assert _code([root]) == "STORAGE_WRITABLE_BY_OTHERS"
    finally:
        os.chmod(root, 0o700)


@pytest.mark.parametrize("side", ["", "-wal", "-shm", "-journal"])
def test_group_writable_database_or_side_file_is_refused(tmp_path, side):
    root = _isolated(tmp_path)
    journal = root / "orders.sqlite"
    journal.write_bytes(b"")
    target = root / ("orders.sqlite" + side)
    target.write_bytes(b"")
    os.chmod(target, 0o664)
    assert _code([root], [journal]) == "STORAGE_WRITABLE_BY_OTHERS"


def test_symlinked_entry_or_directory_is_refused(tmp_path):
    root = _isolated(tmp_path)
    other = tmp_path / "elsewhere.sqlite"
    other.write_bytes(b"")
    (root / "orders.sqlite").symlink_to(other)
    assert _code([root]) == "STORAGE_SYMLINK"
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real)
    assert _code([linked]) == "STORAGE_NOT_DIRECTORY"  # a storage directory is never a link


def test_storage_owned_by_another_user_is_refused(tmp_path):
    root = _isolated(tmp_path)
    # The current user plays "another user" relative to a different Gateway euid.
    assert _code([root], euid=os.geteuid() + 1) in {
        "STORAGE_ANCESTOR_UNTRUSTED", "STORAGE_OWNER_MISMATCH"}


def test_ancestor_writable_by_others_needs_the_sticky_bit(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    root = parent / "gateway"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    try:
        os.chmod(parent, 0o777)  # noqa: S103 - deliberately permissive to test refusal
        assert _code([root]) == "STORAGE_ANCESTOR_WRITABLE_BY_OTHERS"
        os.chmod(parent, 0o777 | stat.S_ISVTX)  # noqa: S103 - deliberately permissive to test refusal
        assert_gateway_storage_isolated([root])  # like /tmp: others cannot rename our entries
    finally:
        os.chmod(parent, 0o755)  # noqa: S103 - deliberately permissive to test refusal


def test_missing_relative_or_non_directory_paths_are_refused(tmp_path):
    root = _isolated(tmp_path)
    assert _code([root / "missing"]) == "STORAGE_DIRECTORY_MISSING"
    assert _code(["relative/dir"]) == "STORAGE_PATH_INVALID"
    file_path = root / "file"
    file_path.write_bytes(b"")
    assert _code([file_path]) == "STORAGE_NOT_DIRECTORY"
    assert _code([root], ["relative.sqlite"]) == "STORAGE_PATH_INVALID"


def test_error_codes_carry_no_paths(tmp_path):
    root = _isolated(tmp_path)
    os.chmod(root, 0o777)  # noqa: S103 - deliberately permissive to test refusal
    try:
        with pytest.raises(GatewayStorageTrustError) as error:
            assert_gateway_storage_isolated([root])
        assert str(tmp_path) not in str(error.value) and str(error.value) == error.value.code
    finally:
        os.chmod(root, 0o700)
