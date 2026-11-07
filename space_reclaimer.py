"""Space Reclaimer - a small disk cleaner for Windows (Python + tkinter, no dependencies).

Tab 1 "Clean":      scans well-known junk locations, shows sizes, deletes only what you tick and confirm.
Tab 2 "Big folders": shows what is using the space on a drive/folder, drill down by double-click.
Tab 3 "Big files":  the largest files under a folder.

Safety: nothing is deleted until you press Clean and confirm. Only the known junk locations below are ever
touched (never the registry, never your documents). Locked/in-use files are skipped and reported.
Run as administrator to also clean C:\\Windows\\Temp and the Windows Update download cache.
"""
import ctypes
import os
import shutil
import stat
import string
import threading
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
WINDIR = Path(os.environ.get("WINDIR", "C:/Windows"))

__version__ = "1.0.0"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.ico"


def fmt(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# ------------------------------------------------------------------ filesystem helpers
def dir_size(path, stop=None):
    """Total size of files under path. Does not follow symlinks/junctions."""
    total = 0
    stack = [str(path)]
    while stack:
        if stop and stop.is_set():
            return total
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _on_rm_error(func, p, _exc):
    try:
        os.chmod(p, stat.S_IWRITE)
        func(p)
    except OSError:
        pass


def delete_contents(root, delete_root=False):
    """Deletes everything inside root (or root itself). Returns (bytes_freed, failed_count)."""
    root = Path(root)
    if len(root.parts) < 3 or root == HOME:  # refuse drive roots, C:\\Users, the home folder itself
        return 0, 0
    freed = failed = 0
    if not root.exists():
        return 0, 0
    targets = [root] if delete_root else list(root.iterdir())
    for t in targets:
        try:
            if t.is_symlink():
                t.unlink()
                continue
            size = dir_size(t) if t.is_dir() else t.stat().st_size
            if t.is_dir():
                shutil.rmtree(t, onerror=_on_rm_error)
                if t.exists():
                    # partially deleted: count what is gone
                    size -= dir_size(t)
                    failed += 1
            else:
                t.unlink()
            freed += size
        except OSError:
            failed += 1
    return freed, failed


# ------------------------------------------------------------------ recycle bin (Win32)
class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]


def recycle_bin_size():
    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    try:
        ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    except Exception:
        return 0
    return info.i64Size


def empty_recycle_bin():
    before = recycle_bin_size()
    # flags: no confirmation, no progress UI, no sound
    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
    return max(before - recycle_bin_size(), 0), 0


# ------------------------------------------------------------------ categories
def find_unreal_projects():
    roots = [HOME / "Documents", HOME / "Desktop", HOME / "Downloads"]
    found = []
    for r in roots:
        if not r.exists():
            continue
        base_depth = len(r.parts)
        for dirpath, dirs, files in os.walk(r):
            depth = len(Path(dirpath).parts) - base_depth
            dirs[:] = [d for d in dirs if d not in {"node_modules", ".git", "Intermediate", "Binaries", "Saved", "Content",
                                                     "Source", "DerivedDataCache", "Plugins"} and not d.startswith(".")]
            if depth >= 4:
                dirs[:] = []
            if any(f.lower().endswith(".uproject") for f in files):
                found.append(Path(dirpath))
                dirs[:] = []
    return found


class Category:
    def __init__(self, name, note, paths=None, size_fn=None, clean_fn=None, default=True, delete_root=False):
        self.name, self.note, self.paths = name, note, paths or []
        self.size_fn, self.clean_fn, self.default, self.delete_root = size_fn, clean_fn, default, delete_root

    def size(self, stop=None):
        if self.size_fn:
            return self.size_fn()
        return sum(dir_size(p, stop) for p in self.paths if Path(p).exists())

    def clean(self):
        if self.clean_fn:
            return self.clean_fn()
        freed = failed = 0
        for p in self.paths:
            f, x = delete_contents(p, self.delete_root)
            freed, failed = freed + f, failed + x
        return freed, failed


