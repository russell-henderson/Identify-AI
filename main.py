from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import flet as ft

from analyzer import analyze_file
from utils import get_thumbnail, load_config


@dataclass(slots=True)
class FileRow:
    path: Path
    image: ft.Image
    proposed_name: ft.Text
    category: ft.Text
    status: ft.Text
    reasoning: ft.Text
    container: ft.Container


async def main(page: ft.Page) -> None:
    page.title = "Identify-AI"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = ft.Colors.BLACK
    page.padding = 0

    page.decoration = ft.BoxDecoration(
        gradient=ft.LinearGradient(
            begin=ft.Alignment(-1.0, -1.0),
            end=ft.Alignment(1.0, 1.0),
            colors=["#0f172a", "#1e3a8a", "#0f172a"],
        )
    )

    try:
        config = await load_config()
    except Exception as exc:
        page.add(
            ft.SafeArea(
                content=ft.Container(
                    padding=24,
                    content=ft.Text(f"Unable to load config.json: {exc}", size=16),
                )
            )
        )
        return

    supported_extensions = {
        str(extension).lower()
        for extension in config["processing"].get("supported_extensions", [])
    }
    recursive_folder_scan = bool(
        config["processing"].get("recursive_folder_scan", True)
    )

    file_picker = ft.FilePicker()
    folder_picker = ft.FilePicker()

    status_text = ft.Text("Ready", size=13, color=ft.Colors.BLUE_GREY_200)
    progress_bar = ft.ProgressBar(value=0, bar_height=4)
    file_list = ft.ListView(expand=True, spacing=10, padding=0)

    pick_files_button = ft.FilledButton("Pick Files")
    pick_folder_button = ft.FilledButton("Pick Folder")

    placeholder_thumbnail = await get_thumbnail(Path("placeholder.file"))
    active_batch_task: asyncio.Future | None = None

    async def set_controls_enabled(enabled: bool) -> None:
        pick_files_button.disabled = not enabled
        pick_folder_button.disabled = not enabled
        page.update()

    async def on_pick_files(_event) -> None:
        nonlocal active_batch_task
        selected = await file_picker.pick_files(
            dialog_title="Select files for Identify-AI",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=[ext.lstrip(".") for ext in sorted(supported_extensions)],
            allow_multiple=True,
        )
        if not selected:
            status_text.value = "File selection cancelled"
            page.update()
            return

        paths = [Path(item.path) for item in selected if item.path]
        active_batch_task = page.run_task(start_batch, paths)

    async def on_pick_folder(_event) -> None:
        nonlocal active_batch_task
        selected_directory = await folder_picker.get_directory_path(
            dialog_title="Select a folder for Identify-AI"
        )
        if not selected_directory:
            status_text.value = "Folder selection cancelled"
            page.update()
            return

        status_text.value = "Scanning folder..."
        progress_bar.value = None
        await set_controls_enabled(False)

        try:
            paths = await asyncio.to_thread(
                scan_folder,
                Path(selected_directory),
                supported_extensions,
                recursive_folder_scan,
            )
        except Exception as exc:
            status_text.value = f"Folder scan failed: {exc}"
            progress_bar.value = 0
            await set_controls_enabled(True)
            return

        if not paths:
            status_text.value = "No supported files were found in the selected folder"
            progress_bar.value = 0
            await set_controls_enabled(True)
            return

        active_batch_task = page.run_task(start_batch, paths)

    async def start_batch(paths: Iterable[Path]) -> None:
        unique_paths = await asyncio.to_thread(deduplicate_paths, paths)
        if not unique_paths:
            status_text.value = "No valid files selected"
            progress_bar.value = 0
            await set_controls_enabled(True)
            return

        await set_controls_enabled(False)
        file_list.controls.clear()
        progress_bar.value = 0
        status_text.value = f"Preparing {len(unique_paths)} file(s)..."

        rows: list[FileRow] = []
        for path in unique_paths:
            row = build_file_row(path, placeholder_thumbnail)
            rows.append(row)
            file_list.controls.append(row.container)

        page.update()

        try:
            page.run_task(update_thumbnails_bg, rows)

            total = len(rows)
            for index, row in enumerate(rows, start=1):
                row.status.value = "Queued"
                status_text.value = f"Processing {index} of {total}: {row.path.name}"
                page.update()

                async def callback(message: str, current_row: FileRow = row) -> None:
                    current_row.status.value = message
                    status_text.value = f"{current_row.path.name}: {message}"
                    page.update()

                result = await analyze_file(row.path, callback)
                apply_analysis_result(row, result)
                progress_bar.value = index / total
                status_text.value = f"Completed {index} of {total}: {row.path.name}"
                page.update()

            status_text.value = f"Batch complete: {total} file(s) processed"
        except Exception as exc:
            status_text.value = f"Unexpected batch error: {exc}"
        finally:
            if progress_bar.value is None:
                progress_bar.value = 0
            await set_controls_enabled(True)

    async def update_thumbnails_bg(rows: list[FileRow]) -> None:
        sem = asyncio.Semaphore(4)

        async def process_single(r: FileRow) -> None:
            async with sem:
                try:
                    thumbnail_result = await get_thumbnail(r.path)
                    r.image.src = thumbnail_result
                except Exception as e:
                    r.status.value = f"Thumbnail unavailable: {e}"
                finally:
                    page.update()

        await asyncio.gather(*(process_single(row) for row in rows))

    pick_files_button.on_click = on_pick_files
    pick_folder_button.on_click = on_pick_folder

    header = ft.Container(
        padding=ft.Padding.symmetric(horizontal=24, vertical=20),
        bgcolor="#1AFFFFFF",
        blur=ft.Blur(15, 15, ft.BlurTileMode.MIRROR),
        border=ft.Border(bottom=ft.BorderSide(1, "#1AFFFFFF")),
        content=ft.Column(
            spacing=14,
            controls=[
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text("Identify-AI", size=26, weight=ft.FontWeight.BOLD),
                        ft.Text(
                            "Local content analysis and standardized filename proposals",
                            size=13,
                            color=ft.Colors.BLUE_GREY_200,
                        ),
                    ],
                ),
                ft.Row(
                    spacing=12,
                    controls=[pick_files_button, pick_folder_button],
                ),
                progress_bar,
                status_text,
            ],
        ),
    )

    empty_hint = ft.Container(
        padding=ft.Padding.only(left=24, right=24, top=18, bottom=8),
        content=ft.Text(
            "Select individual files or a folder. Files are analyzed locally through Ollama.",
            size=13,
            color=ft.Colors.BLUE_GREY_300,
        ),
    )

    page.add(
        ft.SafeArea(
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    header,
                    empty_hint,
                    ft.Container(
                        expand=True,
                        padding=ft.Padding.symmetric(horizontal=24, vertical=12),
                        content=file_list,
                    ),
                ],
            ),
        )
    )


