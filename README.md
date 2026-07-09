# Daily Sheet → Slack Summary (Image Version)

Renders two tables from your "Daily Summary" tab (Region-wise and SPOC-wise
summaries) as images that look like the sheet, and posts them to a Slack
channel every day at **10:30 PM IST**, automatically, via GitHub Actions.

## 1. Create a repo
Put these three items in a GitHub repo (keep the folder structure exactly
as-is):
```
post_sheet_summary.py
requirements.txt
.github/workflows/daily-slack-summary.yml
```

## 2. Share the sheet with your service account
Open your existing Google service account JSON key and find the
`"client_email"` field (looks like
`something@your-project.iam.gserviceaccount.com`).

In your Google Sheet, click **Share** and give that email address
**Viewer** access. Without this step the script can't read the sheet.

## 3. Create a Slack Bot (needed for image uploads)
Slack's Incoming Webhooks can only post text messages — they can't upload
files/images. Posting a rendered table image requires a bot token instead.

1. Go to https://api.slack.com/apps → **Create New App** → "From scratch"
2. Go to **OAuth & Permissions** → under **Scopes → Bot Token Scopes**, add:
   - `files:write`
   - `chat:write`
   - `channels:read` (or `groups:read` if it's a private channel)
3. Click **Install to Workspace** at the top of that page, and approve it
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)
5. Invite the bot to your target channel: in Slack, open the channel and
   type `/invite @YourAppName`
6. Get the channel ID: open the channel in Slack → click the channel name
   at the top → scroll down → copy the **Channel ID** (starts with `C`)

## 4. Add GitHub Secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add these four:

| Secret name | Value |
|---|---|
| `SHEET_ID` | `1pEanYKVZ8gX-dUGN136uWooGOB0UiZsXtyddNikZGRg` (from your sheet URL) |
| `SLACK_BOT_TOKEN` | the `xoxb-...` token from step 3 |
| `SLACK_CHANNEL_ID` | the channel ID from step 3 |
| `GOOGLE_CREDENTIALS_JSON` | paste the **entire contents** of your service account JSON key file |

## 5. Test it
Go to the **Actions** tab in your repo → select **Daily Slack Summary** →
**Run workflow** (this uses the `workflow_dispatch` trigger). Check your
Slack channel for the two table images.

## 6. It's now automated
The workflow runs every day at 17:00 UTC (10:30 PM IST) on its own — no
further action needed.

### Notes
- The script assumes the first row of each range is a header row and
  styles it accordingly. If that's not the case, remove the header-styling
  loop in `render_table_image()`.
- GitHub Actions' free-tier scheduler can occasionally run a few minutes
  late during periods of high load; it's reliable but not to-the-second.
- If you ever change the ranges (`A27:R33` / `A81:R99`) or the tab name,
  just update the `TABLES` list at the top of `post_sheet_summary.py`.
- Very wide tables (many columns) will produce a wide image — Slack will
  auto-scale it down for preview, and it stays viewable at full size on
  click.