def build_categories(projects):
    chrome = LOCAL / "Google/Chrome/User Data"
    edge = LOCAL / "Microsoft/Edge/User Data"
    brave = LOCAL / "BraveSoftware/Brave-Browser/User Data"

    def browser_caches(user_data):
        out = []
        if user_data.exists():
            for prof in user_data.iterdir():
                for sub in ("Cache", "Code Cache", "GPUCache"):
                    out.append(prof / sub)
                out.append(prof / "Service Worker/CacheStorage")
        return out

    firefox = [p / "cache2" for p in (LOCAL / "Mozilla/Firefox/Profiles").glob("*")]

    ue_paths = []
    for p in projects:
        ue_paths += [p / "DerivedDataCache", p / "Intermediate", p / "Saved/Logs", p / "Saved/Crashes", p / "Saved/ShaderDebugInfo"]
        for sub in ("Plugins",):
            if (p / sub).exists():
                for ip in (p / sub).glob("**/Intermediate"):
                    ue_paths.append(ip)

    cats = [
        Category("Recycle Bin", "Everything in the Recycle Bin (permanent).", size_fn=recycle_bin_size, clean_fn=empty_recycle_bin, default=False),
        Category("User temp files", str(Path(os.environ.get("TEMP", LOCAL / "Temp"))), [Path(os.environ.get("TEMP", LOCAL / "Temp"))]),
        Category("Windows temp files", r"C:\Windows\Temp (needs admin for some files)", [WINDIR / "Temp"]),
        Category("Windows Update download cache", r"SoftwareDistribution\Download (needs admin)", [WINDIR / "SoftwareDistribution/Download"]),
        Category("Crash dumps & error reports", "LocalCrashDumps, Windows Error Reporting", [LOCAL / "CrashDumps", LOCAL / "Microsoft/Windows/WER", Path("C:/ProgramData/Microsoft/Windows/WER")]),
        Category("Thumbnail & icon cache", "Windows rebuilds these automatically", [LOCAL / "Microsoft/Windows/Explorer"]),
        Category("Chrome cache", "Pages reload slower once", browser_caches(chrome)),
        Category("Edge cache", "Pages reload slower once", browser_caches(edge)),
        Category("Brave cache", "Pages reload slower once", browser_caches(brave)),
        Category("Firefox cache", "Pages reload slower once", firefox),
        Category("pip cache", str(LOCAL / "pip/Cache"), [LOCAL / "pip/Cache"]),
        Category("npm cache", str(LOCAL / "npm-cache"), [LOCAL / "npm-cache"]),
        Category("NuGet / dotnet cache", "Re-downloaded on next restore", [HOME / ".nuget/packages", LOCAL / "NuGet/v3-cache"], default=False),
        Category("Unreal shared Derived Data Cache", "Rebuilds the first time assets load (can be slow)", [LOCAL / "UnrealEngine/Common/DerivedDataCache"], default=False),
        Category(f"Unreal projects: Intermediate / DDC / logs ({len(projects)} found)",
                 "Regenerated on next build. Keeps Saved\\SaveGames, Binaries and Content.", ue_paths, default=False),
        Category("Discord / Slack / Teams / VS Code caches", "App caches; apps recreate them",
                 [ROAMING / "discord/Cache", ROAMING / "discord/Code Cache", ROAMING / "Code/Cache", ROAMING / "Code/CachedData",
                  ROAMING / "Slack/Cache", ROAMING / "Slack/Code Cache"]),
    ]
    return cats


