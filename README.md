# DICOM Downloader

This tool downloads DICOM files for patients listed in `data/**/response.json` files.

## Features
- **Resume Capability**: Skips already downloaded files (checks for `.done` marker).
- **Multithreading**: Downloads multiple files in parallel.
- **Structured Output**: Organizes files into `data/{patient}/study_{i}/series_{j}/`.
- **Cross-Platform**: Works on Linux and Windows.

## Usage

1. Ensure you have Python installed.
2. Ensure the `requests` library is installed:
   ```bash
   pip install requests
   ```
3. Run the script:
   ```bash
   python main.py
   ```
4. Follow the on-screen prompts to enter:
   - `ASP.NET_SessionId`: The session cookie value.
   - `strSession`: The session ID used in the query parameters.
   - Number of threads (default is 4).

## Directory Structure
The script expects a `data` folder in the current directory containing subfolders with `response.json` files.
Downloaded files will be placed next to their `response.json` in `study_X/series_Y/` folders.
