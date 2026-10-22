# PDF Scraper

PDF Scraper is a local desktop-style web application for finding PDF links on a website. You enter one page address, choose a discovery mode, and the app lists the PDF candidates it finds.

The app can find PDFs from:

- links in the rendered page
- PDF URLs inside JSON API responses the page loads
- network responses whose type or file name identifies a PDF
- browser download events that name a PDF

It stores those candidates in a local SQLite database. It does not download the PDF files or read their contents. A URL in the results list is not a downloaded file.

The interface is Streamlit. There is no separate backend. A later Windows package is planned so the end user can run the app without installing Python or other development tools. During development, Python is still required.

## Python version

Python 3.12 or newer.

## Create a virtual environment

From the `pdf_scraper` folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If `py` is not available, use `python -m venv .venv` with Python 3.12+.

## Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Install Playwright Chromium

The scanner opens pages with Chromium. Install that browser once per environment:

```powershell
python -m playwright install chromium
```

## Run the application

From the `pdf_scraper` folder, with the virtual environment active:

```powershell
python -m streamlit run app.py
```

Streamlit opens a local page in your browser. Enter a website URL, choose a discovery mode, and click **Scan Website**.

Logs are written to `data/logs/pdf_scraper.log`. The SQLite database is `data/pdf_scraper.db`. The download folder `data/downloads` is created for a later phase.

Run the tests from the same folder:

```powershell
python -m unittest discover -s tests -t . -v
```

The tests use local fixtures and a local HTTP server. They do not call an external website.

## Discovery modes

**HTML Links** opens the page and reads PDF links from anchors, iframes, embeds, and object tags after JavaScript has rendered the page. This is the original scan.

**HTML + API Discovery** does the same HTML scan and also watches network traffic while that page loads. It records direct PDF responses and PDF URLs found in JSON responses. Listeners are attached before navigation so early requests are included.

**Interactive Discovery** opens a separate Chromium window. Use that window to paginate, filter, scroll, or open a document list. The app keeps watching the session. Click **Finish interactive scan** when you are done. Streamlit cannot reliably embed that browser, so the window is separate. The app does not click buttons for you.

## How network monitoring works

During HTML + API Discovery and Interactive Discovery, Playwright reports each response the page receives. A response is kept when:

- its Content-Type is `application/pdf` or `application/x-pdf` and the HTTP status is successful
- the URL or Content-Disposition file name ends in `.pdf`, and the body is not an HTML page
- a small body is available and begins with `%PDF-`
- a JSON response contains a PDF URL or a document field such as `pdfUrl`, `fileUrl`, or `downloadUrl`

JSON is read only up to the configured size limit. Large PDF bodies are not copied into memory; those candidates are recorded from the response headers. HTTP errors are counted as failed inspections and are not listed as PDFs.

The word "pdf" in a normal page URL is not enough. An HTML page is not treated as a PDF.

## Run a scan

1. Start the app with `python -m streamlit run app.py`.
2. Choose a discovery mode.
3. Enter an `http://` or `https://` address.
4. Click **Scan Website**.
5. For Interactive Discovery, use the Chromium window, then click **Finish interactive scan**.

Filter the table by discovery source or discovery status. The summary shows HTML, API, direct PDF response, and browser-download counts, plus duplicates removed and failed inspections.

## Project structure

```text
pdf_scraper/
├── app.py                         Streamlit interface
├── config.py                      Paths, timeouts, and limits
├── requirements.txt
├── README.md
├── scraper/
│   ├── website_scanner.py         Opens the page and merges discoveries
│   ├── pdf_detector.py            HTML link extraction
│   ├── network_monitor.py         Response, JSON, and download events
│   ├── json_pdf_parser.py         Nested JSON URL extraction
│   ├── response_assessment.py     PDF response checks
│   └── dedup.py                   Safe URL merging
├── models/
│   └── pdf_item.py                Candidate model and statuses
├── services/
│   ├── scanner_service.py         Validation, scanning, and saving
│   └── download_service.py        PDF header and SHA-256 helpers
├── database/
│   └── database.py                SQLite table and column migration
├── utils/
│   ├── url_utils.py               URL checks, resolution, log redaction
│   ├── url_guard.py               Private-network and metadata blocking
│   └── logger.py                  Log setup
├── data/                          Local database, logs, and future downloads
└── tests/
```