# ------------------------------------------------------------------ GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Space Reclaimer")
        self.geometry("980x640")
        try:
            if ICON_PATH.exists():
                self.iconbitmap(default=str(ICON_PATH))
        except tk.TclError:
            pass  # icon is cosmetic; never let it stop the app from opening
        self.stop = threading.Event()
        self.cats = []
        self.checked = {}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.clean_tab, self.folder_tab, self.file_tab = ttk.Frame(nb), ttk.Frame(nb), ttk.Frame(nb)
        nb.add(self.clean_tab, text="  Clean  ")
        nb.add(self.folder_tab, text="  Big folders  ")
        nb.add(self.file_tab, text="  Big files  ")
        self.build_clean()
        self.build_folders()
        self.build_files()
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, anchor="w", relief="sunken").pack(fill="x", side="bottom")
        self.show_drive_space()
        self.scan_clean()

    # ---- shared
    def ui(self, fn, *a):
        self.after(0, fn, *a)

    def show_drive_space(self):
        try:
            t, u, f = shutil.disk_usage("C:\\")
            self.title(f"Space Reclaimer - C: {fmt(f)} free of {fmt(t)}")
        except OSError:
            pass

    # ---- clean tab
    def build_clean(self):
        top = ttk.Frame(self.clean_tab)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="Rescan", command=self.scan_clean).pack(side="left")
        ttk.Button(top, text="Select all", command=lambda: self.set_all(True)).pack(side="left", padx=4)
        ttk.Button(top, text="Select none", command=lambda: self.set_all(False)).pack(side="left")
        self.total_var = tk.StringVar()
        ttk.Label(top, textvariable=self.total_var).pack(side="left", padx=16)
        ttk.Button(top, text="Clean selected", command=self.do_clean).pack(side="right")
        cols = ("size", "note")
        self.tree = ttk.Treeview(self.clean_tab, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Category")
        self.tree.heading("size", text="Size")
        self.tree.heading("note", text="Details")
        self.tree.column("#0", width=380)
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("note", width=460)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<Button-1>", self.toggle)

    def label(self, i):
        return ("\u2611 " if self.checked.get(i) else "\u2610 ") + self.cats[i].name

    def toggle(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            i = int(iid)
            self.checked[i] = not self.checked.get(i)
            self.tree.item(iid, text=self.label(i))
            self.update_total()

    def set_all(self, v):
        for i in range(len(self.cats)):
            self.checked[i] = v
            self.tree.item(str(i), text=self.label(i))
        self.update_total()

    def update_total(self):
        total = sum(self.sizes.get(i, 0) for i, v in self.checked.items() if v)
        self.total_var.set(f"Selected: {fmt(total)}")

    def scan_clean(self):
        self.tree.delete(*self.tree.get_children())
        self.status.set("Scanning...")
        self.sizes = {}
        threading.Thread(target=self._scan_clean, daemon=True).start()

    def _scan_clean(self):
        projects = find_unreal_projects()
        self.cats = build_categories(projects)
        self.checked = {i: c.default for i, c in enumerate(self.cats)}
        for i, c in enumerate(self.cats):
            self.ui(lambda i=i, c=c: self.tree.insert("", "end", iid=str(i), text=self.label(i), values=("...", c.note)))
        for i, c in enumerate(self.cats):
            s = c.size()
            self.sizes[i] = s
            self.ui(lambda i=i, s=s: self.tree.set(str(i), "size", fmt(s)))
        self.ui(self.update_total)
        self.ui(lambda: self.status.set("Scan complete. Tick what you want gone, then Clean selected."))

    def do_clean(self):
        picks = [i for i, v in self.checked.items() if v and self.sizes.get(i, 0) > 0]
        if not picks:
            messagebox.showinfo("Nothing to do", "Nothing selected (or everything selected is already empty).")
            return
        names = "\n".join(f" - {self.cats[i].name} ({fmt(self.sizes[i])})" for i in picks)
        if not messagebox.askyesno("Delete these?", f"This permanently deletes:\n\n{names}\n\nContinue?", icon="warning"):
            return
        self.status.set("Cleaning...")
        threading.Thread(target=self._do_clean, args=(picks,), daemon=True).start()

    def _do_clean(self, picks):
        freed = failed = 0
        for i in picks:
            f, x = self.cats[i].clean()
            freed, failed = freed + f, failed + x
        msg = f"Freed about {fmt(freed)}."
        if failed:
            msg += f" {failed} item(s) were in use or protected and were skipped (try running as administrator)."
        self.ui(lambda: self.status.set(msg))
        self.ui(self.show_drive_space)
        self.ui(lambda: messagebox.showinfo("Done", msg))
        self.ui(self.scan_clean)

    # ---- big folders tab
    def build_folders(self):
        top = ttk.Frame(self.folder_tab)
        top.pack(fill="x", padx=8, pady=6)
        self.folder_path = tk.StringVar(value="C:\\")
        ttk.Entry(top, textvariable=self.folder_path, width=60).pack(side="left")
        ttk.Button(top, text="Browse...", command=lambda: self.folder_path.set(filedialog.askdirectory() or self.folder_path.get())).pack(side="left", padx=4)
        ttk.Button(top, text="Scan", command=self.scan_folders).pack(side="left")
        ttk.Button(top, text="Up", command=self.folder_up).pack(side="left", padx=4)
        ttk.Button(top, text="Open in Explorer", command=lambda: os.startfile(self.folder_path.get())).pack(side="right")
        self.ftree = ttk.Treeview(self.folder_tab, columns=("size", "pct"), show="tree headings")
        self.ftree.heading("#0", text="Folder (double-click to drill in)")
        self.ftree.heading("size", text="Size")
        self.ftree.heading("pct", text="% of folder")
        self.ftree.column("#0", width=520)
        self.ftree.column("size", width=100, anchor="e")
        self.ftree.column("pct", width=90, anchor="e")
        self.ftree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.ftree.bind("<Double-1>", self.drill)
        self.folder_items = {}

    def folder_up(self):
        p = Path(self.folder_path.get())
        if p.parent != p:
            self.folder_path.set(str(p.parent))
            self.scan_folders()

    def drill(self, _e):
        sel = self.ftree.focus()
        if sel in self.folder_items and self.folder_items[sel].is_dir():
            self.folder_path.set(str(self.folder_items[sel]))
            self.scan_folders()

    def scan_folders(self):
        self.stop.set()
        self.stop = threading.Event()
        self.ftree.delete(*self.ftree.get_children())
        self.folder_items = {}
        self.status.set(f"Scanning {self.folder_path.get()} ... (big drives take a few minutes)")
        threading.Thread(target=self._scan_folders, args=(Path(self.folder_path.get()), self.stop), daemon=True).start()

    def _scan_folders(self, root, stop):
        entries = []
        try:
            with os.scandir(root) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        entries.append(Path(e.path))
                    except OSError:
                        pass
        except OSError as ex:
            self.ui(lambda: self.status.set(f"Cannot read {root}: {ex}"))
            return
        sizes = []
        for p in entries:
            if stop.is_set():
                return
            try:
                s = dir_size(p, stop) if p.is_dir() else p.stat().st_size
            except OSError:
                s = 0
            sizes.append((s, p))
            sizes.sort(key=lambda x: -x[0])
            total = sum(x[0] for x in sizes) or 1
            snapshot = list(sizes)
            self.ui(lambda snap=snapshot, t=total: self._render_folders(snap, t, stop))
        self.ui(lambda: self.status.set(f"Scanned {root}: {fmt(sum(x[0] for x in sizes))} total."))

    def _render_folders(self, sizes, total, stop):
        if stop.is_set():
            return
        self.ftree.delete(*self.ftree.get_children())
        self.folder_items = {}
        for n, (s, p) in enumerate(sizes):
            iid = str(n)
            self.folder_items[iid] = p
            self.ftree.insert("", "end", iid=iid, text=("\U0001F4C1 " if p.is_dir() else "\U0001F4C4 ") + p.name, values=(fmt(s), f"{100 * s / total:.1f}%"))

    # ---- big files tab
    def build_files(self):
        top = ttk.Frame(self.file_tab)
        top.pack(fill="x", padx=8, pady=6)
        self.file_path = tk.StringVar(value=str(HOME))
        ttk.Entry(top, textvariable=self.file_path, width=60).pack(side="left")
        ttk.Button(top, text="Browse...", command=lambda: self.file_path.set(filedialog.askdirectory() or self.file_path.get())).pack(side="left", padx=4)
        ttk.Button(top, text="Find 200 largest files", command=self.scan_files).pack(side="left")
        ttk.Button(top, text="Show in Explorer", command=self.reveal).pack(side="right")
        ttk.Button(top, text="Delete selected file to Recycle Bin...", command=self.delete_file).pack(side="right", padx=4)
        self.gtree = ttk.Treeview(self.file_tab, columns=("size",), show="tree headings", selectmode="browse")
        self.gtree.heading("#0", text="File")
        self.gtree.heading("size", text="Size")
        self.gtree.column("#0", width=780)
        self.gtree.column("size", width=100, anchor="e")
        self.gtree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.file_items = {}

    def scan_files(self):
        self.gtree.delete(*self.gtree.get_children())
        self.file_items = {}
        self.status.set("Searching for large files...")
        threading.Thread(target=self._scan_files, args=(Path(self.file_path.get()),), daemon=True).start()

    def _scan_files(self, root):
        found = []
        stack = [str(root)]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            else:
                                found.append((e.stat(follow_symlinks=False).st_size, e.path))
                        except OSError:
                            pass
            except OSError:
                pass
            if len(found) > 400000:
                found.sort(reverse=True)
                found = found[:1000]
        found.sort(reverse=True)
        top = found[:200]
        self.ui(self._render_files, top)

    def _render_files(self, top):
        for n, (s, p) in enumerate(top):
            self.file_items[str(n)] = p
            self.gtree.insert("", "end", iid=str(n), text=p, values=(fmt(s),))
        self.status.set(f"Found {len(top)} largest files.")

    def reveal(self):
        sel = self.gtree.focus()
        if sel in self.file_items:
            os.system(f'explorer /select,"{self.file_items[sel]}"')

    def delete_file(self):
        sel = self.gtree.focus()
        if sel not in self.file_items:
            return
        p = self.file_items[sel]
        if not messagebox.askyesno("Move to Recycle Bin?", f"Move this file to the Recycle Bin?\n\n{p}"):
            return
        # SHFileOperation with FOF_ALLOWUNDO sends it to the Recycle Bin instead of deleting it for good.
        class SHFILEOPSTRUCT(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = SHFILEOPSTRUCT(None, 3, p + "\0", None, 0x40 | 0x10 | 0x4, False, None, None)  # DELETE, ALLOWUNDO|NOCONFIRMATION|SILENT
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0:
            self.gtree.delete(sel)
            self.status.set(f"Moved to Recycle Bin: {p}")
            self.show_drive_space()
        else:
            messagebox.showerror("Failed", "Windows could not move that file (in use or protected).")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
