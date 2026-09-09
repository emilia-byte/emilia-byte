# page-ops-scripts

Automates Facebook Page publishing through Multilogin X browser profiles. Generates posts from a local template bank, downloads matching images, and publishes them to the page — with human-like timing and image attachment.

---

## Quick start (non-technical)

1. Install Python from [python.org](https://www.python.org/downloads/) — on the first installer screen, tick **"Add Python to PATH"**
2. Double-click **`setup.bat`** — installs all dependencies and the Chromium browser (~150 MB, a few minutes)
3. Double-click **`run.bat`** — follow the prompts to pick an account, mode, and category

On first run you will be asked for your Multilogin email and password. They are saved to `.env` so you are not asked again.

---

## Quick start (command line)

```bash
# Set credentials (once per shell session, or add to your shell profile)
export MLX_EMAIL=you@example.com
export MLX_PASSWORD=yourpassword

# Full run: generate posts then publish
python generate_posts.py --account EMI_AUTO_2
python post.py --account EMI_AUTO_2 --posts posts.txt

# First time logging in to a profile
python login.py --account EMI_AUTO_2 --manual
```

---

## Requirements

- Python 3.9+
- A Multilogin X subscription with browser profiles already created
- Profiles must be logged in to Facebook (see [First-time login](#first-time-login))

---

## First-time login

Each Multilogin profile needs to be logged in to Facebook once before posting. This handles CAPTCHAs and 2FA manually:

```bash
python login.py --account EMI_AUTO_2 --manual
```

Log in as normal in the browser that opens. The script detects when you are done and Multilogin persists the session automatically. You do not need to repeat this unless the session expires.

---

## Accounts

Accounts are defined in `mlx_profiles.json`:

```json
{
  "EMI_AUTO_2": {
    "folder_id": "...",
    "profile_id": "..."
  }
}
```

The account name must match the Multilogin profile exactly. To add a new account, copy the folder and profile UUIDs from the Multilogin dashboard and add an entry here.

Account names follow the convention `EMI_AUTO_N`. The Facebook Page managed by each profile must contain a category suffix in its name:

| Suffix | Category |
|--------|----------|
| `LS`   | Lifestyle |
| `HOB`  | Hobbies |
| `CSI`  | Career and Self Improvement |
| `MF`   | Market and Finance |

`generate_posts.py` reads this suffix from the page name shown in the Facebook sidebar to select the right post templates automatically.

---

## Scripts

| Script | Purpose |
|--------|---------|
| `run.py` / `run.bat` | Interactive launcher — no flags needed |
| `setup.bat` | First-time dependency installer (Windows) |
| `generate_posts.py` | Detects page category, generates `posts.txt` and `images/`, downloads images via Pollinations.ai |
| `post.py` | Publishes posts from `posts.txt` to the Facebook Page |
| `login.py` | Manual login helper for first-time or expired sessions |
| `multilogin_client.py` | Multilogin X REST client |
| `mlx_profiles.json` | Account → Multilogin UUID mapping |

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `MLX_EMAIL` | Multilogin account email |
| `MLX_PASSWORD` | Multilogin account password |

Set these in your shell, or let `run.py` prompt for them and save to `.env`.

---

## How it works

```
generate_posts.py
    → opens Multilogin profile
    → reads page category from Facebook sidebar
    → builds 3 posts from template bank (opener + body + closer + hashtags)
    → downloads matching images from Pollinations.ai
    → writes posts.txt and images/

post.py
    → connects to Multilogin profile via Playwright CDP
    → switches to page context via sidebar link
    → for each post: opens composer, types text, attaches image, submits
    → waits 45–90 s between posts
```

Playwright connects to the Multilogin-managed browser via CDP (`connect_over_cdp`) rather than launching its own browser. This gives Multilogin control over fingerprinting, proxy routing, and session persistence.
