# Space Reclaimer

A small, dependency-free disk cleaner for Windows. One Python file, a standard-library
tkinter UI, no installs, no telemetry, nothing runs in the background.

![icon](assets/icon.png)

## What it does

**Clean tab** — scans well-known junk locations and shows how much each one is holding.
Nothing is deleted until you tick the categories you want, press **Clean selected**, and
confirm. Categories include:

- Recycle Bin, user and Windows temp files, Windows Update download cache
- Crash dumps and Windows Error Reporting archives
- Thumbnail and icon cache
- Browser caches (Chrome, Edge, Brave, Firefox)
- pip, npm, and NuGet caches
- Unreal Engine caches: the shared Derived Data Cache, plus per-project
  `DerivedDataCache`, `Intermediate`, and log/crash folders in projects it finds
  under Documents, Desktop, and Downloads

**Big folders tab** — shows what is actually using the space in a folder or drive,
sorted largest first. Double-click to drill in.

**Big files tab** — lists the 200 largest files under a folder. You can reveal one in
Explorer, or send it to the Recycle Bin (never a permanent delete from this tab).

## Safety

- Deletion only ever happens on the Clean tab, only for the categories you ticked,
  and only after a confirmation dialog.
- The cleaner never touches the registry, your documents, or application installs.
- Drive roots and your home folder itself are refused by the delete helper.
- Locked or in-use files are skipped and reported, not forced.
- Run as administrator to also clear `C:\Windows\Temp` and the Windows Update
  download cache.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer (tkinter is included with the standard Windows installer)

No third-party packages are required.

## Run it

```
python space_reclaimer.py
```

or double-click `Space_Reclaimer.bat`, which launches it without a console window.

## Optional: build a standalone .exe

```
pip install pyinstaller
pyinstaller --noconfirm --windowed --name SpaceReclaimer --icon assets\icon.ico --add-data "assets\icon.ico;assets" space_reclaimer.py
```

## License

MIT — see `LICENSE`.
