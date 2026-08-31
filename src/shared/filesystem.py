"""Private, traversal-safe filesystem primitives for capability runtime data."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import stat
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_FRAGMENTS = ("api_key", "apikey", "access_token", "auth_token", "authorization")
_DARWIN_RENAME_EXCL = 0x00000004
_LINUX_RENAME_NOREPLACE = 1
_AT_FDCWD = -100
_DIRFD_PUBLICATION_AVAILABLE = all(
    function in os.supports_dir_fd for function in (os.link, os.open, os.stat, os.rename, os.unlink)
)


@dataclass(frozen=True)
class DirectoryIdentity:
    """Stable identity captured for a real directory before a transaction."""

    device: int
    inode: int
    file_type: int


@dataclass(frozen=True)
class FileExpectation:
    """Exact bytes and file identity expected at a transaction target."""

    payload: bytes
    identity: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class _GuardedDirectory:
    """Held no-follow descriptors and identities for one lexical directory chain."""

    path: Path
    components: tuple[str, ...]
    handles: tuple[int, ...]
    identities: tuple[DirectoryIdentity, ...]
    expected_final: DirectoryIdentity

    @property
    def final_fd(self) -> int:
        return self.handles[-1]


class SecurePublishError(Exception):
    """Capability-neutral failure from a secure multi-file publication."""

    def __init__(
        self,
        reason: str,
        path: str | Path,
        message: str,
        *,
        recovery_paths: tuple[Path, ...] = (),
        residue_paths: tuple[Path, ...] = (),
        conflict_paths: tuple[Path, ...] = (),
        recovery_notes: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.reason = reason
        self.path = Path(path)
        self.message = message
        self.recovery_paths = recovery_paths
        self.residue_paths = residue_paths
        self.conflict_paths = conflict_paths
        self.recovery_notes = recovery_notes


@dataclass(frozen=True)
class _RollbackOutcome:
    recovery_paths: tuple[Path, ...] = ()
    residue_paths: tuple[Path, ...] = ()
    conflict_paths: tuple[Path, ...] = ()
    recovery_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RecoveryRecreation:
    path: Path
    conflict_paths: tuple[Path, ...] = ()


class _RecoveryRecreationError(Exception):
    """Failure after recovery allocation, with the exact created name and skipped conflicts."""

    def __init__(
        self,
        *,
        created_name: str | None,
        created_path: Path | None,
        conflict_paths: tuple[Path, ...],
    ):
        super().__init__("private recovery recreation failed")
        self.created_name = created_name
        self.created_path = created_path
        self.conflict_paths = conflict_paths


def _merge_rollback_outcomes(*outcomes: _RollbackOutcome) -> _RollbackOutcome:
    return _RollbackOutcome(
        recovery_paths=tuple(dict.fromkeys(path for outcome in outcomes for path in outcome.recovery_paths)),
        residue_paths=tuple(dict.fromkeys(path for outcome in outcomes for path in outcome.residue_paths)),
        conflict_paths=tuple(dict.fromkeys(path for outcome in outcomes for path in outcome.conflict_paths)),
        recovery_notes=tuple(dict.fromkeys(note for outcome in outcomes for note in outcome.recovery_notes)),
    )


def ensure_private_dir(path: str | Path) -> Path:
    """Create a directory and restrict it to the current user where supported."""
    directory = Path(path).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    return directory


def validate_component(value: str, *, label: str = "path component") -> str:
    """Accept one non-special path component, never a path fragment."""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{label} must be a non-empty file-name component")
    if "\x00" in value or "/" in value or "\\" in value or Path(value).name != value:
        raise ValueError(f"{label} must not contain path separators")
    return value


def contained_path(root: str | Path, *components: str) -> Path:
    """Build a path from validated components and prove it remains under root."""
    base = Path(root).expanduser().resolve()
    clean = [validate_component(component) for component in components]
    candidate = base.joinpath(*clean).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("path escapes its configured root")
    return candidate


def atomic_write_text(path: str | Path, content: str, *, encoding: str = "utf-8") -> Path:
    return _atomic_write(Path(path), content.encode(encoding))


def atomic_write_bytes(path: str | Path, content: bytes) -> Path:
    return _atomic_write(Path(path), content)


def atomic_write_json(path: str | Path, data: Any) -> Path:
    return atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


def exclusive_write_text(path: str | Path, content: str, *, encoding: str = "utf-8") -> Path:
    """Create a private text file without overwriting an existing path."""
    destination = Path(path).expanduser()
    parent = ensure_private_dir(destination.parent)
    destination = parent / destination.name
    created = False
    try:
        with destination.open("x", encoding=encoding, newline="") as handle:
            created = True
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            destination.chmod(0o600)
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return destination


def remove_files(paths: Iterable[str | Path]) -> None:
    """Remove the explicitly supplied files, ignoring paths already absent."""
    for path in paths:
        Path(path).unlink(missing_ok=True)


def _atomic_write(path: Path, content: bytes) -> Path:
    parent = ensure_private_dir(path.expanduser().parent)
    destination = parent / path.name
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=parent)
    temporary_path = Path(temporary)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        if os.name != "nt":
            destination.chmod(0o600)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def capture_directory_identity(path: str | Path) -> DirectoryIdentity:
    """Capture a non-symlink directory identity for a later guarded publication."""
    directory = Path(path)
    try:
        info = directory.lstat()
    except OSError as exc:
        raise SecurePublishError("unsafe_directory", directory, "Directory is missing or unreadable.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SecurePublishError("unsafe_directory", directory, "Path must be a real non-symlink directory.")
    return _directory_identity_from_stat(info)


def prepare_secure_directory(path: str | Path, *, mode: int = 0o700) -> tuple[Path, DirectoryIdentity]:
    """Securely create or bind one absolute lexical directory without following symlinks."""
    directory = Path(path)
    if not directory.is_absolute() or ".." in directory.parts or type(mode) is not int or not 0 <= mode <= 0o777:
        raise SecurePublishError("unsafe_directory", directory, "Directory path or creation mode is unsafe.")
    if _secure_directory_walk_supported():
        return _prepare_secure_directory_dirfd(directory, mode)
    return _prepare_existing_directory_fallback(directory)


def _prepare_secure_directory_dirfd(directory: Path, mode: int) -> tuple[Path, DirectoryIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    handles: list[int] = []
    identities: list[DirectoryIdentity] = []
    try:
        current = os.open(directory.anchor, flags)
        handles.append(current)
        for component in directory.parts[1:]:
            created = False
            try:
                child = os.open(component, flags, dir_fd=current)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode, dir_fd=current)
                    created = True
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=current)
            handles.append(child)
            info = os.fstat(child)
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            identity = _directory_identity_from_stat(info)
            if not stat.S_ISDIR(info.st_mode) or _directory_identity_from_stat(named) != identity:
                raise SecurePublishError(
                    "unsafe_directory", directory, "Directory component changed or is not a real directory."
                )
            if created and os.name != "nt":
                os.fchmod(child, mode)
            identities.append(identity)
            current = child
        _verify_secure_directory_chain(directory, identities)
        identity = identities[-1] if identities else _directory_identity_from_stat(os.fstat(handles[0]))
        return directory, identity
    except SecurePublishError:
        raise
    except OSError as exc:
        raise SecurePublishError(
            "unsafe_directory", directory, "Directory could not be created or bound without following symlinks."
        ) from exc
    finally:
        for handle in reversed(handles):
            try:
                os.close(handle)
            except OSError:
                pass


def _verify_secure_directory_chain(directory: Path, expected: list[DirectoryIdentity]) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    handles: list[int] = []
    try:
        current = os.open(directory.anchor, flags)
        handles.append(current)
        for component, identity in zip(directory.parts[1:], expected, strict=True):
            child = os.open(component, flags, dir_fd=current)
            handles.append(child)
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            if (
                _directory_identity_from_stat(os.fstat(child)) != identity
                or _directory_identity_from_stat(named) != identity
            ):
                raise SecurePublishError("unsafe_directory", directory, "Directory chain changed during creation.")
            current = child
    except SecurePublishError:
        raise
    except OSError as exc:
        raise SecurePublishError("unsafe_directory", directory, "Directory chain became unsafe.") from exc
    finally:
        for handle in reversed(handles):
            try:
                os.close(handle)
            except OSError:
                pass


def _prepare_existing_directory_fallback(directory: Path) -> tuple[Path, DirectoryIdentity]:
    """Fail closed when a platform cannot securely create missing path components."""
    current = Path(directory.anchor)
    before: list[DirectoryIdentity] = []
    try:
        for component in directory.parts[1:]:
            current /= component
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise SecurePublishError("unsafe_directory", directory, "Directory path crosses an unsafe component.")
            before.append(_directory_identity_from_stat(info))
        current = Path(directory.anchor)
        for component, expected in zip(directory.parts[1:], before, strict=True):
            current /= component
            info = current.lstat()
            if _directory_identity_from_stat(info) != expected:
                raise SecurePublishError("unsafe_directory", directory, "Directory chain changed during inspection.")
    except FileNotFoundError as exc:
        raise SecurePublishError(
            "unsafe_directory", directory, "Secure directory creation is unavailable on this platform."
        ) from exc
    except SecurePublishError:
        raise
    except OSError as exc:
        raise SecurePublishError("unsafe_directory", directory, "Directory could not be inspected safely.") from exc
    identity = before[-1] if before else capture_directory_identity(directory)
    return directory, identity


def _secure_directory_walk_supported() -> bool:
    return (
        bool(getattr(os, "O_DIRECTORY", 0))
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and (os.name == "nt" or hasattr(os, "fchmod"))
        and all(function in os.supports_dir_fd for function in (os.mkdir, os.open, os.stat))
    )


def _open_guarded_directory(directory: Path, expected: DirectoryIdentity) -> _GuardedDirectory:
    """Open every lexical component without following links and retain the complete chain."""
    if not _secure_directory_walk_supported():
        raise SecurePublishError(
            "unsafe_directory",
            directory,
            "Secure component-by-component directory traversal is unavailable.",
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    handles: list[int] = []
    identities: list[DirectoryIdentity] = []
    components = tuple(directory.parts[1:])
    try:
        root = os.open(directory.anchor, flags)
        handles.append(root)
        root_info = os.fstat(root)
        if not stat.S_ISDIR(root_info.st_mode):
            raise SecurePublishError("unsafe_directory", directory, "Directory anchor is unsafe.")
        identities.append(_directory_identity_from_stat(root_info))
        current = root
        for component in components:
            child = os.open(component, flags, dir_fd=current)
            handles.append(child)
            child_info = os.fstat(child)
            named_info = os.stat(component, dir_fd=current, follow_symlinks=False)
            child_identity = _directory_identity_from_stat(child_info)
            if (
                not stat.S_ISDIR(child_info.st_mode)
                or not stat.S_ISDIR(named_info.st_mode)
                or _directory_identity_from_stat(named_info) != child_identity
            ):
                raise SecurePublishError(
                    "directory_changed", directory, "A guarded directory component changed or became unsafe."
                )
            identities.append(child_identity)
            current = child
        if identities[-1] != expected:
            raise SecurePublishError("directory_changed", directory, "Guarded directory identity changed.")
        guarded = _GuardedDirectory(
            path=directory,
            components=components,
            handles=tuple(handles),
            identities=tuple(identities),
            expected_final=expected,
        )
        _revalidate_guarded_directory(guarded)
        return guarded
    except SecurePublishError:
        for handle in reversed(handles):
            try:
                os.close(handle)
            except OSError:
                pass
        raise
    except OSError as exc:
        for handle in reversed(handles):
            try:
                os.close(handle)
            except OSError:
                pass
        raise SecurePublishError(
            "directory_changed", directory, "Guarded directory could not be opened without following links."
        ) from exc


def _revalidate_guarded_directory(guarded: _GuardedDirectory) -> None:
    """Prove every held descriptor still matches its lexical no-follow directory entry."""
    try:
        for index, (handle, expected) in enumerate(zip(guarded.handles, guarded.identities, strict=True)):
            if _directory_identity_from_stat(os.fstat(handle)) != expected:
                raise SecurePublishError(
                    "directory_changed", guarded.path, "A held directory component changed during publication."
                )
            if index == 0:
                continue
            named = os.stat(
                guarded.components[index - 1],
                dir_fd=guarded.handles[index - 1],
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(named.st_mode) or _directory_identity_from_stat(named) != expected:
                raise SecurePublishError(
                    "directory_changed", guarded.path, "A lexical directory component changed during publication."
                )
        if guarded.identities[-1] != guarded.expected_final:
            raise SecurePublishError("directory_changed", guarded.path, "Guarded directory identity changed.")
    except SecurePublishError:
        raise
    except OSError as exc:
        raise SecurePublishError(
            "directory_changed", guarded.path, "Guarded directory chain became unsafe during publication."
        ) from exc


def _close_guarded_directory(guarded: _GuardedDirectory) -> None:
    for handle in reversed(guarded.handles):
        try:
            os.close(handle)
        except OSError:
            pass


def capture_file_expectation(path: str | Path, *, limit: int) -> FileExpectation | None:
    """Capture one regular target without following symlinks, or return None when absent."""
    target = Path(path)
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    try:
        target.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SecurePublishError("unsafe_target", target, "Target could not be inspected.") from exc
    payload, info = _path_read_regular(target, limit=limit)
    return FileExpectation(payload, _file_identity_from_stat(info))


def secure_atomic_publish(
    files_to_write: Mapping[Path, bytes],
    *,
    directory_identities: Mapping[Path, DirectoryIdentity],
    target_expectations: Mapping[Path, FileExpectation | None],
) -> None:
    """Publish exact bytes across guarded directories as one best-effort transaction.

    Every destination must have a captured parent identity and target expectation. A platform with
    complete no-follow component traversal and directory-relative mutation support uses held
    directory descriptors for the entire transaction. Other platforms fail closed before mutation;
    there is no path-following publication fallback.
    """
    ordered = sorted(((Path(path), payload) for path, payload in files_to_write.items()), key=lambda item: str(item[0]))
    if any(not isinstance(payload, bytes) for _path, payload in ordered):
        raise TypeError("secure_atomic_publish payloads must be bytes")
    destinations = {path for path, _payload in ordered}
    guards = {Path(path): identity for path, identity in directory_identities.items()}
    expectations = {Path(path): expectation for path, expectation in target_expectations.items()}
    parents = {path.parent for path in destinations}
    if set(guards) != parents:
        missing = next(iter(parents - set(guards)), next(iter(set(guards) - parents), Path(".")))
        raise SecurePublishError("unsafe_directory", missing, "Directory identities must exactly cover targets.")
    if set(expectations) != destinations:
        missing = next(iter(destinations - set(expectations)), next(iter(set(expectations) - destinations), Path(".")))
        raise SecurePublishError("unsafe_target", missing, "Target expectations must exactly cover destinations.")
    if any(not isinstance(identity, DirectoryIdentity) for identity in guards.values()):
        raise TypeError("directory_identities must contain DirectoryIdentity values")
    if any(
        expectation is not None and not isinstance(expectation, FileExpectation)
        for expectation in expectations.values()
    ):
        raise TypeError("target_expectations must contain FileExpectation values or None")
    if not ordered:
        return
    if _dirfd_transactions_supported():
        _secure_atomic_publish_dirfd(ordered, guards, expectations)
    else:
        _secure_atomic_publish_path(ordered, guards, expectations)


def _secure_atomic_publish_dirfd(
    ordered: list[tuple[Path, bytes]],
    guards: dict[Path, DirectoryIdentity],
    expectations: dict[Path, FileExpectation | None],
) -> None:
    handles: dict[Path, int] = {}
    guarded_directories: dict[Path, _GuardedDirectory] = {}
    temporaries: dict[Path, str] = {}
    backups: dict[Path, str] = {}
    placed: dict[Path, FileExpectation] = {}
    active_path = ordered[0][0]
    committed = False
    failure: SecurePublishError | None = None
    failure_cause: Exception | None = None
    try:
        for parent, identity in guards.items():
            active_path = parent
            _assert_directory_identity(parent, identity)
            guarded = _open_guarded_directory(parent, identity)
            guarded_directories[parent] = guarded
            handles[parent] = guarded.final_fd

        for destination, payload in ordered:
            active_path = destination
            parent = destination.parent
            _revalidate_guarded_directory(guarded_directories[parent])
            _validate_dirfd_expectation(handles[parent], destination.name, expectations[destination], destination)
            temporary_name, temporary_fd = _create_private_dirfd_file(handles[parent], f".{destination.name}.")
            temporaries[destination] = temporary_name
            with os.fdopen(temporary_fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

        for destination, _payload in ordered:
            active_path = destination
            parent = destination.parent
            directory_fd = handles[parent]
            _revalidate_guarded_directory(guarded_directories[parent])
            _validate_dirfd_expectation(directory_fd, destination.name, expectations[destination], destination)
            if _dirfd_exists(directory_fd, destination.name):
                backup_name = _unused_dirfd_name(directory_fd, f".{destination.name}.backup.")
                os.replace(destination.name, backup_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                backups[destination] = backup_name
                _validate_dirfd_expectation(directory_fd, backup_name, expectations[destination], destination)
            elif expectations[destination] is not None:
                raise SecurePublishError("target_changed", destination, "Target disappeared before backup.")

        for destination, payload in ordered:
            active_path = destination
            parent = destination.parent
            directory_fd = handles[parent]
            _revalidate_guarded_directory(guarded_directories[parent])
            staged = _dirfd_file_expectation(
                directory_fd,
                temporaries[destination],
                destination,
                limit=max(len(payload), 1),
            )
            if staged.payload != payload:
                raise SecurePublishError("target_changed", destination, "Staged output changed before publication.")
            try:
                os.link(
                    temporaries[destination],
                    destination.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise SecurePublishError("target_changed", destination, "Target appeared during publication.") from exc
            placed[destination] = staged
            actual = _dirfd_file_expectation(
                directory_fd,
                destination.name,
                destination,
                limit=max(len(payload), 1),
            )
            if actual != staged:
                raise SecurePublishError("target_changed", destination, "Placed output differs from its staged file.")
            _dirfd_unlink(directory_fd, temporaries[destination])

        for parent in guards:
            _revalidate_guarded_directory(guarded_directories[parent])
        for destination, expected_payload in ordered:
            active_path = destination
            actual = _dirfd_file_expectation(
                handles[destination.parent],
                destination.name,
                destination,
                limit=max(len(expected_payload), 1),
            )
            if actual.payload != expected_payload:
                raise SecurePublishError("target_changed", destination, "Published output verification failed.")
        for handle in handles.values():
            os.fsync(handle)
        committed = True
        retained_backups: list[Path] = []
        for destination, backup_name in backups.items():
            try:
                os.unlink(backup_name, dir_fd=handles[destination.parent])
            except OSError:
                retained_backups.append(destination.parent / backup_name)
        for handle in handles.values():
            try:
                os.fsync(handle)
            except OSError as exc:
                raise SecurePublishError(
                    "cleanup_failed",
                    active_path,
                    "Published outputs could not be durably finalized.",
                    recovery_paths=tuple(retained_backups),
                ) from exc
        if retained_backups:
            raise SecurePublishError(
                "cleanup_failed",
                retained_backups[0],
                "Published outputs retain one or more backup files that could not be removed.",
                recovery_paths=tuple(retained_backups),
            )
    except Exception as exc:
        if committed:
            if isinstance(exc, SecurePublishError):
                failure = exc
            else:
                failure = SecurePublishError("cleanup_failed", active_path, "Published outputs could not be finalized.")
                failure_cause = exc
        else:
            rollback = _rollback_dirfd(handles, placed, backups)
            if isinstance(exc, SecurePublishError):
                exc.recovery_paths = tuple(dict.fromkeys((*exc.recovery_paths, *rollback.recovery_paths)))
                exc.residue_paths = tuple(dict.fromkeys((*exc.residue_paths, *rollback.residue_paths)))
                exc.conflict_paths = tuple(dict.fromkeys((*exc.conflict_paths, *rollback.conflict_paths)))
                exc.recovery_notes = tuple(dict.fromkeys((*exc.recovery_notes, *rollback.recovery_notes)))
                failure = exc
            else:
                failure = SecurePublishError(
                    "write_failed",
                    active_path,
                    "Outputs could not be written atomically.",
                    recovery_paths=rollback.recovery_paths,
                    residue_paths=rollback.residue_paths,
                    conflict_paths=rollback.conflict_paths,
                    recovery_notes=rollback.recovery_notes,
                )
                failure_cause = exc
    finally:
        retained_temporaries: list[Path] = []
        temporary_cleanup_failed = False
        temporary_cleanup_durability_failed = False
        temporary_cleanup_parents: dict[Path, int] = {}
        for destination, temporary_name in temporaries.items():
            directory_fd = handles.get(destination.parent)
            try:
                temporary_exists = directory_fd is not None and _dirfd_exists(directory_fd, temporary_name)
            except OSError:
                temporary_exists = True
            try:
                _dirfd_unlink(directory_fd, temporary_name)
            except OSError:
                temporary_cleanup_failed = True
                try:
                    retained = directory_fd is not None and _dirfd_exists(directory_fd, temporary_name)
                except OSError:
                    retained = True
                if retained:
                    retained_temporaries.append(destination.parent / temporary_name)
            else:
                if temporary_exists and directory_fd is not None:
                    temporary_cleanup_parents[destination.parent] = directory_fd
        for parent, directory_fd in temporary_cleanup_parents.items():
            try:
                os.fsync(directory_fd)
            except OSError:
                temporary_cleanup_durability_failed = True
        for guarded in guarded_directories.values():
            _close_guarded_directory(guarded)
        if temporary_cleanup_durability_failed:
            existing_paths = failure.recovery_paths if failure is not None else ()
            existing_residues = failure.residue_paths if failure is not None else ()
            existing_conflicts = failure.conflict_paths if failure is not None else ()
            existing_notes = failure.recovery_notes if failure is not None else ()
            failure = SecurePublishError(
                "cleanup_failed",
                next(iter(temporary_cleanup_parents), active_path),
                "Temporary cleanup directory durability could not be verified.",
                recovery_paths=tuple(dict.fromkeys((*existing_paths, *retained_temporaries))),
                residue_paths=existing_residues,
                conflict_paths=existing_conflicts,
                recovery_notes=existing_notes,
            )
        elif temporary_cleanup_failed:
            if failure is None:
                failure = SecurePublishError(
                    "cleanup_failed",
                    retained_temporaries[0] if retained_temporaries else active_path,
                    "Published output temporary cleanup could not be completed.",
                    recovery_paths=tuple(retained_temporaries),
                )
            elif retained_temporaries:
                failure.recovery_paths = tuple(dict.fromkeys((*failure.recovery_paths, *retained_temporaries)))
    if failure is not None:
        if failure_cause is not None:
            raise failure from failure_cause
        raise failure


def _rollback_dirfd(
    handles: Mapping[Path, int],
    placed: Mapping[Path, FileExpectation],
    backups: Mapping[Path, str],
) -> _RollbackOutcome:
    recovery_paths: list[Path] = []
    residue_paths: list[Path] = []
    conflict_paths: list[Path] = []
    recovery_notes: list[str] = []
    for destination in reversed(list(placed)):
        directory_fd = handles.get(destination.parent)
        if directory_fd is None:
            residue_paths.append(destination)
            continue
        quarantine = _quarantine_dirfd_entry(
            directory_fd,
            destination.name,
            placed[destination],
            destination,
        )
        recovery_paths.extend(quarantine.recovery_paths)
        residue_paths.extend(quarantine.residue_paths)
        conflict_paths.extend(quarantine.conflict_paths)
        recovery_notes.extend(quarantine.recovery_notes)
    for destination, backup_name in backups.items():
        directory_fd = handles.get(destination.parent)
        if directory_fd is None:
            recovery_paths.append(destination.parent / backup_name)
            continue
        try:
            backup_exists = _dirfd_exists(directory_fd, backup_name)
        except OSError:
            recovery_paths.append(destination.parent / backup_name)
            continue
        if not backup_exists:
            continue
        try:
            os.link(
                backup_name,
                destination.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            recovery_paths.extend(_link_dirfd_recovery(directory_fd, destination, backup_name))
        except OSError:
            recovery_paths.append(destination.parent / backup_name)
        else:
            try:
                os.fsync(directory_fd)
            except OSError:
                recovery_paths.append(destination.parent / backup_name)
                continue
            try:
                os.unlink(backup_name, dir_fd=directory_fd)
            except OSError:
                recovery_paths.append(destination.parent / backup_name)
            else:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    recovery_paths.append(destination)
    return _RollbackOutcome(
        recovery_paths=tuple(dict.fromkeys(recovery_paths)),
        residue_paths=tuple(dict.fromkeys(residue_paths)),
        conflict_paths=tuple(dict.fromkeys(conflict_paths)),
        recovery_notes=tuple(dict.fromkeys(recovery_notes)),
    )


def _quarantine_dirfd_entry(
    directory_fd: int,
    target_name: str,
    expected: FileExpectation,
    reported_path: Path,
) -> _RollbackOutcome:
    for _attempt in range(100):
        recovery_name = f".{target_name}.recovery.{secrets.token_hex(12)}"
        try:
            _rename_noreplace_dirfd(directory_fd, target_name, recovery_name)
        except FileNotFoundError:
            return _RollbackOutcome()
        except FileExistsError:
            continue
        except OSError:
            return _RollbackOutcome(
                residue_paths=(reported_path,),
                recovery_notes=(f"Recovery identity is uncertain for {reported_path}.",),
            )
        recovery_path = reported_path.parent / recovery_name
        try:
            os.fsync(directory_fd)
        except OSError:
            return _RollbackOutcome(
                residue_paths=(recovery_path,),
                recovery_notes=(f"Recovery durability is uncertain for {recovery_path}.",),
            )
        try:
            current = _dirfd_file_expectation(
                directory_fd,
                recovery_name,
                recovery_path,
                limit=max(len(expected.payload), 1),
            )
        except SecurePublishError:
            return _RollbackOutcome(
                residue_paths=(recovery_path,),
                recovery_notes=(f"Recovery bytes could not be verified for {recovery_path}.",),
            )
        if current != expected:
            return _RollbackOutcome(
                residue_paths=(recovery_path,),
                recovery_notes=(f"Recovery bytes differ from the staged output at {recovery_path}.",),
            )
        try:
            os.unlink(recovery_name, dir_fd=directory_fd)
        except OSError:
            return _RollbackOutcome(recovery_paths=(recovery_path,))
        try:
            os.fsync(directory_fd)
        except OSError:
            try:
                recreated = _recreate_dirfd_recovery(
                    directory_fd,
                    recovery_name,
                    recovery_path,
                    expected.payload,
                )
            except _RecoveryRecreationError as exc:
                conflicts = _RollbackOutcome(conflict_paths=exc.conflict_paths)
                if exc.created_name is None or exc.created_path is None:
                    return conflicts
                inspected = _inspect_or_remove_failed_dirfd_recovery(
                    directory_fd,
                    exc.created_name,
                    exc.created_path,
                    expected.payload,
                    uncertainty_path=reported_path,
                )
                return _merge_rollback_outcomes(conflicts, inspected)
            return _RollbackOutcome(
                recovery_paths=(recreated.path,),
                conflict_paths=recreated.conflict_paths,
            )
        return _RollbackOutcome()
    return _RollbackOutcome(
        residue_paths=(reported_path,),
        recovery_notes=(f"A private recovery name could not be allocated for {reported_path}.",),
    )


def _recreate_dirfd_recovery(
    directory_fd: int,
    preferred_name: str,
    preferred_path: Path,
    payload: bytes,
) -> _RecoveryRecreation:
    """Recreate staged bytes privately and durably after an uncertain quarantine deletion."""
    conflicts: list[Path] = []
    for attempt in range(100):
        name = preferred_name if attempt == 0 else f".{preferred_path.name}.recreated.{secrets.token_hex(12)}"
        path = preferred_path.parent / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            conflicts.append(path)
            continue
        except OSError as exc:
            raise _RecoveryRecreationError(
                created_name=None,
                created_path=None,
                conflict_paths=tuple(conflicts),
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(directory_fd)
            actual = _dirfd_file_expectation(directory_fd, name, path, limit=max(len(payload), 1))
            if actual.payload != payload:
                raise SecurePublishError("cleanup_failed", path, "Recreated recovery bytes could not be verified.")
            return _RecoveryRecreation(path=path, conflict_paths=tuple(conflicts))
        except Exception as exc:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise _RecoveryRecreationError(
                created_name=name,
                created_path=path,
                conflict_paths=tuple(conflicts),
            ) from exc
    raise _RecoveryRecreationError(
        created_name=None,
        created_path=None,
        conflict_paths=tuple(conflicts),
    ) from OSError(errno.EEXIST, "could not allocate a private recovery path")


def _inspect_or_remove_failed_dirfd_recovery(
    directory_fd: int,
    name: str,
    path: Path,
    expected_payload: bytes,
    *,
    uncertainty_path: Path,
) -> _RollbackOutcome:
    """Classify an exact recovery, or durably remove a partial/unknown residue."""
    uncertainty = (
        f"Recovery durability is uncertain for {uncertainty_path}; a partial or unknown private artifact remains."
    )
    try:
        payload, info = _dirfd_read_regular(
            directory_fd,
            name,
            path,
            limit=max(len(expected_payload), 1),
        )
    except SecurePublishError:
        return _remove_or_report_dirfd_residue(
            directory_fd,
            name,
            path,
            uncertainty,
            observed_payload=None,
            expected_payload=expected_payload,
        )
    if payload == expected_payload and stat.S_IMODE(info.st_mode) & 0o077 == 0:
        return _RollbackOutcome(recovery_paths=(path,))
    return _remove_or_report_dirfd_residue(
        directory_fd,
        name,
        path,
        uncertainty,
        observed_payload=payload,
        expected_payload=expected_payload,
    )


def _remove_or_report_dirfd_residue(
    directory_fd: int,
    name: str,
    path: Path,
    uncertainty: str,
    *,
    observed_payload: bytes | None,
    expected_payload: bytes,
) -> _RollbackOutcome:
    """Durably remove one residue or report its exact lexical path without claiming its bytes."""
    try:
        os.unlink(name, dir_fd=directory_fd)
    except OSError:
        try:
            retained = _dirfd_exists(directory_fd, name)
        except OSError:
            retained = True
        if retained:
            return _RollbackOutcome(residue_paths=(path,), recovery_notes=(uncertainty,))
        return _RollbackOutcome()
    try:
        os.fsync(directory_fd)
    except OSError:
        try:
            retained = _dirfd_exists(directory_fd, name)
        except OSError:
            retained = True
        if retained:
            return _RollbackOutcome(residue_paths=(path,), recovery_notes=(uncertainty,))
        if observed_payload is not None:
            try:
                recreated = _recreate_dirfd_recovery(directory_fd, name, path, observed_payload)
            except _RecoveryRecreationError as exc:
                conflicts = _RollbackOutcome(conflict_paths=exc.conflict_paths)
                if exc.created_name is None or exc.created_path is None:
                    return _merge_rollback_outcomes(
                        conflicts,
                        _RollbackOutcome(recovery_notes=(uncertainty,)),
                    )
                inspected = _inspect_or_remove_failed_dirfd_recovery(
                    directory_fd,
                    exc.created_name,
                    exc.created_path,
                    expected_payload,
                    uncertainty_path=path,
                )
                return _merge_rollback_outcomes(conflicts, inspected)
            if observed_payload == expected_payload:
                return _RollbackOutcome(
                    recovery_paths=(recreated.path,),
                    conflict_paths=recreated.conflict_paths,
                )
            return _RollbackOutcome(
                residue_paths=(recreated.path,),
                conflict_paths=recreated.conflict_paths,
                recovery_notes=(uncertainty,),
            )
        return _RollbackOutcome(recovery_notes=(uncertainty,))
    return _RollbackOutcome()


def _link_dirfd_recovery(directory_fd: int, destination: Path, backup_name: str) -> list[Path]:
    backup_path = destination.parent / backup_name
    for _attempt in range(100):
        recovery_name = f".{destination.name}.recovery.{secrets.token_hex(12)}"
        recovery_path = destination.parent / recovery_name
        try:
            os.link(
                backup_name,
                recovery_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            continue
        except OSError:
            return [backup_path]
        try:
            os.fsync(directory_fd)
        except OSError:
            return [recovery_path, backup_path]
        try:
            os.unlink(backup_name, dir_fd=directory_fd)
        except OSError:
            return [recovery_path, backup_path]
        try:
            os.fsync(directory_fd)
        except OSError:
            return [recovery_path]
        return [recovery_path]
    return [backup_path]


def _secure_atomic_publish_path(
    ordered: list[tuple[Path, bytes]],
    guards: dict[Path, DirectoryIdentity],
    expectations: dict[Path, FileExpectation | None],
) -> None:
    # A path-following mutation cannot close the gap between checking an ancestor and opening a
    # descendant. When the selector is forced to this compatibility entry point on a platform that
    # still has the complete dirfd primitive set, use the secure descriptor transaction. A platform
    # that genuinely lacks those primitives fails closed instead of following a mutable path.
    if not _dirfd_publication_primitives_available() or not _secure_directory_walk_supported():
        raise SecurePublishError(
            "unsafe_directory",
            ordered[0][0].parent,
            "Secure nested publication is unavailable on this platform.",
        )
    _secure_atomic_publish_dirfd(ordered, guards, expectations)


def _dirfd_transactions_supported() -> bool:
    return _dirfd_publication_primitives_available()


def _dirfd_publication_primitives_available() -> bool:
    return _DIRFD_PUBLICATION_AVAILABLE


def _rename_noreplace_dirfd(directory_fd: int, source_name: str, destination_name: str) -> None:
    _rename_noreplace(source_name, destination_name, source_dir_fd=directory_fd, destination_dir_fd=directory_fd)


def _rename_noreplace(
    source: str,
    destination: str,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
) -> None:
    """Atomically move one entry without replacing an existing destination."""
    if os.name == "nt":
        if source_dir_fd is not None or destination_dir_fd is not None:
            raise OSError(errno.ENOTSUP, "directory-relative no-replace rename is unavailable")
        os.rename(source, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function_name = "renameatx_np" if source_dir_fd is not None else "renamex_np"
        try:
            function = getattr(library, function_name)
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable") from exc
        if source_dir_fd is None:
            result = function(
                ctypes.c_char_p(source_bytes),
                ctypes.c_char_p(destination_bytes),
                ctypes.c_uint(_DARWIN_RENAME_EXCL),
            )
        else:
            result = function(
                ctypes.c_int(source_dir_fd),
                ctypes.c_char_p(source_bytes),
                ctypes.c_int(destination_dir_fd),
                ctypes.c_char_p(destination_bytes),
                ctypes.c_uint(_DARWIN_RENAME_EXCL),
            )
    elif sys.platform.startswith("linux"):
        try:
            function = library.renameat2
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable") from exc
        source_fd = _AT_FDCWD if source_dir_fd is None else source_dir_fd
        destination_fd = _AT_FDCWD if destination_dir_fd is None else destination_dir_fd
        result = function(
            ctypes.c_int(source_fd),
            ctypes.c_char_p(source_bytes),
            ctypes.c_int(destination_fd),
            ctypes.c_char_p(destination_bytes),
            ctypes.c_uint(_LINUX_RENAME_NOREPLACE),
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _directory_identity_from_stat(info: os.stat_result) -> DirectoryIdentity:
    return DirectoryIdentity(info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _file_identity_from_stat(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns)


def _assert_directory_identity(path: Path, expected: DirectoryIdentity) -> None:
    try:
        current = capture_directory_identity(path)
    except SecurePublishError as exc:
        raise SecurePublishError("directory_changed", path, "Guarded directory changed during publication.") from exc
    if current != expected:
        raise SecurePublishError("directory_changed", path, "Guarded directory changed during publication.")


def _unused_dirfd_name(directory_fd: int, prefix: str) -> str:
    for _attempt in range(100):
        name = f"{prefix}{secrets.token_hex(12)}"
        if not _dirfd_exists(directory_fd, name):
            return name
    raise SecurePublishError("write_failed", Path(prefix), "Could not allocate a private output name.")


def _create_private_dirfd_file(directory_fd: int, prefix: str) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(100):
        name = f"{prefix}{secrets.token_hex(12)}"
        try:
            return name, os.open(name, flags, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
    raise SecurePublishError("write_failed", Path(prefix), "Could not create a private output file.")


def _dirfd_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _dirfd_file_expectation(
    directory_fd: int,
    name: str,
    reported_path: Path,
    *,
    limit: int,
) -> FileExpectation:
    payload, info = _dirfd_read_regular(directory_fd, name, reported_path, limit=limit)
    return FileExpectation(payload, _file_identity_from_stat(info))


def _validate_dirfd_expectation(
    directory_fd: int,
    name: str,
    expected: FileExpectation | None,
    reported_path: Path,
) -> None:
    exists = _dirfd_exists(directory_fd, name)
    if expected is None:
        if exists:
            raise SecurePublishError("target_changed", reported_path, "Target appeared during publication.")
        return
    if not exists:
        raise SecurePublishError("target_changed", reported_path, "Target disappeared during publication.")
    try:
        current = _dirfd_file_expectation(
            directory_fd,
            name,
            reported_path,
            limit=max(len(expected.payload), 1),
        )
    except SecurePublishError as exc:
        raise SecurePublishError("target_changed", reported_path, "Target changed during publication.") from exc
    if current != expected:
        raise SecurePublishError("target_changed", reported_path, "Target changed during publication.")


def _dirfd_read_regular(
    directory_fd: int,
    name: str,
    reported_path: Path,
    *,
    limit: int,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise SecurePublishError("unsafe_target", reported_path, "Target is unsafe or unreadable.") from exc
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise SecurePublishError("unsafe_target", reported_path, "Target must be a supported regular file.")
        with os.fdopen(file_fd, "rb", closefd=False) as handle:
            payload = handle.read(limit + 1)
        after = os.fstat(file_fd)
        if len(payload) > limit:
            raise SecurePublishError("unsafe_target", reported_path, "Target exceeds the supported size limit.")
        if _file_identity_from_stat(before) != _file_identity_from_stat(after) or len(payload) != after.st_size:
            raise SecurePublishError("target_changed", reported_path, "Target changed while being read.")
        return payload, after
    finally:
        os.close(file_fd)


def _dirfd_unlink(directory_fd: int | None, name: str) -> None:
    if directory_fd is None:
        return
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def _path_read_regular(path: Path, *, limit: int) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecurePublishError("unsafe_target", path, "Target is unsafe or unreadable.") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise SecurePublishError("unsafe_target", path, "Target must be a supported regular file.")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(limit + 1)
        after = os.fstat(descriptor)
        if len(payload) > limit:
            raise SecurePublishError("unsafe_target", path, "Target exceeds the supported size limit.")
        if _file_identity_from_stat(before) != _file_identity_from_stat(after) or len(payload) != after.st_size:
            raise SecurePublishError("target_changed", path, "Target changed while being read.")
        return payload, after
    finally:
        os.close(descriptor)


def sanitize_url(url: str) -> str:
    """Return a URL with credential-like query parameters removed."""
    try:
        parsed = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not _sensitive(key)]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    except (TypeError, ValueError):
        return "redacted-source"


def sanitize_mapping(value: Any) -> Any:
    """Recursively remove credential-bearing mapping entries before persistence."""
    if isinstance(value, dict):
        return {key: sanitize_mapping(item) for key, item in value.items() if not _sensitive(str(key))}
    if isinstance(value, list):
        return [sanitize_mapping(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_mapping(item) for item in value]
    return value


def json_for_html_script(value: Any) -> str:
    """Serialize data for a script block without allowing an HTML end-tag breakout."""
    return (
        json.dumps(value, ensure_ascii=False, default=str)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)
