"""
Daily Google Sheet -> Slack summary poster (formatting-faithful image version).

Reads two ranges from the "Daily Summary" tab of a Google Sheet, along with
their ACTUAL cell formatting (background colors, bold/italic, alignment,
merged cells, column widths, row heights), renders each as an image that
matches how it looks in Google Sheets, and uploads it to a Slack channel
using a Slack Bot Token.

Required environment variables (set as GitHub Actions secrets):
    SHEET_ID                 - the Google Sheet ID (from the sheet URL)
    SLACK_BOT_TOKEN          - Slack bot token, starts with "xoxb-"
    SLACK_CHANNEL_ID         - the Slack channel ID to post into (e.g. C0123ABCDEF)
    GOOGLE_CREDENTIALS_JSON  - the full contents of your service account JSON key
"""

import json
import os
import re
import tempfile

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from PIL import Image, ImageDraw, ImageFont

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SHEET_ID = os.environ["SHEET_ID"]

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]

GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

SHEET_TAB = "Daily Summary"

# Add/remove tables here if the ranges ever change.
TABLES = [
    {
        "range": "A27:R33",
        "title": "Region-wise Summary Update",
        "filename": "region_wise_summary.png",
    },
    {
        "range": "A81:R99",
        "title": "SPOC-wise Summary Update",
        "filename": "spoc_wise_summary.png",
    },
]

# ---------------- Rendering Constants ----------------

if os.name == "nt":  # Windows
    FONT_DIR = r"C:\Windows\Fonts"
    FONT_REGULAR = os.path.join(FONT_DIR, "arial.ttf")
    FONT_BOLD = os.path.join(FONT_DIR, "arialbd.ttf")
    FONT_ITALIC = os.path.join(FONT_DIR, "ariali.ttf")
    FONT_BOLD_ITALIC = os.path.join(FONT_DIR, "arialbi.ttf")
else:  # GitHub/Linux
    FONT_DIR = "/usr/share/fonts/truetype/dejavu"
    FONT_REGULAR = f"{FONT_DIR}/DejaVuSans.ttf"
    FONT_BOLD = f"{FONT_DIR}/DejaVuSans-Bold.ttf"
    FONT_ITALIC = f"{FONT_DIR}/DejaVuSans-Oblique.ttf"
    FONT_BOLD_ITALIC = f"{FONT_DIR}/DejaVuSans-BoldOblique.ttf"

SCALE = 2

DEFAULT_COL_WIDTH = 100
DEFAULT_ROW_HEIGHT = 21

BORDER_COLOR = "#B7B7B7"

MARGIN = 6


def col_letter_to_index(letters):
    result = 0
    for ch in letters.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1  # 0-indexed


def parse_a1_range(a1):
    m = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", a1.strip())
    if not m:
        raise ValueError(f"Could not parse A1 range: {a1}")
    c1, r1, c2, r2 = m.groups()
    return int(r1) - 1, col_letter_to_index(c1), int(r2) - 1, col_letter_to_index(c2)


def get_sheets_service():
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)

    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=SCOPES,
    )

    return build(
        "sheets",
        "v4",
        credentials=creds,
    )


def _color_to_hex(color, default="#FFFFFF"):
    if not color:
        return default
    r = round(color.get("red", 0) * 255)
    g = round(color.get("green", 0) * 255)
    b = round(color.get("blue", 0) * 255)
    return f"#{r:02X}{g:02X}{b:02X}"


