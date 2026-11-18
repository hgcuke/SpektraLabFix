import json
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk


BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
EXIFTOOL = BASE_DIR / "exiftool.exe"
ICON = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "fav.ico"
RAW_EXTENSION = ".arw"
MODEL = "ZV-1"
LENS = "ZV-1 & compatibles"
FIXED_DIR = "fixed"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
APP_TITLE = "SpektraLabFix For ZV-1"

BG = "#ffffff"
WHITE = "#ffffff"
TEXT = "#20262e"
MUTED = "#5c6672"
LINE = "#e6e9ed"
ACCENT = "#2f3b47"
ACCENT_ACTIVE = "#202a34"
BTN_BG = "#f4f6f8"
BTN_ACTIVE = "#e9edf1"
HEAD_BG = "#fafbfc"
ROW_ALT = "#f8fafc"
SELECT = "#e8f0f8"
MARK_OK = "#16a34a"
MARK_BAD = "#dc2626"
FONT = ("Microsoft YaHei UI", 9)
BOLD_FONT = ("Microsoft YaHei UI", 9, "bold")
WINDOW_WIDTH = 760
WINDOW_HEIGHT = 480


class StepError(RuntimeError):
    def __init__(self, step, message):
        super().__init__(message)
        self.step = step


def short(text, limit=300):
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def tag_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value).strip()


