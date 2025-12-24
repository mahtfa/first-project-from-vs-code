import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import csv
import os
import sys
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import threading
import queue

from aparat import Aparat


def get_base_dir():
    """
    مسیر کناریِ برنامه (کنار exe یا فایل .py).
    """
    if getattr(sys, "frozen", False):  # PyInstaller
        return os.path.dirname(sys.executable)
    # اسکریپت پایتون
    return os.path.dirname(os.path.abspath(__file__))


def setup_logger(log_path: str):
    logger = logging.getLogger("aparat_uploader")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fmt = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


class AparatUploaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Aparat Video Uploader")
        self.root.geometry("720x650")

        # مسیر خروجی‌ها کنار برنامه
        self.base_dir = get_base_dir()
        self.log_path = os.path.join(self.base_dir, "app.log")
        self.csv_path = os.path.join(self.base_dir, "upload_results.csv")

        self.logger = setup_logger(self.log_path)
        self.logger.info("UI started (base_dir=%s)", self.base_dir)

        self.aparat = Aparat()
        self.video_files = []
        self.worker_thread = None
        self.msg_q = queue.Queue()
        self.is_uploading = False

        # شناسه اجرا
        self.current_run_id = None

        # --- UI ---
        frm = tk.Frame(root)
        frm.pack(fill="x", padx=10, pady=10)

        tk.Label(frm, text="Username:").grid(row=0, column=0, sticky="w")
        self.username_entry = tk.Entry(frm, width=32)
        self.username_entry.grid(row=0, column=1, sticky="w", padx=(6, 0))

        tk.Label(frm, text="Password:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.password_entry = tk.Entry(frm, show="*", width=32)
        self.password_entry.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(4, 0))

        # فیلد Tags
        tk.Label(frm, text="Tags (comma-separated):").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.tags_entry = tk.Entry(frm, width=50)
        # پیش‌فرض: اگر کاربر چیزی نداد، همین می‌ماند
        self.tags_entry.insert(0, "auto-upload")
        self.tags_entry.grid(row=2, column=1, sticky="we", padx=(6, 0), pady=(8, 0))

        frm.grid_columnconfigure(1, weight=1)

        btns = tk.Frame(root)
        btns.pack(fill="x", padx=10, pady=(6, 0))
        self.btn_browse = tk.Button(btns, text="Browse File", command=self.browse_file)
        self.btn_browse.pack(side="left", padx=(0, 6))
        self.btn_folder = tk.Button(btns, text="Select Folder", command=self.select_folder)
        self.btn_folder.pack(side="left", padx=(0, 6))
        self.btn_upload = tk.Button(btns, text="Upload", command=self.start_upload)
        self.btn_upload.pack(side="left", padx=(0, 6))
        self.btn_retry_failed = tk.Button(btns, text="Retry Failed", command=self.open_retry_selector)
        self.btn_retry_failed.pack(side="left")

        # نمایش مسیر فایل‌های خروجی
        paths_frame = tk.Frame(root)
        paths_frame.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(paths_frame, text=f"Log: {self.log_path}").pack(anchor="w")
        tk.Label(paths_frame, text=f"CSV: {self.csv_path}").pack(anchor="w")

        # Progress
        pgf = tk.Frame(root)
        pgf.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(pgf, text="Progress:").pack(anchor="w")
        self.progress = ttk.Progressbar(pgf, orient="horizontal", mode="determinate", length=680)
        self.progress.pack(fill="x")
        self.progress_label = tk.Label(pgf, text="Idle")
        self.progress_label.pack(anchor="w")

        # Log box
        self.log_box = tk.Text(root, height=20)
        self.log_box.pack(fill="both", expand=True, padx=10, pady=10)

        # Poll queue for UI updates
        self.root.after(100, self._poll_queue)

    # --------------- Helpers ---------------
    @staticmethod
    def _normalize_upload_result(result):
        """
        خروجی Aparat.uploadPost را نرمال می‌کند.
        اگر hash/url قابل برداشت نبود، None برمی‌گرداند.
        """
        if result is None:
            return None

        # dict
        if isinstance(result, dict):
            h = result.get("hash") or result.get("video_hash") or result.get("file_hash") or result.get("uid")
            u = result.get("url") or result.get("video_url") or result.get("link")
            if h or u:
                return {"hash": h or "", "url": u or ""}
            return None

        # object-like
        h = getattr(result, "uid", None) or getattr(result, "hash", None) \
            or getattr(result, "video_hash", None) or getattr(result, "file_hash", None)
        u = getattr(result, "url", None) or getattr(result, "video_url", None) or getattr(result, "link", None)
        if h or u:
            return {"hash": h or "", "url": u or ""}
        return None

    @staticmethod
    def _parse_tags(text: str):
        """
        تگ‌ها را از ورودی کاربر می‌خواند. جداکننده: ','.
        """
        if not text:
            return []
        parts = [t.strip() for t in text.split(",")]
        return [p for p in parts if p]

    def _make_video_url(self, videohash: str) -> str:
        if not videohash:
            return ""
        return f"https://www.aparat.com/v/{videohash}"

    # --------------- UI callbacks ---------------
    def browse_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4")])
        if file_path:
            self.video_files.append(file_path)
            self._append_log(f"Added file: {file_path}")
            self.logger.info("Added file: %s", file_path)

    def select_folder(self):
        folder_path = filedialog.askdirectory()
        if folder_path:
            added = 0
            for file in os.listdir(folder_path):
                if file.lower().endswith(".mp4"):
                    full_path = os.path.join(folder_path, file)
                    self.video_files.append(full_path)
                    self._append_log(f"Added file: {full_path}")
                    added += 1
            self.logger.info("Selected folder: %s (added %d .mp4 files)", folder_path, added)

    def start_upload(self):
        if self.is_uploading:
            return

        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        tags_text = self.tags_entry.get().strip()
        tags_list = self._parse_tags(tags_text)

        if not username or not password:
            messagebox.showwarning("Missing Info", "Enter username and password!")
            return
        if not self.video_files:
            messagebox.showwarning("No Files", "No video files selected!")
            return

        # شناسه اجرای تازه
        self.current_run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

        # Lock UI
        self._set_ui_state(False)
        self.is_uploading = True
        self.progress_label.config(text=f"Starting upload... (RunID: {self.current_run_id})")
        self.progress.config(value=0, maximum=max(1, len(self.video_files)))

        # Launch worker thread
        self.worker_thread = threading.Thread(
            target=self._upload_worker,
            args=(self.current_run_id, username, password, list(self.video_files), tags_list),
            daemon=True
        )
        self.worker_thread.start()

    # --------------- Background worker (no direct UI access here) ---------------
    def _upload_worker(self, run_id, username, password, files, tags_list):
        self.logger.info("Attempting login for user: %s (RunID=%s)", username, run_id)
        failed_files = []

        try:
            user = self.aparat.login(username, password)
            if not user:
                self._q_err("Login failed!")
                self.logger.error("Login failed for user: %s", username)
                self._q_done(False, failed_files)
                return
            self.logger.info("Login successful for user: %s", username)
            form = self.aparat.uploadForm(user.username, user.ltoken)
            self.logger.info("Fetched upload form")
        except Exception as e:
            self._q_err(f"Login/form error: {e}")
            self.logger.exception("Login/form error")
            self._q_done(False, failed_files)
            return

        # CSV کنار برنامه
        csv_file = self.csv_path
        write_header = not os.path.exists(csv_file)
        headers = ["RunID", "Timestamp", "Filename", "Status", "Hash", "URL", "Error"]

        try:
            with open(csv_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if write_header:
                    writer.writeheader()

                processed = 0
                total = len(files)

                for video in files:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        base_title = os.path.splitext(os.path.basename(video))[0]
                        self.logger.info("Uploading started: %s", video)
                        self._q_info(f"Uploading: {video}")

                        raw_result = self.aparat.uploadPost(
                            form=form,
                            video_path=video,
                            title=base_title,
                            category=3,
                            tags=tags_list if tags_list else ["english,alavi,school"],
                            allow_comment=True,
                            descreption="",
                            video_pass=False
                        )

                        self.logger.info("Raw uploadPost result (truncated): %r", repr(raw_result)[:500])

                        norm = self._normalize_upload_result(raw_result)
                        if not norm:
                            uid_guess = getattr(raw_result, "uid", None) if raw_result is not None else None
                            if uid_guess:
                                url_guess = self._make_video_url(uid_guess)
                                norm = {"hash": uid_guess, "url": url_guess}
                            else:
                                raise ValueError("Upload returned no usable result (hash/url missing)")

                        vid_hash = norm["hash"]
                        vid_url = norm["url"] or self._make_video_url(norm["hash"])

                        writer.writerow({
                            "RunID": run_id,
                            "Timestamp": ts,
                            "Filename": video,
                            "Status": "Success",
                            "Hash": vid_hash,
                            "URL": vid_url,
                            "Error": ""
                        })

                        self.logger.info("SUCCESS: %s | hash=%s | url=%s", video, vid_hash, vid_url)
                        self._q_info(f"Uploaded: {video}\n{vid_url or '(no url provided)'}\n")

                    except Exception as e:
                        err_msg = str(e)
                        failed_files.append(video)
                        writer.writerow({
                            "RunID": run_id,
                            "Timestamp": ts,
                            "Filename": video,
                            "Status": "Failed",
                            "Hash": "",
                            "URL": "",
                            "Error": err_msg
                        })
                        self.logger.exception("FAILED: %s | %s", video, err_msg)
                        self._q_info(f"Failed: {video} | Error: {err_msg}")

                    finally:
                        processed += 1
                        self._q_progress(processed, total)

        except Exception as outer_e:
            self.logger.exception("Writing to CSV failed: %s", str(outer_e))
            self._q_err(f"Writing CSV failed: {outer_e}")
            self._q_done(False, failed_files)
            return

        self._q_info("All uploads processed.")
        self._q_done(True, failed_files)

    # --------------- Retry selector ---------------
    def open_retry_selector(self):
        csv_file = self.csv_path
        if not os.path.exists(csv_file):
            messagebox.showinfo("Retry Failed", "No CSV log found yet.")
            return

        last_run_id, failed_files = self._collect_failed_from_csv(csv_file)
        if not failed_files:
            messagebox.showinfo("Retry Failed", "No failed files found in the last run.")
            return

        win = tk.Toplevel(self.root)
        win.title(f"Retry Failed (RunID: {last_run_id or 'unknown'})")
        win.geometry("760x460")

        tk.Label(win, text="Select the files you want to retry:").pack(anchor="w", padx=10, pady=6)

        lb = tk.Listbox(win, selectmode="extended")
        lb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        for path in failed_files:
            suffix = "" if os.path.exists(path) else "  [MISSING]"
            lb.insert(tk.END, path + suffix)

        def do_retry_selected():
            sel = lb.curselection()
            selected = [failed_files[i] for i in sel]
            if not selected:
                messagebox.showwarning("Retry Failed", "No files selected.")
                return
            self.video_files = selected
            win.destroy()
            self.start_upload()

        btns = tk.Frame(win)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(btns, text="Retry Selected", command=do_retry_selected).pack(side="left")
        tk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")

    def _collect_failed_from_csv(self, csv_file):
        """
        آخرین RunID را پیدا می‌کند (اگر ستون RunID باشد) و فایل‌های Failed آن اجرا را برمی‌گرداند.
        اگر RunID نبود، همه‌ی ردیف‌های Failed را برمی‌گرداند (fallback).
        """
        failed_by_run = {}
        run_order = []

        try:
            with open(csv_file, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                has_runid = "RunID" in fieldnames

                if has_runid:
                    for row in reader:
                        rid = row.get("RunID", "") or ""
                        status = (row.get("Status", "") or "").strip().lower()
                        fname = row.get("Filename", "") or ""
                        if rid not in failed_by_run:
                            failed_by_run[rid] = []
                            run_order.append(rid)
                        if status == "failed" and fname:
                            failed_by_run[rid].append(fname)
                    last_run_id = run_order[-1] if run_order else None
                    return last_run_id, failed_by_run.get(last_run_id, [])
                else:
                    # بدون RunID: همه‌ی Failها
                    failed_all = []
                    for row in reader:
                        status = (row.get("Status", "") or "").strip().lower()
                        fname = row.get("Filename", "") or ""
                        if status == "failed" and fname:
                            failed_all.append(fname)
                    # یکتاسازی با حفظ ترتیب
                    seen = set()
                    uniq = []
                    for p in failed_all:
                        if p not in seen:
                            uniq.append(p)
                            seen.add(p)
                    return None, uniq

        except Exception as e:
            self.logger.exception("Failed to parse CSV for retry: %s", e)
            messagebox.showerror("Retry Failed", f"Could not parse CSV: {e}")
            return None, []

    # --------------- Queue helpers (worker -> UI) ---------------
    def _q_info(self, msg: str):
        self.msg_q.put({"type": "log", "level": "info", "msg": msg})

    def _q_err(self, msg: str):
        self.msg_q.put({"type": "log", "level": "error", "msg": msg})

    def _q_progress(self, count, total):
        self.msg_q.put({"type": "progress", "count": count, "total": total})

    def _q_done(self, ok: bool, failed_files):
        self.msg_q.put({"type": "done", "ok": ok, "failed": failed_files})

    # --------------- UI side: poll queue ---------------
    def _poll_queue(self):
        try:
            while True:
                item = self.msg_q.get_nowait()
                typ = item.get("type")
                if typ == "log":
                    self._append_log(item["msg"])
                    if item.get("level") == "error":
                        self.progress_label.config(text="Error occurred (see log)")
                elif typ == "progress":
                    count = item["count"]
                    total = item["total"]
                    self.progress.config(maximum=total, value=count)
                    self.progress_label.config(text=f"{count}/{total} uploaded")
                elif typ == "done":
                    ok = item["ok"]
                    failed_files = item.get("failed", [])
                    self.is_uploading = False
                    self._set_ui_state(True)
                    suffix = "" if ok and not failed_files else f" | Failed: {len(failed_files)}"
                    self.progress_label.config(text=("Completed" if ok else "Completed with errors") + suffix)
                    if ok and not failed_files:
                        messagebox.showinfo("Done", "Upload completed!")
                    else:
                        messagebox.showwarning(
                            "Done",
                            f"Upload finished. Failed files: {len(failed_files)}.\n"
                            f"You can click 'Retry Failed' to try them again."
                        )
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_queue)

    # --------------- small helpers ---------------
    def _set_ui_state(self, enabled: bool):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.btn_browse.config(state=state)
        self.btn_folder.config(state=state)
        self.btn_upload.config(state=state)
        self.username_entry.config(state=state)
        self.password_entry.config(state=state)
        self.tags_entry.config(state=state)
        self.btn_retry_failed.config(state=(tk.NORMAL if enabled else tk.DISABLED))

    def _append_log(self, text):
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)


if __name__ == "__main__":
    root = tk.Tk()
    app = AparatUploaderGUI(root)
    root.mainloop()
