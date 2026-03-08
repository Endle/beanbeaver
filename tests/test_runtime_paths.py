from __future__ import annotations

from pathlib import Path

import beanbeaver.runtime.paths as runtime_paths
from _pytest.monkeypatch import MonkeyPatch


def test_project_root_uses_env_override(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    expected = tmp_path / "beanbeaver-root"
    monkeypatch.setenv("BEANBEAVER_ROOT", str(expected))
    monkeypatch.chdir(tmp_path)

    assert runtime_paths._get_project_root() == expected


def test_project_root_detects_host_project_from_cwd(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    project_root = tmp_path / "ledger"
    nested = project_root / "nested" / "dir"
    nested.mkdir(parents=True)
    (project_root / "main.beancount").write_text("", encoding="utf-8")

    monkeypatch.delenv("BEANBEAVER_ROOT", raising=False)
    monkeypatch.chdir(nested)

    assert runtime_paths._get_project_root() == project_root.resolve()


def test_project_paths_src_prefers_vendored_checkout(tmp_path: Path) -> None:
    host_root = tmp_path / "host"
    vendored_src = host_root / "vendor" / "beanbeaver"
    vendored_src.mkdir(parents=True)

    assert runtime_paths.ProjectPaths(root=host_root).src == vendored_src.resolve()


def test_project_paths_src_falls_back_to_package_root(tmp_path: Path) -> None:
    paths = runtime_paths.ProjectPaths(root=tmp_path / "host")

    assert paths.src == Path(runtime_paths.__file__).resolve().parents[1]


def test_downloads_path_uses_env_override(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    downloads = tmp_path / "downloads"
    monkeypatch.setenv("BEANBEAVER_DOWNLOADS", str(downloads))
    monkeypatch.delenv("XDG_DOWNLOAD_DIR", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)

    assert runtime_paths.ProjectPaths(root=tmp_path).downloads == downloads.resolve()


def test_downloads_path_expands_xdg_home_placeholder(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    target = fake_home / "DownloadsXDG"
    target.mkdir(parents=True)
    monkeypatch.setattr(runtime_paths.Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.delenv("BEANBEAVER_DOWNLOADS", raising=False)
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", "$HOME/DownloadsXDG")
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)

    assert runtime_paths.ProjectPaths(root=tmp_path).downloads == target.resolve()