def exiftool(args):
    if not EXIFTOOL.is_file():
        raise RuntimeError("请把本程序和 exiftool.exe 放在同一个文件夹")
    result = subprocess.run(
        [str(EXIFTOOL), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=NO_WINDOW,
        check=False,
    )
    output = result.stdout.strip()
    error = result.stderr.strip()
    if result.returncode != 0:
        raise RuntimeError(error or output or "ExifTool 执行失败")
    return output


def read_tags(path):
    records = json.loads(exiftool(["-j", "-Model", "-LensModel", str(path)]))
    if not records or not isinstance(records[0], dict):
        raise RuntimeError("ExifTool 没有返回元数据")
    record = records[0]
    if record.get("Error"):
        raise RuntimeError(short(record["Error"]))
    return tag_text(record.get("Model")), tag_text(record.get("LensModel"))


def make_fixed_copy(source, output):
    if output.exists():
        raise StepError("输出已存在", "输出文件已存在，未覆盖")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        try:
            shutil.copy2(source, output)
        except Exception as exc:
            raise StepError("复制失败", short(exc)) from exc
        try:
            exiftool(["-overwrite_original", f"-LensModel={LENS}", str(output)])
        except Exception as exc:
            raise StepError("写入失败", short(exc)) from exc
        try:
            model, lens = read_tags(output)
        except Exception as exc:
            raise StepError("校验失败", f"回读失败：{short(exc)}") from exc
        if model != MODEL:
            raise StepError("校验失败", f"副本 Model 为 {model or '空'}")
        if lens != LENS:
            raise StepError("校验失败", f"副本 LensModel 为 {lens or '空'}")
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        raise


def output_name(source, root):
    return root / FIXED_DIR / f"{source.stem}_fixed{source.suffix}"


def check_model_and_lens(model, lens):
    if not model:
        return False, "缺少 Model"
    if model != MODEL:
        return False, f"Model 为 {model}，不是 ZV-1"
    if lens == LENS:
        return False, "LensModel 已正确"
    return True, "待补写" if not lens else "待修正"


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.attributes("-alpha", 0.0)
        self.root.title(APP_TITLE)
        if ICON.is_file():
            try:
                self.root.iconbitmap(default=str(ICON))
            except tk.TclError:
                pass
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(640, 380)
        self.root.configure(bg=BG)

        self.directory = tk.StringVar()
        self.status_text = tk.StringVar(value="选择照片目录，然后扫描")
        self.count_text = tk.StringVar(value="待处理 0 · 跳过 0 · 读取失败 0")
        self.pending = []
        self.row_by_path = {}
        self.scan_root = None
        self.busy = False
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.running = True
        self.worker = None

        self._style()
        self._build()
        self.set_exiftool_status()
        self.set_buttons(False)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - WINDOW_WIDTH) // 2
        y = (self.root.winfo_screenheight() - WINDOW_HEIGHT) // 2
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")
        self.root.deiconify()
        self.root.update()
        self.root.attributes("-alpha", 1.0)
        self.root.after(60, self._poll)

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=FONT, background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Head.TLabel", background=BG, foreground=TEXT, font=BOLD_FONT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("TEntry", padding=4, fieldbackground=WHITE, bordercolor=LINE)
        style.configure(
            "TButton", background=BTN_BG, foreground=TEXT,
            borderwidth=1, bordercolor=LINE, padding=(12, 5),
        )
        style.map(
            "TButton",
            background=[("active", BTN_ACTIVE), ("disabled", "#f7f8fa")],
            foreground=[("disabled", "#aeb5bd")],
        )
        style.configure(
            "Accent.TButton", background=ACCENT, foreground=WHITE,
            borderwidth=0, padding=(14, 5),
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_ACTIVE), ("disabled", "#b9c0c7")],
            foreground=[("disabled", "#f0f2f4")],
        )
        style.configure(
            "Treeview", background=WHITE, fieldbackground=WHITE,
            foreground=TEXT, borderwidth=0, rowheight=23,
        )
        style.configure(
            "Treeview.Heading", background=HEAD_BG, foreground=MUTED,
            borderwidth=0, padding=(6, 6),
        )
        style.map("Treeview.Heading", background=[("active", HEAD_BG)])
        style.map("Treeview", background=[("selected", SELECT)])
        style.configure("Vertical.TScrollbar", background=BTN_BG, borderwidth=0, troughcolor=BG)

    def _line(self, parent):
        return tk.Frame(parent, bg=LINE, height=1)

    @staticmethod
    def _tags(row, kind):
        return (kind, "alt") if int(row.rsplit("_", 1)[1]) % 2 else (kind,)

    def _build(self):
        page = ttk.Frame(self.root, padding=(12, 10, 12, 10))
        page.pack(fill="both", expand=True)

        top = ttk.Frame(page)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="照片目录").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(top, textvariable=self.directory).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="选择…", width=9, command=self.choose_directory).grid(row=0, column=2)

        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(10, 0))
        self.scan_button = ttk.Button(actions, text="扫描", width=10, command=self.scan)
        self.scan_button.pack(side="left")
        self.process_button = ttk.Button(
            actions, text="开始处理", width=12, style="Accent.TButton", command=self.process
        )
        self.process_button.pack(side="left", padx=6)

        self._line(page).pack(fill="x", pady=(10, 0))

        table_top = ttk.Frame(page)
        table_top.pack(fill="x", pady=(8, 6))
        ttk.Label(table_top, text="文件列表", style="Head.TLabel").pack(side="left")
        ttk.Label(table_top, textvariable=self.count_text, style="Muted.TLabel").pack(side="right")

        table = ttk.Frame(page)
        table.pack(fill="both", expand=True)
        columns = ("file", "model", "lens", "state", "note")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        headings = {
            "file": "文件", "model": "Model", "lens": "LensModel",
            "state": "状态", "note": "说明",
        }
        widths = {"file": 190, "model": 70, "lens": 145, "state": 75, "note": 230}
        for column in columns:
            self.tree.heading(column, text=headings[column], anchor="w")
            self.tree.column(
                column, width=widths[column], minwidth=60, anchor="w",
                stretch=column in ("file", "note"),
            )
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.tag_configure("pending", foreground="#1d5b9e")
        self.tree.tag_configure("ok", foreground="#217346")
        self.tree.tag_configure("fail", foreground="#b42318")
        self.tree.tag_configure("skip", foreground="#6b7280")
        self.tree.tag_configure("alt", background=ROW_ALT)

        self._line(page).pack(fill="x", pady=(8, 0))
        bottom = ttk.Frame(page)
        bottom.pack(fill="x", pady=(6, 0))
        ttk.Label(bottom, textvariable=self.status_text, style="Muted.TLabel").pack(side="left")

        tool_status = ttk.Frame(bottom)
        tool_status.pack(side="right")
        self.tool_mark = tk.Canvas(
            tool_status, width=10, height=10, bg=BG, highlightthickness=0, bd=0
        )
        self.tool_mark.pack(side="left", padx=(0, 6))
        self.tool_label = ttk.Label(tool_status, text="", style="Muted.TLabel")
        self.tool_label.pack(side="left")

    def run(self):
        self.root.mainloop()

    def _close(self):
        self.stop_event.set()
        self.running = False
        self.root.destroy()

    def _poll(self):
        if not self.running:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.set_status(payload)
                elif kind == "row_add":
                    self.add_row(*payload)
                elif kind == "row_set":
                    self.set_row(*payload)
                elif kind == "alert":
                    self.alert(*payload)
                elif kind == "scan_done":
                    self._finish_scan(*payload)
                elif kind == "process_done":
                    self._finish_process(*payload)
        except queue.Empty:
            pass
        if self.running:
            self.root.after(60, self._poll)

    def alert(self, title, message):
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        def close():
            dialog.grab_release()
            dialog.destroy()

        frame = ttk.Frame(dialog, padding=(16, 14))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=message, wraplength=360, justify="left").pack(anchor="w")
        button = ttk.Button(frame, text="确定", width=10, command=close)
        button.pack(anchor="e", pady=(18, 0))
        dialog.bind("<Return>", lambda event: close())
        dialog.protocol("WM_DELETE_WINDOW", close)

        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.deiconify()
        button.focus_set()
        dialog.grab_set()

    def choose_directory(self):
        selected = filedialog.askdirectory()
        if selected:
            self.directory.set(selected)

    def set_status(self, text):
        self.status_text.set(text)
        self.root.update_idletasks()

    def set_counts(self, first, skipped, last, first_label="待处理", last_label="读取失败"):
        self.count_text.set(f"{first_label} {first} · 跳过 {skipped} · {last_label} {last}")

    def add_row(self, path, values, kind):
        row = f"row_{len(self.row_by_path)}"
        self.row_by_path[str(path)] = row
        self.tree.insert("", "end", iid=row, values=values, tags=self._tags(row, kind))

    def set_row(self, path, values, kind):
        row = self.row_by_path.get(str(path))
        if row:
            self.tree.item(row, values=values, tags=self._tags(row, kind))
            self.tree.see(row)

    def set_buttons(self, busy):
        self.busy = busy
        self.scan_button.configure(state="disabled" if busy else "normal")
        self.process_button.configure(
            state="disabled" if busy or not self.pending else "normal"
        )

    def set_exiftool_status(self, connected=None):
        if connected is None:
            connected = EXIFTOOL.is_file()
        self.tool_mark.delete("all")
        color = MARK_OK if connected else MARK_BAD
        self.tool_mark.create_rectangle(1, 1, 9, 9, fill=color, outline=color)
        self.tool_label.configure(
            text="exiftool已连接" if connected else "未检测到exiftool"
        )

    def check_exiftool(self):
        connected = EXIFTOOL.is_file()
        self.set_exiftool_status(connected)
        if connected:
            return True
        self.alert(
            "缺少 ExifTool",
            f"请把本程序放在 ExifTool 根目录。\n\n未找到：{EXIFTOOL}",
        )
        return False

    def valid_directory(self):
        text = self.directory.get().strip()
        path = Path(text) if text else None
        if path is None or not path.is_dir():
            self.alert("目录无效", "请选择照片目录。")
            return None
        if not self.check_exiftool():
            return None
        return path.resolve()

    def scan(self):
        if self.busy:
            return
        root = self.valid_directory()
        if root is None:
            return
        self.scan_root = root
        self.tree.delete(*self.tree.get_children())
        self.row_by_path.clear()
        self.pending.clear()
        self.set_buttons(True)
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._scan_worker, args=(root,), daemon=True)
        self.worker.start()

    def _scan_worker(self, root):
        pending_paths = []
        skipped = 0
        read_failed = 0
        total = 0
        try:
            raw_files = sorted(
                (
                    path for path in root.iterdir()
                    if path.is_file() and path.suffix.lower() == RAW_EXTENSION
                ),
                key=lambda path: path.name.lower(),
            )
            total = len(raw_files)
            for number, path in enumerate(raw_files, 1):
                if self.stop_event.is_set():
                    break
                name = path.name
                try:
                    model, lens = read_tags(path)
                    should_fix, note = check_model_and_lens(model, lens)
                    values = (
                        name, model or "—", lens or "无",
                        note if should_fix else "跳过",
                        "等待处理" if should_fix else note,
                    )
                    if should_fix:
                        pending_paths.append(path)
                        self.events.put(("row_add", (path, values, "pending")))
                    else:
                        skipped += 1
                        self.events.put(("row_add", (path, values, "skip")))
                except Exception as exc:
                    read_failed += 1
                    self.events.put(
                        ("row_add", (path, (name, "—", "—", "读取失败", short(exc)), "fail"))
                    )
                self.events.put(("status", f"扫描 {number}/{total}"))
        except Exception as exc:
            self.events.put(("alert", ("扫描失败", short(exc))))
        self.events.put(("scan_done", (total, pending_paths, skipped, read_failed)))

    def _finish_scan(self, total, pending_paths, skipped, read_failed):
        self.pending = pending_paths
        self.set_buttons(False)
        self.set_counts(len(pending_paths), skipped, read_failed)
        self.status_text.set(
            f"扫描完成：共 {total} 个 ARW 文件，待处理 {len(pending_paths)}，"
            f"跳过 {skipped}，读取失败 {read_failed}。"
        )

    def process(self):
        if self.busy or not self.pending or self.scan_root is None:
            return
        if not self.check_exiftool():
            return
        root = self.scan_root
        paths = list(self.pending)
        self.pending.clear()
        self.set_buttons(True)
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._process_worker, args=(root, paths), daemon=True)
        self.worker.start()

    def _process_worker(self, root, paths):
        success = 0
        failed = 0
        skipped = 0
        reasons = {}
        total = len(paths)
        try:
            for number, source in enumerate(paths, 1):
                if self.stop_event.is_set():
                    break
                name = source.name
                output = output_name(source, root)
                try:
                    try:
                        model, lens = read_tags(source)
                    except Exception as exc:
                        raise StepError("读取失败", short(exc)) from exc
                    should_fix, note = check_model_and_lens(model, lens)
                    if not should_fix:
                        skipped += 1
                        self.events.put(
                            ("row_set", (source, (name, model or "—", lens or "无", "跳过", f"{note}，未复制"), "skip"))
                        )
                    else:
                        make_fixed_copy(source, output)
                        success += 1
                        self.events.put(
                            ("row_set", (source, (name, model, LENS, "成功", f"{FIXED_DIR}/{output.name}"), "ok"))
                        )
                except StepError as exc:
                    if exc.step == "输出已存在":
                        skipped += 1
                        self.events.put(
                            ("row_set", (source, (name, model, lens or "无", "跳过", short(exc)), "skip"))
                        )
                    else:
                        failed += 1
                        reasons[exc.step] = reasons.get(exc.step, 0) + 1
                        self.events.put(
                            ("row_set", (source, (name, "—", "—", exc.step, short(exc)), "fail"))
                        )
                except Exception as exc:
                    failed += 1
                    reasons["其他失败"] = reasons.get("其他失败", 0) + 1
                    self.events.put(
                        ("row_set", (source, (name, "—", "—", "其他失败", short(exc)), "fail"))
                    )
                self.events.put(("status", f"处理 {number}/{total}"))
        except Exception as exc:
            self.events.put(("alert", ("处理失败", short(exc))))
        self.events.put(("process_done", (success, skipped, failed, reasons)))

    def _finish_process(self, success, skipped, failed, reasons):
        self.set_buttons(False)
        self.set_counts(success, skipped, failed, "成功", "失败")
        detail = "、".join(f"{step} {count}" for step, count in reasons.items())
        self.status_text.set(
            f"处理完成：成功 {success}，失败 {failed}"
            + (f"（{detail}）" if detail else "")
            + f"，跳过 {skipped}。源文件未修改。"
        )


if __name__ == "__main__":
    App().run()
