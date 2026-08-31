"""Security invariants shared by every file-backed capability."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_contained_path_rejects_path_components_that_can_escape(tmp_path):
    from shared.filesystem import contained_path

    for value in ("../outside", "..", "/absolute", r"..\outside", "nested/name", ""):
        with pytest.raises(ValueError):
            contained_path(tmp_path, value)


def test_private_directory_and_atomic_file_permissions(tmp_path):
    from shared.filesystem import atomic_write_text, ensure_private_dir

    private = ensure_private_dir(tmp_path / "private")
    output = private / "record.json"
    atomic_write_text(output, "{}")

    if os.name != "nt":
        assert _mode(private) == 0o700
        assert _mode(output) == 0o600


def test_exclusive_private_text_creation_never_overwrites(tmp_path):
    from shared.filesystem import exclusive_write_text

    output = tmp_path / "private" / "plan.csv"
    exclusive_write_text(output, "first")

    with pytest.raises(FileExistsError):
        exclusive_write_text(output, "second")

    assert output.read_text() == "first"
    if os.name != "nt":
        assert _mode(output.parent) == 0o700
        assert _mode(output) == 0o600


def test_remove_files_ignores_missing_paths(tmp_path):
    from shared.filesystem import remove_files

    output = tmp_path / "plan.csv"
    output.write_text("plan")

    remove_files([output, tmp_path / "already-missing.csv"])

    assert not output.exists()


def test_campaign_store_rejects_traversal_and_writes_privately(tmp_path, monkeypatch):
    from open_pharma_plugins_campaign_studio._campaign_store import save_artifact

    root = tmp_path / "campaigns"
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(root))

    with pytest.raises(ValueError):
        save_artifact("../../escaped", "campaign-brief.json", {"bad": True})
    assert not (tmp_path / "escaped" / "campaign-brief.json").exists()

    output = save_artifact("safe_campaign", "campaign-brief.json", {"ok": True})
    if os.name != "nt":
        assert _mode(root) == 0o700
        assert _mode(output.parent) == 0o700
        assert _mode(output) == 0o600


def test_field_store_rejects_traversal_reads(tmp_path, monkeypatch):
    from open_pharma_plugins_field_training._content_store import load_document

    store = tmp_path / "training"
    monkeypatch.setenv("OPEN_PHARMA_TRAINING_CONTENT_DIR", str(store))
    (tmp_path / "secret.json").write_text('{"document_id":"secret","pages":[]}')

    with pytest.raises(ValueError):
        load_document("../secret")


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
def test_secure_atomic_publish_rejects_post_validation_target_replacement_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    from shared import filesystem

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"FIRST OLD")
    second.write_bytes(b"SECOND OLD")
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    expectations = {
        first: filesystem.capture_file_expectation(first, limit=100),
        second: filesystem.capture_file_expectation(second, limit=100),
    }
    replacement = tmp_path / ".second.concurrent-source"
    replacement.write_bytes(b"SECOND CONCURRENT")
    real_replace = os.replace
    injected = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def replace_with_race(source, destination, **kwargs) -> None:
        nonlocal injected
        if (
            not injected
            and Path(source).name == second.name
            and Path(destination).name.startswith(f".{second.name}.backup.")
        ):
            injected = True
            directory_fd = kwargs.get("src_dir_fd")
            if directory_fd is None:
                real_replace(replacement, second)
            else:
                real_replace(
                    replacement.name,
                    second.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(filesystem.os, "replace", replace_with_race)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert injected is True
    assert captured.value.reason == "target_changed"
    assert captured.value.path == second
    assert first.read_bytes() == b"FIRST OLD"
    assert second.read_bytes() == b"SECOND CONCURRENT"
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".")]


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
def test_secure_atomic_publish_never_clobbers_a_target_created_at_final_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    from shared import filesystem

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"FIRST OLD")
    second.write_bytes(b"SECOND OLD")
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    expectations = {
        first: filesystem.capture_file_expectation(first, limit=100),
        second: filesystem.capture_file_expectation(second, limit=100),
    }
    concurrent = b"SECOND CONCURRENT AT PLACEMENT"
    real_replace = os.replace
    real_link = os.link
    injected = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def is_second_placement(source, destination) -> bool:
        source_name = Path(source).name
        return (
            Path(destination).name == second.name
            and source_name.startswith(f".{second.name}.")
            and ".backup." not in source_name
            and ".recovery." not in source_name
        )

    def create_concurrent(directory_fd: int | None) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = (
            os.open(second.name, flags, 0o600, dir_fd=directory_fd)
            if directory_fd is not None
            else os.open(second, flags, 0o600)
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(concurrent)

    def replace_with_race(source, destination, **kwargs) -> None:
        nonlocal injected
        if not injected and is_second_placement(source, destination):
            injected = True
            create_concurrent(kwargs.get("dst_dir_fd"))
        real_replace(source, destination, **kwargs)

    def link_with_race(source, destination, **kwargs) -> None:
        nonlocal injected
        if not injected and is_second_placement(source, destination):
            injected = True
            create_concurrent(kwargs.get("dst_dir_fd"))
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(filesystem.os, "replace", replace_with_race)
    monkeypatch.setattr(filesystem.os, "link", link_with_race)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert injected is True
    assert captured.value.reason == "target_changed"
    assert captured.value.path == second
    assert first.read_bytes() == b"FIRST OLD"
    assert second.read_bytes() == concurrent
    assert len(captured.value.recovery_paths) == 1
    recovery = captured.value.recovery_paths[0]
    assert recovery.name.startswith(f".{second.name}.recovery.")
    assert recovery.read_bytes() == b"SECOND OLD"
    assert [path for path in tmp_path.iterdir() if path.name.startswith(".")] == [recovery]


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
@pytest.mark.parametrize("failure_errno", [errno.EIO, errno.ENOTSUP], ids=["io-error", "unsupported-link"])
def test_secure_atomic_publish_retains_original_backup_when_recovery_link_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_fallback: bool,
    failure_errno: int,
) -> None:
    """Removing a failed recovery backup would silently destroy the original bytes."""
    from shared import filesystem

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"FIRST OLD")
    second.write_bytes(b"SECOND OLD")
    first.chmod(0o600)
    second.chmod(0o600)
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    expectations = {
        first: filesystem.capture_file_expectation(first, limit=100),
        second: filesystem.capture_file_expectation(second, limit=100),
    }
    concurrent = b"SECOND CONCURRENT"
    real_link = os.link
    injected_placement = False
    injected_recovery = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def link_with_failures(source, destination, **kwargs) -> None:
        nonlocal injected_placement, injected_recovery
        source_name = Path(source).name
        destination_name = Path(destination).name
        if (
            not injected_placement
            and destination_name == second.name
            and source_name.startswith(f".{second.name}.")
            and ".backup." not in source_name
            and ".recovery." not in source_name
        ):
            injected_placement = True
            directory_fd = kwargs.get("dst_dir_fd")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = (
                os.open(second.name, flags, 0o600, dir_fd=directory_fd)
                if directory_fd is not None
                else os.open(second, flags, 0o600)
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(concurrent)
        if ".backup." in source_name and ".recovery." in destination_name:
            injected_recovery = True
            raise OSError(failure_errno, "injected recovery-link failure")
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(filesystem.os, "link", link_with_failures)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert injected_placement is True
    assert injected_recovery is True
    assert captured.value.reason == "target_changed"
    assert first.read_bytes() == b"FIRST OLD"
    assert second.read_bytes() == concurrent
    assert first.stat().st_mode & 0o777 == 0o600
    assert second.stat().st_mode & 0o777 == 0o600
    assert len(captured.value.recovery_paths) == 1
    recovery = captured.value.recovery_paths[0]
    assert recovery.exists()
    assert recovery.read_bytes() == b"SECOND OLD"
    assert recovery.stat().st_mode & 0o777 == 0o600
    assert [path for path in tmp_path.iterdir() if path.name.startswith(".")] == [recovery]


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
def test_secure_atomic_publish_quarantines_concurrent_rollback_replacement_without_deleting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    """Replacing a staged target after a match check must not be deleted by rollback."""
    from shared import filesystem

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"FIRST OLD")
    second.write_bytes(b"SECOND OLD")
    first.chmod(0o600)
    second.chmod(0o600)
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    expectations = {
        first: filesystem.capture_file_expectation(first, limit=100),
        second: filesystem.capture_file_expectation(second, limit=100),
    }
    first_concurrent = b"FIRST CONCURRENT DURING ROLLBACK"
    second_concurrent = b"SECOND CONCURRENT AT PLACEMENT"
    first_source = tmp_path / ".first.concurrent-source"
    first_source.write_bytes(first_concurrent)
    first_source.chmod(0o600)
    real_link = os.link
    real_replace = os.replace
    injected_failure = False
    injected_rollback = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)
    real_dirfd_quarantine = getattr(filesystem, "_quarantine_dirfd_entry", None)

    def inject_first_replacement(directory_fd: int | None) -> None:
        nonlocal injected_rollback
        if injected_rollback:
            return
        injected_rollback = True
        if directory_fd is None:
            real_replace(first_source, first)
        else:
            real_replace(
                first_source.name,
                first.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )

    def quarantine_dirfd_with_race(directory_fd, name, expected, reported_path):
        if name == first.name:
            inject_first_replacement(directory_fd)
        assert real_dirfd_quarantine is not None
        return real_dirfd_quarantine(directory_fd, name, expected, reported_path)

    def link_with_placement_failure(source, destination, **kwargs) -> None:
        nonlocal injected_failure
        source_name = Path(source).name
        if (
            not injected_failure
            and Path(destination).name == second.name
            and source_name.startswith(f".{second.name}.")
            and ".backup." not in source_name
            and ".recovery." not in source_name
        ):
            injected_failure = True
            directory_fd = kwargs.get("dst_dir_fd")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = (
                os.open(second.name, flags, 0o600, dir_fd=directory_fd)
                if directory_fd is not None
                else os.open(second, flags, 0o600)
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(second_concurrent)
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(filesystem, "_quarantine_dirfd_entry", quarantine_dirfd_with_race, raising=False)
    monkeypatch.setattr(filesystem.os, "link", link_with_placement_failure)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert injected_failure is True
    assert injected_rollback is True
    assert first.read_bytes() == b"FIRST OLD"
    assert second.read_bytes() == second_concurrent
    recoveries = list(captured.value.recovery_paths)
    residues = list(captured.value.residue_paths)
    assert {path.read_bytes() for path in recoveries} == {b"SECOND OLD"}
    assert {path.read_bytes() for path in residues} == {first_concurrent}
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in [first, second, *recoveries, *residues])
    assert {path for path in tmp_path.iterdir() if path.name.startswith(".")} == {*recoveries, *residues}


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
@pytest.mark.parametrize("create_securely", [False, True], ids=["existing-nested", "created-nested"])
def test_secure_atomic_publish_rejects_relocated_parent_reintroduced_through_ancestor_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_fallback: bool,
    create_securely: bool,
) -> None:
    """The same final directory inode is unsafe when its lexical ancestor becomes a symlink."""
    from shared import filesystem

    ancestor = tmp_path / "lexical"
    parent = ancestor / "nested" / "handoff"
    if create_securely:
        filesystem.prepare_secure_directory(parent)
    else:
        parent.mkdir(parents=True)
    target = parent / "package.zip"
    target.write_bytes(b"ORIGINAL PACKAGE")
    target.chmod(0o600)
    guards = {parent: filesystem.capture_directory_identity(parent)}
    expectations = {target: filesystem.capture_file_expectation(target, limit=100)}
    relocated = tmp_path / "relocated"
    real_assert = filesystem._assert_directory_identity
    injected = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def assert_then_swap(path: Path, expected: filesystem.DirectoryIdentity) -> None:
        nonlocal injected
        real_assert(path, expected)
        if not injected and path == parent:
            injected = True
            ancestor.rename(relocated)
            ancestor.symlink_to(relocated, target_is_directory=True)

    monkeypatch.setattr(filesystem, "_assert_directory_identity", assert_then_swap)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {target: b"NEW PACKAGE"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert injected is True
    assert captured.value.reason in {"directory_changed", "unsafe_directory"}
    assert (relocated / "nested" / "handoff" / target.name).read_bytes() == b"ORIGINAL PACKAGE"
    assert not [path for path in (relocated / "nested" / "handoff").iterdir() if path.name.startswith(".")]


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
def test_secure_atomic_publish_fsyncs_recovery_before_and_after_backup_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    from shared import filesystem

    target = tmp_path / "review.html"
    target.write_bytes(b"ORIGINAL REVIEW")
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    expectations = {target: filesystem.capture_file_expectation(target, limit=100)}
    real_link = os.link
    real_unlink = os.unlink
    real_fsync = os.fsync
    events: list[str] = []
    placement_failed = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def link_with_failure(source, destination, **kwargs) -> None:
        nonlocal placement_failed
        source_name = Path(source).name
        destination_name = Path(destination).name
        if destination_name == target.name and ".backup." not in source_name:
            placement_failed = True
            raise OSError(errno.EIO, "injected placement failure")
        if ".backup." in source_name and destination_name == target.name:
            events.append("recovery-link")
        real_link(source, destination, **kwargs)

    def unlink_with_events(path, *args, **kwargs) -> None:
        if ".backup." in Path(path).name:
            events.append("backup-unlink")
        real_unlink(path, *args, **kwargs)

    def fsync_with_events(descriptor: int) -> None:
        events.append("fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(filesystem.os, "link", link_with_failure)
    monkeypatch.setattr(filesystem.os, "unlink", unlink_with_events)
    monkeypatch.setattr(filesystem.os, "fsync", fsync_with_events)

    with pytest.raises(filesystem.SecurePublishError):
        filesystem.secure_atomic_publish(
            {target: b"NEW REVIEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert placement_failed is True
    link_index = events.index("recovery-link")
    unlink_index = events.index("backup-unlink")
    assert "fsync" in events[link_index + 1 : unlink_index]
    assert "fsync" in events[unlink_index + 1 :]
    assert target.read_bytes() == b"ORIGINAL REVIEW"
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".")]


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
def test_secure_atomic_publish_reports_retained_backup_when_success_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    from shared import filesystem

    target = tmp_path / "review.html"
    target.write_bytes(b"ORIGINAL REVIEW")
    target.chmod(0o600)
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    expectations = {target: filesystem.capture_file_expectation(target, limit=100)}
    real_unlink = os.unlink
    injected = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def fail_backup_cleanup(path, *args, **kwargs) -> None:
        nonlocal injected
        if ".backup." in Path(path).name:
            injected = True
            raise OSError(errno.EIO, "injected backup cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(filesystem.os, "unlink", fail_backup_cleanup)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {target: b"NEW REVIEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert injected is True
    assert captured.value.reason == "cleanup_failed"
    assert target.read_bytes() == b"NEW REVIEW"
    assert target.stat().st_mode & 0o777 == 0o600
    assert len(captured.value.recovery_paths) == 1
    retained = captured.value.recovery_paths[0]
    assert retained.read_bytes() == b"ORIGINAL REVIEW"
    assert retained.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
def test_secure_atomic_publish_fsyncs_each_parent_before_and_after_success_backup_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    from shared import filesystem

    parents = [tmp_path / "one", tmp_path / "two"]
    for parent in parents:
        parent.mkdir()
    targets = [parent / "review.html" for parent in parents]
    for index, target in enumerate(targets):
        target.write_bytes(f"ORIGINAL {index}".encode())
    guards = {parent: filesystem.capture_directory_identity(parent) for parent in parents}
    expectations = {target: filesystem.capture_file_expectation(target, limit=100) for target in targets}
    real_unlink = os.unlink
    real_fsync = os.fsync
    events: list[tuple[str, int]] = []

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def unlink_with_events(path, *args, **kwargs) -> None:
        directory_fd = kwargs.get("dir_fd")
        if ".backup." in Path(path).name and directory_fd is not None:
            events.append(("backup-unlink", os.fstat(directory_fd).st_ino))
        real_unlink(path, *args, **kwargs)

    def fsync_with_events(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            events.append(("directory-fsync", info.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(filesystem.os, "unlink", unlink_with_events)
    monkeypatch.setattr(filesystem.os, "fsync", fsync_with_events)

    filesystem.secure_atomic_publish(
        {target: f"NEW {index}".encode() for index, target in enumerate(targets)},
        directory_identities=guards,
        target_expectations=expectations,
    )

    for parent in parents:
        inode = parent.stat().st_ino
        unlink_index = events.index(("backup-unlink", inode))
        assert ("directory-fsync", inode) in events[:unlink_index]
        assert ("directory-fsync", inode) in events[unlink_index + 1 :]
    assert not [path for parent in parents for path in parent.iterdir() if path.name.startswith(".")]


def test_secure_atomic_publish_fallback_fails_closed_without_component_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import filesystem

    parent = tmp_path / "nested" / "handoff"
    parent.mkdir(parents=True)
    target = parent / "review.html"
    target.write_bytes(b"ORIGINAL")
    guards = {parent: filesystem.capture_directory_identity(parent)}
    expectations = {target: filesystem.capture_file_expectation(target, limit=100)}
    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: False)
    monkeypatch.setattr(filesystem, "_secure_directory_walk_supported", lambda: False)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {target: b"NEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    assert captured.value.reason == "unsafe_directory"
    assert target.read_bytes() == b"ORIGINAL"
    assert not [path for path in parent.iterdir() if path.name.startswith(".")]


def test_secure_atomic_publish_fsyncs_quarantine_rename_before_inspection_and_after_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import filesystem

    first = tmp_path / "a-new.txt"
    second = tmp_path / "b-fail.txt"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    expectations = {first: None, second: None}
    real_link = os.link
    real_unlink = os.unlink
    real_fsync = os.fsync
    real_rename = filesystem._rename_noreplace_dirfd
    events: list[str] = []

    def fail_second_placement(source, destination, **kwargs) -> None:
        if Path(destination).name == second.name:
            raise OSError(errno.EIO, "injected placement failure")
        real_link(source, destination, **kwargs)

    def rename_with_event(directory_fd: int, source_name: str, destination_name: str) -> None:
        if source_name == first.name and ".recovery." in destination_name:
            events.append("quarantine-rename")
        real_rename(directory_fd, source_name, destination_name)

    def unlink_with_event(path, *args, **kwargs) -> None:
        if Path(path).name.startswith(f".{first.name}.recovery."):
            events.append("quarantine-unlink")
        real_unlink(path, *args, **kwargs)

    def fsync_with_event(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("directory-fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(filesystem.os, "link", fail_second_placement)
    monkeypatch.setattr(filesystem, "_rename_noreplace_dirfd", rename_with_event)
    monkeypatch.setattr(filesystem.os, "unlink", unlink_with_event)
    monkeypatch.setattr(filesystem.os, "fsync", fsync_with_event)

    with pytest.raises(filesystem.SecurePublishError):
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations=expectations,
        )

    rename_index = events.index("quarantine-rename")
    unlink_index = events.index("quarantine-unlink")
    assert "directory-fsync" in events[rename_index + 1 : unlink_index]
    assert "directory-fsync" in events[unlink_index + 1 :]
    assert not first.exists()
    assert not second.exists()
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".")]


def test_secure_atomic_publish_reports_temporary_retained_after_otherwise_successful_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import filesystem

    target = tmp_path / "review.html"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    real_unlink = os.unlink
    temporary_attempts = 0

    def retain_temporary(path, *args, **kwargs) -> None:
        nonlocal temporary_attempts
        name = Path(path).name
        if name.startswith(f".{target.name}.") and ".backup." not in name and ".recovery." not in name:
            temporary_attempts += 1
            if temporary_attempts == 1:
                return
            raise OSError(errno.EIO, "injected temporary cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(filesystem.os, "unlink", retain_temporary)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {target: b"NEW REVIEW"},
            directory_identities=guards,
            target_expectations={target: None},
        )

    assert temporary_attempts == 2
    assert captured.value.reason == "cleanup_failed"
    assert target.read_bytes() == b"NEW REVIEW"
    assert len(captured.value.recovery_paths) == 1
    retained = captured.value.recovery_paths[0]
    assert retained.read_bytes() == b"NEW REVIEW"
    assert retained.stat().st_mode & 0o777 == 0o600


def test_secure_atomic_publish_adds_failed_temporary_cleanup_to_primary_failure_recoveries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import filesystem

    first = tmp_path / "a-new.txt"
    second = tmp_path / "b-fail.txt"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    real_link = os.link
    real_unlink = os.unlink

    def fail_second_placement(source, destination, **kwargs) -> None:
        if Path(destination).name == second.name:
            raise OSError(errno.EIO, "injected primary failure")
        real_link(source, destination, **kwargs)

    def fail_second_temporary_cleanup(path, *args, **kwargs) -> None:
        name = Path(path).name
        if name.startswith(f".{second.name}.") and ".backup." not in name and ".recovery." not in name:
            raise OSError(errno.EIO, "injected temporary cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(filesystem.os, "link", fail_second_placement)
    monkeypatch.setattr(filesystem.os, "unlink", fail_second_temporary_cleanup)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations={first: None, second: None},
        )

    assert captured.value.reason == "write_failed"
    assert not first.exists()
    assert not second.exists()
    assert len(captured.value.recovery_paths) == 1
    retained = captured.value.recovery_paths[0]
    assert retained.name.startswith(f".{second.name}.")
    assert retained.read_bytes() == b"SECOND NEW"
    assert retained.stat().st_mode & 0o777 == 0o600


def test_secure_atomic_publish_fsyncs_parent_after_final_temporary_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import filesystem

    target = tmp_path / "review.html"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    real_unlink = os.unlink
    real_fsync = os.fsync
    temporary_attempts = 0
    events: list[str] = []

    def defer_temporary_cleanup(path, *args, **kwargs) -> None:
        nonlocal temporary_attempts
        name = Path(path).name
        if name.startswith(f".{target.name}.") and ".backup." not in name and ".recovery." not in name:
            temporary_attempts += 1
            if temporary_attempts == 1:
                return
            events.append("final-temp-unlink")
        real_unlink(path, *args, **kwargs)

    def fsync_with_event(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("directory-fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(filesystem.os, "unlink", defer_temporary_cleanup)
    monkeypatch.setattr(filesystem.os, "fsync", fsync_with_event)

    filesystem.secure_atomic_publish(
        {target: b"NEW REVIEW"},
        directory_identities=guards,
        target_expectations={target: None},
    )

    unlink_index = events.index("final-temp-unlink")
    assert "directory-fsync" in events[unlink_index + 1 :]
    assert target.read_bytes() == b"NEW REVIEW"
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".")]


def test_secure_atomic_publish_surfaces_final_temporary_directory_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import filesystem

    target = tmp_path / "review.html"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    real_unlink = os.unlink
    real_fsync = os.fsync
    temporary_attempts = 0
    final_temp_unlinked = False

    def defer_temporary_cleanup(path, *args, **kwargs) -> None:
        nonlocal temporary_attempts, final_temp_unlinked
        name = Path(path).name
        if name.startswith(f".{target.name}.") and ".backup." not in name and ".recovery." not in name:
            temporary_attempts += 1
            if temporary_attempts == 1:
                return
            real_unlink(path, *args, **kwargs)
            final_temp_unlinked = True
            return
        real_unlink(path, *args, **kwargs)

    def fail_final_directory_fsync(descriptor: int) -> None:
        if final_temp_unlinked and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "injected final temporary directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(filesystem.os, "unlink", defer_temporary_cleanup)
    monkeypatch.setattr(filesystem.os, "fsync", fail_final_directory_fsync)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {target: b"NEW REVIEW"},
            directory_identities=guards,
            target_expectations={target: None},
        )

    assert final_temp_unlinked is True
    assert captured.value.reason == "cleanup_failed"
    assert captured.value.recovery_paths == ()
    assert target.read_bytes() == b"NEW REVIEW"
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".")]


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "compatibility-route"])
def test_secure_atomic_publish_recreates_durable_recovery_after_post_quarantine_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    from shared import filesystem

    first = tmp_path / "a-new.txt"
    second = tmp_path / "b-fail.txt"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    real_link = os.link
    real_unlink = os.unlink
    real_fsync = os.fsync
    recovery_unlinked = False
    injected_fsync = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)

    def fail_second_placement(source, destination, **kwargs) -> None:
        if Path(destination).name == second.name:
            raise OSError(errno.EIO, "injected placement failure")
        real_link(source, destination, **kwargs)

    def track_recovery_unlink(path, *args, **kwargs) -> None:
        nonlocal recovery_unlinked
        if Path(path).name.startswith(f".{first.name}.recovery."):
            recovery_unlinked = True
        real_unlink(path, *args, **kwargs)

    def fail_once_after_recovery_unlink(descriptor: int) -> None:
        nonlocal injected_fsync
        if recovery_unlinked and not injected_fsync and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            injected_fsync = True
            raise OSError(errno.EIO, "injected post-quarantine fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(filesystem.os, "link", fail_second_placement)
    monkeypatch.setattr(filesystem.os, "unlink", track_recovery_unlink)
    monkeypatch.setattr(filesystem.os, "fsync", fail_once_after_recovery_unlink)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations={first: None, second: None},
        )

    assert recovery_unlinked is True
    assert injected_fsync is True
    assert captured.value.recovery_paths
    assert all(path.exists() for path in captured.value.recovery_paths)
    recovery = captured.value.recovery_paths[0]
    assert recovery.read_bytes() == b"FIRST NEW"
    assert recovery.stat().st_mode & 0o777 == 0o600


def test_secure_atomic_publish_omits_uncertainty_when_failed_recreation_left_no_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import filesystem

    first = tmp_path / "a-new.txt"
    second = tmp_path / "b-fail.txt"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    real_link = os.link
    real_unlink = os.unlink
    real_fsync = os.fsync
    real_open = os.open
    recovery_unlinked = False
    injected_fsync = False

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: True)
    monkeypatch.setattr(filesystem, "_secure_directory_walk_supported", lambda: True)

    def fail_second_placement(source, destination, **kwargs) -> None:
        if Path(destination).name == second.name:
            raise OSError(errno.EIO, "injected placement failure")
        real_link(source, destination, **kwargs)

    def track_recovery_unlink(path, *args, **kwargs) -> None:
        nonlocal recovery_unlinked
        if Path(path).name.startswith(f".{first.name}.recovery."):
            recovery_unlinked = True
        real_unlink(path, *args, **kwargs)

    def fail_once_after_recovery_unlink(descriptor: int) -> None:
        nonlocal injected_fsync
        if recovery_unlinked and not injected_fsync and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            injected_fsync = True
            raise OSError(errno.EIO, "injected post-quarantine fsync failure")
        real_fsync(descriptor)

    def fail_recovery_recreation_open(path, *args, **kwargs):
        if recovery_unlinked:
            raise OSError(errno.EIO, "injected recreation failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(filesystem.os, "link", fail_second_placement)
    monkeypatch.setattr(filesystem.os, "unlink", track_recovery_unlink)
    monkeypatch.setattr(filesystem.os, "fsync", fail_once_after_recovery_unlink)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        with monkeypatch.context() as recreation_patch:
            recreation_patch.setattr(filesystem.os, "open", fail_recovery_recreation_open)
            filesystem.secure_atomic_publish(
                {first: b"FIRST NEW", second: b"SECOND NEW"},
                directory_identities=guards,
                target_expectations={first: None, second: None},
            )

    assert captured.value.reason == "write_failed"
    assert captured.value.recovery_paths == ()
    assert captured.value.residue_paths == ()
    assert captured.value.recovery_notes == ()
    assert list(tmp_path.glob(".*.recovery.*")) == []


@pytest.mark.parametrize(
    ("failure_stage", "preferred_collision"),
    [
        pytest.param("empty-write", False, id="empty-write"),
        pytest.param("empty-write-retained", False, id="empty-write-retained"),
        pytest.param("partial-write", False, id="partial-write"),
        pytest.param("partial-removal-fsync", False, id="partial-removal-fsync"),
        pytest.param("file-fsync", False, id="file-fsync"),
        pytest.param("parent-fsync", False, id="parent-fsync"),
        pytest.param("verification", False, id="verification"),
        pytest.param("empty-write", True, id="collision-empty-write"),
        pytest.param("partial-write", True, id="collision-partial-write"),
        pytest.param("file-fsync", True, id="collision-file-fsync"),
        pytest.param("verification", True, id="collision-verification"),
    ],
)
def test_secure_atomic_publish_classifies_files_created_before_recreation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    preferred_collision: bool,
) -> None:
    from shared import filesystem

    first = tmp_path / "a-new.txt"
    second = tmp_path / "b-fail.txt"
    guards = {tmp_path: filesystem.capture_directory_identity(tmp_path)}
    real_link = os.link
    real_unlink = os.unlink
    real_open = os.open
    real_fdopen = os.fdopen
    real_fsync = os.fsync
    real_expectation = filesystem._dirfd_file_expectation
    recovery_unlinked = False
    post_unlink_fsync_failed = False
    partial_removal_fsync_failed = False
    created_fd: int | None = None
    created_name: str | None = None
    collision_name: str | None = None
    stage_failed = False
    collision_bytes = b"CONCURRENT PREFERRED RECOVERY"

    class FailingWriteHandle:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, exc_type, exc, traceback):
            return self.handle.__exit__(exc_type, exc, traceback)

        def write(self, payload):
            nonlocal stage_failed
            stage_failed = True
            if failure_stage in {"partial-write", "partial-removal-fsync"}:
                self.handle.write(payload[:4])
                self.handle.flush()
            raise OSError(errno.EIO, "injected recreated recovery write failure")

    def fail_second_placement(source, destination, **kwargs) -> None:
        if Path(destination).name == second.name:
            raise OSError(errno.EIO, "injected placement failure")
        real_link(source, destination, **kwargs)

    def track_recovery_unlink(path, *args, **kwargs) -> None:
        nonlocal recovery_unlinked
        name = Path(path).name
        if failure_stage in {"empty-write-retained", "partial-write"} and recovery_unlinked and name == created_name:
            raise OSError(errno.EIO, "injected partial recovery cleanup failure")
        if name.startswith(f".{first.name}.recovery."):
            recovery_unlinked = True
        real_unlink(path, *args, **kwargs)

    def track_recovery_create(path, flags, *args, **kwargs):
        nonlocal collision_name, created_fd, created_name
        name = Path(path).name
        if (
            preferred_collision
            and recovery_unlinked
            and flags & os.O_EXCL
            and ".recovery." in name
            and ".recreated." not in name
            and collision_name is None
        ):
            collision_fd = real_open(path, flags, *args, **kwargs)
            with real_fdopen(collision_fd, "wb") as collision_handle:
                collision_handle.write(collision_bytes)
            collision_name = name
        descriptor = real_open(path, flags, *args, **kwargs)
        if recovery_unlinked and flags & os.O_EXCL and ".recovery." in name:
            created_fd = descriptor
            created_name = name
        return descriptor

    def maybe_fail_write(descriptor, *args, **kwargs):
        handle = real_fdopen(descriptor, *args, **kwargs)
        if (
            failure_stage in {"empty-write", "empty-write-retained", "partial-write", "partial-removal-fsync"}
            and descriptor == created_fd
            and not stage_failed
        ):
            return FailingWriteHandle(handle)
        return handle

    def inject_barrier_failures(descriptor: int) -> None:
        nonlocal post_unlink_fsync_failed, partial_removal_fsync_failed, stage_failed
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if recovery_unlinked and not post_unlink_fsync_failed and is_directory:
            post_unlink_fsync_failed = True
            raise OSError(errno.EIO, "injected post-quarantine fsync failure")
        if (
            failure_stage == "partial-removal-fsync"
            and stage_failed
            and is_directory
            and not partial_removal_fsync_failed
        ):
            partial_removal_fsync_failed = True
            raise OSError(errno.EIO, "injected partial recovery removal fsync failure")
        if failure_stage == "file-fsync" and descriptor == created_fd and not stage_failed:
            stage_failed = True
            raise OSError(errno.EIO, "injected recreated recovery file fsync failure")
        if failure_stage == "parent-fsync" and created_fd is not None and is_directory and not stage_failed:
            stage_failed = True
            raise OSError(errno.EIO, "injected recreated recovery parent fsync failure")
        real_fsync(descriptor)

    def inject_verification_failure(directory_fd, name, reported_path, *, limit):
        nonlocal stage_failed
        if failure_stage == "verification" and name == created_name:
            stage_failed = True
            raise filesystem.SecurePublishError(
                "cleanup_failed", reported_path, "injected recreated recovery verification failure"
            )
        return real_expectation(directory_fd, name, reported_path, limit=limit)

    monkeypatch.setattr(filesystem.os, "link", fail_second_placement)
    monkeypatch.setattr(filesystem.os, "unlink", track_recovery_unlink)
    monkeypatch.setattr(filesystem.os, "open", track_recovery_create)
    monkeypatch.setattr(filesystem.os, "fdopen", maybe_fail_write)
    monkeypatch.setattr(filesystem.os, "fsync", inject_barrier_failures)
    monkeypatch.setattr(filesystem, "_dirfd_file_expectation", inject_verification_failure)
    monkeypatch.setattr(filesystem, "_secure_directory_walk_supported", lambda: True)

    with pytest.raises(filesystem.SecurePublishError) as captured:
        filesystem.secure_atomic_publish(
            {first: b"FIRST NEW", second: b"SECOND NEW"},
            directory_identities=guards,
            target_expectations={first: None, second: None},
        )

    assert post_unlink_fsync_failed is True
    assert stage_failed is True
    assert captured.value.reason == "write_failed"
    retained_paths = (
        *captured.value.recovery_paths,
        *captured.value.residue_paths,
        *captured.value.conflict_paths,
    )
    assert all(path.exists() for path in retained_paths)
    assert all(path.is_file() for path in retained_paths)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in retained_paths)
    assert set(path for path in tmp_path.iterdir() if path.name.startswith(".")) == set(retained_paths)
    if preferred_collision:
        assert collision_name is not None
        assert created_name is not None and ".recreated." in created_name
        assert len(captured.value.conflict_paths) == 1
        assert captured.value.conflict_paths[0].name == collision_name
        assert captured.value.conflict_paths[0].read_bytes() == collision_bytes
        assert captured.value.conflict_paths[0].stat().st_mode & 0o777 == 0o600
    else:
        assert captured.value.conflict_paths == ()
    if failure_stage == "empty-write":
        assert captured.value.recovery_paths == ()
        assert captured.value.residue_paths == ()
        assert captured.value.recovery_notes == ()
    elif failure_stage in {"empty-write-retained", "partial-write", "partial-removal-fsync"}:
        assert captured.value.recovery_paths == ()
        assert len(captured.value.residue_paths) == 1
        assert captured.value.residue_paths[0].name == created_name
        expected_residue = b"" if failure_stage == "empty-write-retained" else b"FIRS"
        assert captured.value.residue_paths[0].read_bytes() == expected_residue
        assert captured.value.recovery_notes
        assert "uncertain" in captured.value.recovery_notes[0].lower()
        assert partial_removal_fsync_failed is (failure_stage == "partial-removal-fsync")
    else:
        assert captured.value.residue_paths == ()
        assert len(captured.value.recovery_paths) == 1
        assert captured.value.recovery_paths[0].name == created_name
        assert captured.value.recovery_paths[0].read_bytes() == b"FIRST NEW"
