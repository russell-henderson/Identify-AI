from __future__ import annotations

import asyncio
import shutil
from collections import Counter
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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
    copy_button: ft.TextButton
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
    analysis_workers = max(1, int(config["processing"].get("analysis_workers", 2)))
    staging_root = Path(config["paths"]["staging"])
    archive_root = Path(config["paths"]["archive"])

    file_picker = ft.FilePicker()
    folder_picker = ft.FilePicker()

    status_text = ft.Text("Ready", size=13, color=ft.Colors.BLUE_GREY_200)
    progress_bar = ft.ProgressBar(value=0, bar_height=4)
    file_list = ft.ListView(expand=True, spacing=10, padding=0)

    pick_files_button = ft.FilledButton("Pick Files")
    pick_folder_button = ft.FilledButton("Pick Folder")

    placeholder_thumbnail = await get_thumbnail(Path("placeholder.file"))
    background_tasks: set[asyncio.Task[Any]] = set()

    def safe_update() -> None:
        try:
            page.update()
        except (asyncio.CancelledError, RuntimeError):
            pass

    def track_background_task(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        background_tasks.add(task)

        def _cleanup(_task: asyncio.Task[Any]) -> None:
            background_tasks.discard(_task)

        task.add_done_callback(_cleanup)
        return task

    def launch_background_task(coro: Any, *args: Any) -> asyncio.Task[Any]:
        return track_background_task(page.run_task(coro, *args))

    async def cancel_background_tasks() -> None:
        tasks = [task for task in background_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def set_controls_enabled(enabled: bool) -> None:
        pick_files_button.disabled = not enabled
        pick_folder_button.disabled = not enabled
        safe_update()

    async def on_pick_files(_event) -> None:
        selected = await file_picker.pick_files(
            dialog_title="Select files for Identify-AI",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=[ext.lstrip(".") for ext in sorted(supported_extensions)],
            allow_multiple=True,
        )
        if not selected:
            status_text.value = "File selection cancelled"
            safe_update()
            return

        paths = [Path(item.path) for item in selected if item.path]
        launch_background_task(start_batch, paths, None)

    async def on_pick_folder(_event) -> None:
        selected_directory = await folder_picker.get_directory_path(
            dialog_title="Select a folder for Identify-AI"
        )
        if not selected_directory:
            status_text.value = "Folder selection cancelled"
            safe_update()
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

        launch_background_task(start_batch, paths, Path(selected_directory))

    async def start_batch(paths: Iterable[Path], source_directory: Path | None) -> None:
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
            async def handle_copy(_event, current_row: FileRow = row) -> None:
                launch_background_task(copy_row_to_staging, current_row)

            row.copy_button.on_click = handle_copy
            rows.append(row)
            file_list.controls.append(row.container)

        safe_update()

        try:
            launch_background_task(update_thumbnails_bg, rows)

            total = len(rows)
            worker_limit = asyncio.Semaphore(analysis_workers)

            async def process_row(index: int, row: FileRow) -> tuple[int, FileRow]:
                async with worker_limit:
                    row.status.value = "Queued"
                    status_text.value = f"Processing {index} of {total}: {row.path.name}"
                    safe_update()

                    async def callback(message: str, current_row: FileRow = row) -> None:
                        current_row.status.value = message
                        status_text.value = f"{current_row.path.name}: {message}"
                        safe_update()

                    try:
                        result = await analyze_file(row.path, callback)
                        apply_analysis_result(row, result)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        row.status.value = f"Error: {exc}"
                        row.status.color = ft.Colors.RED_300
                        row.category.color = ft.Colors.RED_300
                        safe_update()
                    return index, row

            worker_tasks = [
                track_background_task(asyncio.create_task(process_row(index, row)))
                for index, row in enumerate(rows, start=1)
            ]

            completed = 0
            for finished in asyncio.as_completed(worker_tasks):
                _, row = await finished
                completed += 1
                progress_bar.value = completed / total
                status_text.value = f"Completed {completed} of {total}: {row.path.name}"
                safe_update()

            status_text.value = f"Batch complete: {total} file(s) processed"
            try:
                report_path = await asyncio.to_thread(
                    write_batch_report,
                    unique_paths,
                    rows,
                    source_directory,
                    archive_root,
                )
                status_text.value = (
                    f"Batch complete: {total} file(s) processed. "
                    f"Report saved: {report_path.name}"
                )
            except Exception as exc:
                status_text.value = (
                    f"Batch complete: {total} file(s) processed. "
                    f"Report generation failed: {exc}"
                )
            safe_update()
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
                    safe_update()

        await asyncio.gather(*(process_single(row) for row in rows))

    async def copy_row_to_staging(row: FileRow) -> None:
        source = row.path
        suggested_name = row.proposed_name.value.strip() or source.name
        destination = _unique_destination_path(staging_root, suggested_name)

        row.copy_button.disabled = True
        row.status.value = "Copying to Staging..."
        safe_update()

        try:
            await asyncio.to_thread(_copy_file, source, destination)
        except Exception as exc:
            row.copy_button.disabled = False
            row.status.value = f"Copy failed: {exc}"
            row.status.color = ft.Colors.RED_300
            safe_update()
            return

        row.status.value = f"Copied to Staging: {destination.name}"
        row.status.color = ft.Colors.GREEN_300
        row.copy_button.text = "Copied"
        safe_update()

    async def handle_disconnect(_event) -> None:
        await cancel_background_tasks()

    pick_files_button.on_click = on_pick_files
    pick_folder_button.on_click = on_pick_folder
    page.on_disconnect = handle_disconnect

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
    copy_button = ft.TextButton("Copy to Staging", disabled=True)
    reasoning = ft.Text(
        str(path),
        size=12,
        color=ft.Colors.BLUE_GREY_300,
        max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

    actions = ft.Column(
        spacing=8,
        horizontal_alignment=ft.CrossAxisAlignment.END,
        controls=[copy_button],
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
                actions,
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
        copy_button=copy_button,
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
        row.copy_button.disabled = True
    else:
        row.status.value = "Complete"
        row.status.color = ft.Colors.GREEN_300
        row.category.color = ft.Colors.CYAN_200
        row.copy_button.disabled = False


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


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _unique_destination_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    for counter in range(1, 1000):
        numbered = directory / f"{stem} ({counter}){suffix}"
        if not numbered.exists():
            return numbered

    raise FileExistsError(f"Unable to find a free destination name in {directory}")


def write_batch_report(
    paths: list[Path],
    rows: list[FileRow],
    source_directory: Path | None,
    archive_root: Path,
) -> Path:
    report_root = archive_root / "Reports"
    report_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if source_directory is None:
        batch_label = "selected-files"
        batch_title = "Selected Files"
        source_display = "Manual file selection"
    else:
        batch_label = _slugify_report_name(source_directory.name)
        batch_title = source_directory.name or "Folder"
        source_display = str(source_directory)

    report_path = report_root / f"{timestamp}_{batch_label}_analysis.md"

    category_counts = Counter((row.category.value or "uncategorized").strip() for row in rows)
    completed_count = sum(1 for row in rows if row.status.value == "Complete")
    failed_count = sum(1 for row in rows if row.status.value == "Failed")

    lines: list[str] = [
        f"# Identify-AI Analysis Report - {batch_title}",
        "",
        f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Source: {source_display}",
        f"- Files analyzed: {len(paths)}",
        f"- Completed: {completed_count}",
        f"- Failed: {failed_count}",
        "",
        "## Category Summary",
        "",
    ]

    if category_counts:
        for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0].lower())):
            lines.append(f"- {category}: {count}")
    else:
        lines.append("- No categories recorded")

    lines.extend(
        [
            "",
            "## File Details",
            "",
            "| File | Suggested Name | Category | Status | Reasoning |",
            "|---|---|---|---|---|",
        ]
    )

    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_markdown_cell(row.path.name),
                    _escape_markdown_cell(row.proposed_name.value or row.path.name),
                    _escape_markdown_cell(row.category.value or "uncategorized"),
                    _escape_markdown_cell(row.status.value or "Unknown"),
                    _escape_markdown_cell(row.reasoning.value or ""),
                ]
            )
            + " |"
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _escape_markdown_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _slugify_report_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "batch"


if __name__ == "__main__":
    ft.run(main)