def fetch_sheet_grid(service, sheet_tab, a1_range):
    """Fetch cell values + effective formatting + merges + row/col sizes."""
    full_range = f"'{sheet_tab}'!{a1_range}"
    fields = (
        "sheets("
        "data("
        "startRow,startColumn,"
        "rowData(values(formattedValue,effectiveFormat("
        "backgroundColor,horizontalAlignment,verticalAlignment,"
        "textFormat(bold,italic,underline,fontSize,foregroundColor)"
        "))),"
        "rowMetadata(pixelSize),"
        "columnMetadata(pixelSize)"
        "),"
        "merges"
        ")"
    )
    resp = service.spreadsheets().get(
        spreadsheetId=SHEET_ID,
        ranges=[full_range],
        includeGridData=True,
        fields=fields,
    ).execute()

    sheet = resp["sheets"][0]
    data_block = sheet["data"][0]
    start_row = data_block.get("startRow", 0)
    start_col = data_block.get("startColumn", 0)
    row_data_list = data_block.get("rowData", [])
    row_meta = data_block.get("columnMetadata", []) and data_block.get("rowMetadata", [])
    col_meta = data_block.get("columnMetadata", [])

    range_start_row, range_start_col, range_end_row, range_end_col = parse_a1_range(a1_range)
    n_rows = range_end_row - range_start_row + 1
    n_cols = range_end_col - range_start_col + 1

    col_widths = [
        col_meta[i].get("pixelSize", DEFAULT_COL_WIDTH) if i < len(col_meta) else DEFAULT_COL_WIDTH
        for i in range(n_cols)
    ]
    row_heights = [
        data_block.get("rowMetadata", [])[i].get("pixelSize", DEFAULT_ROW_HEIGHT)
        if i < len(data_block.get("rowMetadata", []))
        else DEFAULT_ROW_HEIGHT
        for i in range(n_rows)
    ]

    grid = {}
    for r, row in enumerate(row_data_list):
        values = row.get("values", [])
        for c, cell in enumerate(values):
            if c >= n_cols:
                break
            text = cell.get("formattedValue", "")
            fmt = cell.get("effectiveFormat", {})
            text_format = fmt.get("textFormat", {})
            grid[(r, c)] = {
                "text": text,
                "bg": _color_to_hex(fmt.get("backgroundColor"), default="#FFFFFF"),
                "fg": _color_to_hex(text_format.get("foregroundColor"), default="#000000"),
                "bold": text_format.get("bold", False),
                "italic": text_format.get("italic", False),
                "underline": text_format.get("underline", False),
                "size": text_format.get("fontSize", 10),
                "align": fmt.get("horizontalAlignment", "LEFT"),
            }

    # Merges are returned as absolute sheet coordinates; convert to offsets
    # relative to this range's top-left cell.
    merges = []
    for m in sheet.get("merges", []):
        r0 = m["startRowIndex"] - start_row
        c0 = m["startColumnIndex"] - start_col
        r1 = m["endRowIndex"] - start_row
        c1 = m["endColumnIndex"] - start_col
        if 0 <= r0 < n_rows and 0 <= c0 < n_cols:
            merges.append((r0, c0, min(r1, n_rows), min(c1, n_cols)))

    return grid, row_heights, col_widths, merges, n_rows, n_cols


def _get_font(bold, italic, size_pt):
    path = FONT_REGULAR
    if bold and italic:
        path = FONT_BOLD_ITALIC
    elif bold:
        path = FONT_BOLD
    elif italic:
        path = FONT_ITALIC
    px = max(11, round(size_pt * 1.33)) * SCALE // 2
    return ImageFont.truetype(path, px)


def _fit_text(draw, text, bold, italic, size_pt, max_width):
    size = size_pt
    font = _get_font(bold, italic, size)
    width = draw.textlength(text, font=font)
    while width > max_width and size > 6:
        size -= 1
        font = _get_font(bold, italic, size)
        width = draw.textlength(text, font=font)
    return font, width