def build_file_row(path: Path, placeholder_thumbnail: str) -> FileRow:
    image = ft.Image(
        src=placeholder_thumbnail,
        width=64,
        height=64,
        fit=ft.BoxFit.COVER,
        border_radius=10,
    )
    proposed_name = ft.Text(
        path.name,
        size=15,
        weight=ft.FontWeight.W_600,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    category = ft.Text(
        "Unclassified",
        size=12,
        color=ft.Colors.CYAN_200,
    )
    status = ft.Text(
        "Waiting",
        size=12,
        color=ft.Colors.BLUE_GREY_300,
    )
    reasoning = ft.Text(
        str(path),
        size=12,
        color=ft.Colors.BLUE_GREY_300,
        max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

    container = ft.Container(
        padding=14,
        border_radius=14,
        bgcolor="#0DFFFFFF",
        blur=ft.Blur(10, 10, ft.BlurTileMode.MIRROR),
        border=ft.Border(
            top=ft.BorderSide(1, "#1AFFFFFF"),
            right=ft.BorderSide(1, "#1AFFFFFF"),
            bottom=ft.BorderSide(1, "#1AFFFFFF"),
            left=ft.BorderSide(1, "#1AFFFFFF"),
        ),
        content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=14,
            controls=[
                image,
                ft.Column(
                    expand=True,
                    spacing=4,
                    controls=[proposed_name, category, reasoning, status],
                ),
            ],
        ),
    )

    return FileRow(
        path=path,
        image=image,
        proposed_name=proposed_name,
        category=category,
        status=status,
        reasoning=reasoning,
        container=container,
    )


def apply_analysis_result(row: FileRow, result: dict[str, str]) -> None:
    row.proposed_name.value = result.get("suggested_name", row.path.name)
    row.category.value = result.get("category", "uncategorized")
    row.reasoning.value = result.get("reasoning", "No reasoning returned")

    if result.get("category") == "error":
        row.status.value = "Failed"
        row.status.color = ft.Colors.RED_300
        row.category.color = ft.Colors.RED_300
    else:
        row.status.value = "Complete"
        row.status.color = ft.Colors.GREEN_300
        row.category.color = ft.Colors.CYAN_200


def scan_folder(
    directory: Path,
    supported_extensions: set[str],
    recursive: bool,
) -> list[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"Folder does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a folder: {directory}")

    iterator = directory.rglob("*") if recursive else directory.glob("*")
    files = [
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in supported_extensions
    ]
    return sorted(files, key=lambda path: str(path).lower())


def deduplicate_paths(paths: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if resolved.is_file():
            unique[str(resolved).casefold()] = resolved
    return list(unique.values())


if __name__ == "__main__":
    ft.run(main)