## Security

Only `http` and `https` addresses can be scanned. Localhost, private IP ranges, link-local addresses, and cloud metadata hostnames are blocked unless you explicitly opt in for a trusted local test:

```powershell
$env:PDF_SCRAPER_ALLOW_PRIVATE_NETWORKS = "1"
```

Leave that variable unset for normal use. The scanner checks the address you enter and the address after a redirect. Logs remove passwords and query values whose names look like signatures, tokens, or credentials. Cookies and authorization headers are not written to the log.

The app does not log in, solve CAPTCHAs, ignore certificate errors, or walk the rest of the website. Pagination follows the next page of the same results. It does not open ordinary links in menus, articles, or footers.

## Pagination

Pagination scanning is on by default in the app and off when code calls `scan()` without options. Open **Pagination settings** on the scan form to change the limits. The scan always stops on a page limit, a time limit, pages with no new PDFs, a repeated address or cursor, or **Stop scanning**.

The scanner can move forward in these ways:

- HTML page links, including `rel="next"`, a Next or Load More link, and numbered links inside a pagination control
- one visible Next or Load More button, when the page does not already expose a next URL
- an API next URL, cursor, page number, or offset taken from a response the page already loaded
- optional infinite scrolling, which is off until you enable it

HTML pages and followed API responses both count toward the maximum of 20 pages. A later page that fails does not cancel PDFs already found or other pages that are still safe to open. Interactive Discovery stays manual and does not click the page for you.

## Current limitations

- With pagination turned off, a scan reads only the page you enter.
- Pagination does not open every link on a site. A next control is followed only when it is identifiable. Several competing Next controls are left alone and reported.
- API URLs are inspected only when the browser session receives that response, or when that response contains enough evidence to request the next page of the same API. The app does not invent endpoints.
- A PDF listed from a JSON payload is still only a link until you download it.
- Interactive Discovery records the pages you open in that window. It does not click, type, or bypass access controls.
- PDFs that appear only after an unrelated button click are found in Interactive Discovery. Automatic scanning clicks only a single clear Next or Load More control.
- Very large or chunked JSON bodies above the size limit are skipped.
- A direct PDF response is header-validated when the body is too large to read safely. Magic-byte validation runs for smaller responses.
- Browser download events are recorded and cancelled. The file is not saved, and the row stays "Not downloaded".
- SHA-256 grouping is available for files that already exist locally. Discovery does not download a file in order to hash it.
- Two different signed URLs stay as two rows until a later download shows they are the same bytes.

## Troubleshooting missing API PDFs

- Use **HTML + API Discovery** or **Interactive Discovery**. HTML Links does not read JSON responses.
- Confirm the page requests the API while the browser is open. An endpoint that runs only after a button click needs Interactive Discovery.
- The JSON value needs to be a PDF URL, or it needs to sit in a document field such as `pdfUrl` or `downloadUrl`. A normal page link in a field named `url` is ignored.
- Responses larger than 2 MB are skipped. The limit is `max_json_bytes` in `config.py`.
- Check `data/logs/pdf_scraper.log` for skipped responses. The log line uses a redacted URL.

## Saving PDFs

The results table can export a CSV of the links. To save the files themselves, select rows and click **Download selected**, or click **Download shown files**. Files are written to `data/downloads`. Each saved file is checked for a PDF header, limited to 50 MB, and recorded with its path and SHA-256 hash. A file that is already in that folder is not downloaded again. Private and metadata addresses stay blocked.

## Future features

- Extract PDF text and metadata with PyMuPDF.
- Package the app with PyInstaller as a Windows `.exe`, including the browser, so a non-developer machine does not need Python, Node.js, npm, pip, Streamlit, or Playwright installed separately.
