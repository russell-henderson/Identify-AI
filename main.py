from __future__ import annotations

import asyncio
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import flet as ft

from analyzer import analyze_file, check_ollama, normalize_user_filename
from utils import get_thumbnail, load_config


@dataclass(slots=True)
class FileRow:
    path: Path
    image: ft.Image
    proposed_name: ft.TextField
    category: ft.Text
    status: ft.Text
    reasoning: ft.Text
    destination: ft.Text
    approved: ft.Checkbox
    copy_button: ft.TextButton
    retry_button: ft.TextButton
    container: ft.Container
    staged_path: Path | None = None


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
        page.add(ft.SafeArea(content=ft.Container(padding=24, content=ft.Text(f"Unable to load config.json: {exc}", size=16))))
        return

    supported_extensions = {str(extension).lower() for extension in config["processing"].get("supported_extensions", [])}
    recursive_folder_scan = bool(config["processing"].get("recursive_folder_scan", True))
    analysis_workers = max(1, int(config["processing"].get("analysis_workers", 2)))
    staging_root = Path(config["paths"]["staging"])
    archive_root = Path(config["paths"]["archive"])

    preflight_error: str | None = None
    try:
        await asyncio.to_thread(_prepare_output_directories, staging_root, archive_root)
        preflight_error = await check_ollama(config)
    except Exception as exc:
        preflight_error = f"Cannot prepare output folders: {exc}"

    file_picker = ft.FilePicker()
    folder_picker = ft.FilePicker()
    status_text = ft.Text("Ready" if not preflight_error else f"Setup required: {preflight_error}", size=13, color=ft.Colors.BLUE_GREY_200 if not preflight_error else ft.Colors.AMBER_200)
    progress_bar = ft.ProgressBar(value=0, bar_height=4)
    file_list = ft.ListView(expand=True, spacing=10, padding=0)
    setup_hint = ft.Text(
        "All analysis remains on this device. Staging creates renamed copies; originals are never changed.",
        size=12,
        color=ft.Colors.BLUE_GREY_300,
    )
    if preflight_error:
        setup_hint.value = f"Before analysis: {preflight_error}"
        setup_hint.color = ft.Colors.AMBER_200

    pick_files_button = ft.FilledButton("Pick Files", disabled=bool(preflight_error))
    pick_folder_button = ft.FilledButton("Pick Folder", disabled=bool(preflight_error))
    stage_all_button = ft.FilledButton("Stage Approved (0)", disabled=True)
    placeholder_thumbnail = await get_thumbnail(Path("placeholder.file"))
    background_tasks: set[asyncio.Task[Any]] = set()
    current_rows: list[FileRow] = []
    current_paths: list[Path] = []
    current_source_directory: Path | None = None
    current_batch_id: str | None = None

    def safe_update() -> None:
        try:
            page.update()
        except (asyncio.CancelledError, RuntimeError):
            pass

    def track_background_task(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return task

    def launch_background_task(coro: Any, *args: Any) -> asyncio.Task[Any]:
        return track_background_task(page.run_task(coro, *args))

    async def cancel_background_tasks() -> None:
        tasks = [task for task in background_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def refresh_stage_all() -> None:
        approved = [row for row in current_rows if row.approved.value and row.status.value == "Complete" and row.staged_path is None]
        stage_all_button.text = f"Stage Approved ({len(approved)})"
        stage_all_button.disabled = not approved

    async def set_pick_controls_enabled(enabled: bool) -> None:
        pick_files_button.disabled = not enabled or bool(preflight_error)
        pick_folder_button.disabled = not enabled or bool(preflight_error)
        safe_update()

    def update_destination(row: FileRow) -> None:
        filename = normalize_user_filename(row.proposed_name.value or row.path.name, row.path, config)
        row.destination.value = f"Staging: {staging_root / filename}"

    async def on_name_change(_event: Any, row: FileRow) -> None:
        update_destination(row)
        safe_update()

    async def on_approval_change(_event: Any) -> None:
        refresh_stage_all()
        safe_update()

    async def on_pick_files(_event: Any) -> None:
        selected = await file_picker.pick_files(
            dialog_title="Select files for Identify-AI",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=[extension.lstrip(".") for extension in sorted(supported_extensions)],
            allow_multiple=True,
        )
        if not selected:
            status_text.value = "File selection cancelled"
            safe_update()
            return
        launch_background_task(start_batch, [Path(item.path) for item in selected if item.path], None)

    async def on_pick_folder(_event: Any) -> None:
        selected_directory = await folder_picker.get_directory_path(dialog_title="Select a folder for Identify-AI")
        if not selected_directory:
            status_text.value = "Folder selection cancelled"
            safe_update()
            return
        status_text.value = "Scanning folder..."
        progress_bar.value = None
        await set_pick_controls_enabled(False)
        try:
            paths = await asyncio.to_thread(scan_folder, Path(selected_directory), supported_extensions, recursive_folder_scan)
        except Exception as exc:
            status_text.value = f"Folder scan failed: {exc}"
            progress_bar.value = 0
            await set_pick_controls_enabled(True)
            return
        if not paths:
            status_text.value = "No supported files were found in the selected folder"
            progress_bar.value = 0
            await set_pick_controls_enabled(True)
            return
        launch_background_task(start_batch, paths, Path(selected_directory))

    async def process_row(index: int, total: int, row: FileRow, semaphore: asyncio.Semaphore) -> FileRow:
        async with semaphore:
            row.status.value = "Queued"
            status_text.value = f"Processing {index} of {total}: {row.path.name}"
            safe_update()

            async def callback(message: str) -> None:
                row.status.value = message
                status_text.value = f"{row.path.name}: {message}"
                safe_update()

            result = await analyze_file(row.path, callback)
            apply_analysis_result(row, result)
            update_destination(row)
            refresh_stage_all()
            safe_update()
            return row

    async def retry_row(row: FileRow) -> None:
        row.retry_button.disabled = True
        row.approved.disabled = True
        try:
            await process_row(1, 1, row, asyncio.Semaphore(1))
        finally:
            row.retry_button.disabled = row.status.value == "Complete"
            safe_update()

    async def start_batch(paths: Iterable[Path], source_directory: Path | None) -> None:
        nonlocal current_rows, current_paths, current_source_directory, current_batch_id
        current_paths = await asyncio.to_thread(deduplicate_paths, paths)
        current_source_directory = source_directory
        current_batch_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if not current_paths:
            status_text.value = "No valid files selected"
            progress_bar.value = 0
            await set_pick_controls_enabled(True)
            return

        await set_pick_controls_enabled(False)
        file_list.controls.clear()
        current_rows = []
        progress_bar.value = 0
        status_text.value = f"Preparing {len(current_paths)} file(s)..."
        for path in current_paths:
            row = build_file_row(path, placeholder_thumbnail)
            row.proposed_name.on_change = lambda event, current_row=row: on_name_change(event, current_row)
            row.approved.on_change = on_approval_change
            row.copy_button.on_click = lambda _event, current_row=row: launch_background_task(stage_row, current_row)
            row.retry_button.on_click = lambda _event, current_row=row: launch_background_task(retry_row, current_row)
            current_rows.append(row)
            file_list.controls.append(row.container)
        refresh_stage_all()
        safe_update()

        try:
            launch_background_task(update_thumbnails_bg, current_rows)
            semaphore = asyncio.Semaphore(analysis_workers)
            workers = [track_background_task(asyncio.create_task(process_row(index, len(current_rows), row, semaphore))) for index, row in enumerate(current_rows, start=1)]
            completed = 0
            for finished in asyncio.as_completed(workers):
                row = await finished
                completed += 1
                progress_bar.value = completed / len(current_rows)
                status_text.value = f"Completed {completed} of {len(current_rows)}: {row.path.name}"
                safe_update()
            report_path, manifest_path = await asyncio.to_thread(write_batch_artifacts, current_paths, current_rows, current_source_directory, archive_root, config, current_batch_id)
            status_text.value = f"Batch complete. Report: {report_path.name}; manifest: {manifest_path.name}"
        except asyncio.CancelledError:
            status_text.value = "Batch cancelled; completed items remain available for review."
            raise
        except Exception as exc:
            status_text.value = f"Unexpected batch error: {exc}"
        finally:
            if progress_bar.value is None:
                progress_bar.value = 0
            await set_pick_controls_enabled(True)
            refresh_stage_all()

    async def update_thumbnails_bg(rows: list[FileRow]) -> None:
        semaphore = asyncio.Semaphore(4)
        async def process_single(row: FileRow) -> None:
            async with semaphore:
                row.image.src = await get_thumbnail(row.path)
                safe_update()
        await asyncio.gather(*(process_single(row) for row in rows))

    async def stage_row(row: FileRow) -> None:
        if row.status.value != "Complete" or row.staged_path:
            return
        filename = normalize_user_filename(row.proposed_name.value or row.path.name, row.path, config)
        row.proposed_name.value = filename
        destination = _unique_destination_path(staging_root, filename)
        row.copy_button.disabled = True
        row.status.value = "Copying to Staging..."
        safe_update()
        try:
            await asyncio.to_thread(_copy_file, row.path, destination)
            row.staged_path = destination
            row.status.value = f"Staged: {destination.name}"
            row.status.color = ft.Colors.GREEN_300
            row.destination.value = f"Staged at: {destination}"
            row.copy_button.text = "Staged"
            row.approved.disabled = True
            await asyncio.to_thread(write_batch_artifacts, current_paths, current_rows, current_source_directory, archive_root, config, current_batch_id)
        except Exception as exc:
            row.copy_button.disabled = False
            row.status.value = f"Stage failed: {exc}"
            row.status.color = ft.Colors.RED_300
        finally:
            refresh_stage_all()
            safe_update()

    async def stage_approved(_event: Any) -> None:
        for row in list(current_rows):
            if row.approved.value and row.status.value == "Complete" and not row.staged_path:
                await stage_row(row)

    async def handle_disconnect(_event: Any) -> None:
        await cancel_background_tasks()

    pick_files_button.on_click = on_pick_files
    pick_folder_button.on_click = on_pick_folder
    stage_all_button.on_click = stage_approved
    page.on_disconnect = handle_disconnect

    header = ft.Container(
        padding=ft.Padding.symmetric(horizontal=24, vertical=20), bgcolor="#1AFFFFFF", blur=ft.Blur(15, 15, ft.BlurTileMode.MIRROR),
        border=ft.Border(bottom=ft.BorderSide(1, "#1AFFFFFF")),
        content=ft.Column(spacing=14, controls=[
            ft.Column(spacing=2, controls=[ft.Text("Identify-AI", size=26, weight=ft.FontWeight.BOLD), ft.Text("Local review-first file naming and staging", size=13, color=ft.Colors.BLUE_GREY_200)]),
            ft.Row(spacing=12, controls=[pick_files_button, pick_folder_button, stage_all_button]), progress_bar, status_text, setup_hint,
        ]),
    )
    page.add(ft.SafeArea(expand=True, content=ft.Column(expand=True, spacing=0, controls=[header, ft.Container(expand=True, padding=ft.Padding.symmetric(horizontal=24, vertical=12), content=file_list)])))


def build_file_row(path: Path, placeholder_thumbnail: str) -> FileRow:
    image = ft.Image(src=placeholder_thumbnail, width=64, height=64, fit=ft.BoxFit.COVER, border_radius=10)
    proposed_name = ft.TextField(value=path.name, dense=True, text_size=14, expand=True, label="Suggested filename")
    category = ft.Text("Unclassified", size=12, color=ft.Colors.CYAN_200)
    status = ft.Text("Waiting", size=12, color=ft.Colors.BLUE_GREY_300)
    reasoning = ft.Text(str(path), size=12, color=ft.Colors.BLUE_GREY_300, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    destination = ft.Text("Awaiting analysis", size=11, color=ft.Colors.BLUE_GREY_400, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    approved = ft.Checkbox(label="Approve", value=False, disabled=True)
    copy_button = ft.TextButton("Stage Copy", disabled=True)
    retry_button = ft.TextButton("Retry", disabled=True)
    actions = ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.END, controls=[approved, copy_button, retry_button])
    container = ft.Container(
        padding=14, border_radius=14, bgcolor="#0DFFFFFF", blur=ft.Blur(10, 10, ft.BlurTileMode.MIRROR),
        border=ft.Border(top=ft.BorderSide(1, "#1AFFFFFF"), right=ft.BorderSide(1, "#1AFFFFFF"), bottom=ft.BorderSide(1, "#1AFFFFFF"), left=ft.BorderSide(1, "#1AFFFFFF")),
        content=ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=14, controls=[image, ft.Column(expand=True, spacing=4, controls=[proposed_name, category, reasoning, destination, status]), actions]),
    )
    return FileRow(path, image, proposed_name, category, status, reasoning, destination, approved, copy_button, retry_button, container)


def apply_analysis_result(row: FileRow, result: dict[str, str]) -> None:
    row.proposed_name.value = result.get("suggested_name", row.path.name)
    row.category.value = result.get("category", "uncategorized")
    row.reasoning.value = result.get("reasoning", "No reasoning returned")
    if result.get("category") == "error":
        row.status.value = "Failed"
        row.status.color = ft.Colors.RED_300
        row.category.color = ft.Colors.RED_300
        row.approved.disabled = True
        row.copy_button.disabled = True
        row.retry_button.disabled = False
    else:
        row.status.value = "Complete"
        row.status.color = ft.Colors.GREEN_300
        row.category.color = ft.Colors.CYAN_200
        row.approved.disabled = False
        row.copy_button.disabled = False
        row.retry_button.disabled = True


def scan_folder(directory: Path, supported_extensions: set[str], recursive: bool) -> list[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"Folder does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a folder: {directory}")
    iterator = directory.rglob("*") if recursive else directory.glob("*")
    return sorted((path for path in iterator if path.is_file() and path.suffix.lower() in supported_extensions), key=lambda path: str(path).lower())


def deduplicate_paths(paths: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if resolved.is_file():
            unique[str(resolved).casefold()] = resolved
    return list(unique.values())


def _prepare_output_directories(staging_root: Path, archive_root: Path) -> None:
    staging_root.mkdir(parents=True, exist_ok=True)
    (archive_root / "Reports").mkdir(parents=True, exist_ok=True)


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _unique_destination_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    for counter in range(1, 1000):
        numbered = directory / f"{candidate.stem} ({counter}){candidate.suffix}"
        if not numbered.exists():
            return numbered
    raise FileExistsError(f"Unable to find a free destination name in {directory}")


def write_batch_artifacts(paths: list[Path], rows: list[FileRow], source_directory: Path | None, archive_root: Path, config: dict[str, Any], batch_id: str | None = None) -> tuple[Path, Path]:
    report_root = archive_root / "Reports"
    report_root.mkdir(parents=True, exist_ok=True)
    timestamp = batch_id or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    batch_label = _slugify_report_name(source_directory.name) if source_directory else "selected-files"
    report_path = report_root / f"{timestamp}_{batch_label}_analysis.md"
    manifest_path = report_root / f"{timestamp}_{batch_label}_manifest.json"
    counts = Counter((row.category.value or "uncategorized").strip() for row in rows)
    staged = sum(row.staged_path is not None for row in rows)
    completed = sum(row.category.value != "error" for row in rows)
    failed = sum(row.status.value == "Failed" or row.status.value.startswith("Stage failed") for row in rows)
    lines = [f"# Identify-AI Analysis Report - {source_directory.name if source_directory else 'Selected Files'}", "", f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", f"- Source: {source_directory or 'Manual file selection'}", f"- Files analyzed: {len(paths)}", f"- Completed: {completed}", f"- Failed: {failed}", f"- Staged: {staged}", "", "## Category Summary", ""]
    lines.extend(f"- {category}: {count}" for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())))
    lines.extend(["", "## File Details", "", "| File | Final Name | Category | Status | Staged Path | Reasoning |", "|---|---|---|---|---|---|"])
    for row in rows:
        lines.append("| " + " | ".join(_escape_markdown_cell(value) for value in [row.path.name, row.proposed_name.value or row.path.name, row.category.value or "uncategorized", row.status.value or "Unknown", str(row.staged_path or ""), row.reasoning.value or ""]) + " |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {"generated_at": datetime.now().isoformat(timespec="seconds"), "source_directory": str(source_directory) if source_directory else None, "models": {"vision": config["ollama"]["vision_model"], "text": config["ollama"]["text_model"]}, "files": [{"source_path": str(row.path), "original_name": row.path.name, "final_name": row.proposed_name.value or row.path.name, "category": row.category.value, "status": row.status.value, "approved": bool(row.approved.value), "staged_path": str(row.staged_path) if row.staged_path else None, "reasoning": row.reasoning.value} for row in rows]}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report_path, manifest_path


def _escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _slugify_report_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower())
    return "-".join(part for part in cleaned.split("-") if part) or "batch"


if __name__ == "__main__":
    ft.run(main)
