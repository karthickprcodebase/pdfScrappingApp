# PDF Scraper

PDF Scraper is a local web application for finding PDF links on a single web page. You enter a URL, the app opens that page in Chromium, and it lists the PDF links it can identify. This version does not download files or read PDF contents.

The interface is a Streamlit application that runs on your computer. There is no separate backend service.

## Required Python version

Python 3.12 or newer.

## Create a virtual environment

From the project folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## Install dependencies

```powershell
pip install -r requirements.txt
```

## Install Playwright Chromium

Playwright needs its own Chromium browser. This is separate from the Python packages:

```powershell
python -m playwright install chromium
```

## Run the application

```powershell
streamlit run app.py
```

Streamlit serves the app at http://127.0.0.1:8501 and opens it in your browser. The server listens on localhost only.

To run the tests:

```powershell
python -m pytest
```

Logs are written to `data/logs/pdf_scraper.log`. Discovered PDF links are stored in `data/pdf_scraper.db`.

## Project structure

```text
app.py                  Streamlit UI
config.py               Paths, timeouts, and browser defaults
requirements.txt
scraper/
  website_scanner.py    Opens one page with Playwright and reads anchors
  pdf_detector.py       Decides which links point to PDFs
models/
  pdf_item.py           PDF link record
services/
  scanner_service.py    Validation, scanning, and saving
database/
  database.py           SQLite setup and discovered-PDF table
utils/
  url_utils.py          URL checks, absolute links, and filenames
  logger.py             Application logging
data/                   Local database, logs, and future downloads
tests/                  Unit tests that do not contact a website
```

## Current V1 limitations

- Only the page you submit is scanned. The app does not crawl the rest of the site.
- PDF files are listed only. Nothing is downloaded, and PDF text is not extracted.
- The results table has a Select column for a later download step. Selecting a row does not download it.
- Likely PDF links that do not end in `.pdf` may be checked with a small number of HEAD requests. The app does not download those files to inspect them.
- Pages that show a PDF only after a login, CAPTCHA, payment, or extra click are not bypassed.
- The app is not yet packaged as a Windows executable. Running it in development requires Python and the Playwright Chromium install above.

## Future features

- Download the selected PDFs into `data/downloads`.
- Record download status, file location, timestamps, and errors in SQLite.
- Extract PDF text and metadata with PyMuPDF.
- Package the app with PyInstaller so a Windows user can run one `.exe` without installing Python, Node.js, pip, Streamlit, or Playwright.