def render_grid_image(grid, row_heights, col_widths, merges, n_rows, n_cols, title, out_path):
    if not grid:
        # No data at all: render a simple placeholder image
        img = Image.new("RGB", (600, 100), "white")
        d = ImageDraw.Draw(img)
        d.text((20, 40), f"{title}: no data found in this range.", fill="black")
        img.save(out_path)
        return

    col_widths_px = [int(w * SCALE) for w in col_widths]
    row_heights_px = [int(h * SCALE) for h in row_heights]

    cum_x = [0]
    for w in col_widths_px:
        cum_x.append(cum_x[-1] + w)
    cum_y = [0]
    for h in row_heights_px:
        cum_y.append(cum_y[-1] + h)

    margin = MARGIN * SCALE
    W = cum_x[-1] + margin * 2
    H = cum_y[-1] + margin * 2

    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    covered = set()
    merge_map = {}
    for (r0, c0, r1, c1) in merges:
        merge_map[(r0, c0)] = (r1 - r0, c1 - c0)
        for rr in range(r0, r1):
            for cc in range(c0, c1):
                if (rr, cc) != (r0, c0):
                    covered.add((rr, cc))

    for r in range(n_rows):
        for c in range(n_cols):
            if (r, c) in covered:
                continue
            rowspan, colspan = merge_map.get((r, c), (1, 1))
            x0 = cum_x[c] + margin
            x1 = cum_x[min(c + colspan, n_cols)] + margin
            y0 = cum_y[r] + margin
            y1 = cum_y[min(r + rowspan, n_rows)] + margin

            cell = grid.get((r, c), {})
            bg = cell.get("bg", "#FFFFFF")
            text = cell.get("text", "")

            draw.rectangle([x0, y0, x1, y1], fill=bg, outline=BORDER_COLOR, width=1)

            if text:
                bold = cell.get("bold", False)
                italic = cell.get("italic", False)
                underline = cell.get("underline", False)
                align = cell.get("align", "LEFT")
                fg = cell.get("fg", "#000000")
                size_pt = cell.get("size", 10)

                pad = 6 * SCALE
                max_w = max(1, (x1 - x0) - pad * 2)
                font, tw = _fit_text(draw, text, bold, italic, size_pt, max_w)
                th = font.size

                if align == "CENTER":
                    tx = x0 + ((x1 - x0) - tw) / 2
                elif align == "RIGHT":
                    tx = x1 - pad - tw
                else:
                    tx = x0 + pad
                ty = y0 + ((y1 - y0) - th) / 2

                draw.text((tx, ty), text, font=font, fill=fg)
                if underline:
                    draw.line([(tx, ty + th + 1), (tx + tw, ty + th + 1)], fill=fg, width=SCALE)

    img.save(out_path)


def upload_image_to_slack(image_path, title, filename):
    """Upload a local image to Slack and share it in SLACK_CHANNEL_ID."""
    file_size = os.path.getsize(image_path)

    resp = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        data={"filename": filename, "length": file_size},
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"files.getUploadURLExternal failed: {data}")
    upload_url = data["upload_url"]
    file_id = data["file_id"]

    with open(image_path, "rb") as f:
        upload_resp = requests.post(upload_url, files={"file": f}, timeout=30)
    if upload_resp.status_code != 200:
        raise RuntimeError(
            f"File upload to {upload_url} failed: "
            f"{upload_resp.status_code} {upload_resp.text}"
        )

    complete_resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "files": [{"id": file_id, "title": title}],
            "channel_id": SLACK_CHANNEL_ID,
            "initial_comment": title,
        },
        timeout=15,
    )
    complete_data = complete_resp.json()
    if not complete_data.get("ok"):
        raise RuntimeError(f"files.completeUploadExternal failed: {complete_data}")


def main():
    service = get_sheets_service()
    for table in TABLES:
        grid, row_heights, col_widths, merges, n_rows, n_cols = fetch_sheet_grid(
            service, SHEET_TAB, table["range"]
        )
        image_path = os.path.join(tempfile.gettempdir(),table["filename"])
        render_grid_image(
            grid, row_heights, col_widths, merges, n_rows, n_cols, table["title"], image_path
        )
        upload_image_to_slack(image_path, table["title"], table["filename"])
        print(f"Posted: {table['title']} ({n_rows} rows x {n_cols} cols)")


if __name__ == "__main__":
    main()
