# Publishing Ember — download website + auto-updates

This wires up the **download website** (GitHub Pages) and the **in-app auto-updater**
(GitHub Releases). One-time setup, then every release is a single `./RELEASE.command`.

Everything keys off **one value**: `GITHUB_OWNER` in `version.py`.

---

## One-time setup (≈10 min)

### 1. Create the GitHub repo
Make a repo named **`ember`** under your account (public, so Pages + release downloads are free).
- Web: <https://github.com/new> → name `ember` → Create.
- Or CLI: `brew install gh && gh auth login` then `gh repo create ember --public`.

> Using a different name? Set `GITHUB_REPO` in `version.py` to match.

### 2. Point Ember at your account
Edit `version.py`:
```python
GITHUB_OWNER = "your-github-username"   # <- change this
GITHUB_REPO  = "ember"                   # (only if you named the repo differently)
```
That's the only edit needed — the updater, website, and release script all read from here.

### 3. Push the code
```bash
cd ~/Desktop/EmberMac
git init && git add -A && git commit -m "Ember"
git branch -M main
git remote add origin https://github.com/your-github-username/ember.git
git push -u origin main
```

### 4. Turn on GitHub Pages (the website)
On GitHub: **repo → Settings → Pages**
- **Source:** Deploy from a branch
- **Branch:** `main`  ·  **Folder:** `/docs`  → Save.

After ~1 minute your download page is live at:
**`https://your-github-username.github.io/ember/`**

### 5. Cut the first release
```bash
./RELEASE.command 1.0.0
```
This builds `Ember.app`, zips it, generates `latest.json`, and (if `gh` is installed/authed)
creates the GitHub release automatically. Without `gh`, it prints the exact manual upload steps.

Done. The website's **Download** button now serves your build, and any installed copy
auto-updates to the newest release on launch.

---

## Every release after that

1. Edit `RELEASE_NOTES.md` with what changed.
2. **macOS** (on a Mac): `./RELEASE.command` (auto-bumps the patch, e.g. 1.0.0 → 1.0.1) or
   `./RELEASE.command 1.1.0`.
3. **Windows** (on a PC, for the *same* version): pull the repo, then `./RELEASE-windows.ps1`
   (uses the version already in `version.py` — no re-bump) or `./RELEASE-windows.ps1 -Version 1.1.0`
   if you're releasing Windows first.

Each script: syncs the site → builds → packs its OS's zip (`Ember-macOS.zip` / `Ember-Windows.zip`)
→ checksums → updates **its own** entry in `latest.json` (preserving the other OS's download) →
uploads to the same GitHub release tag → pushes `docs/` so Pages updates. The website auto-detects
the visitor's OS and offers the right build; each installed app auto-updates from its own entry.

> **One OS only?** Just run that platform's script — the site still shows both download buttons
> (the missing OS points at the release page until you publish that build).

---

## How the pieces fit
| Piece | File | Role |
|---|---|---|
| Version source of truth | `version.py` | `__version__` + GitHub owner/repo |
| Updater (in app) | `updater.py` | Fetches `releases/latest/.../latest.json`, compares, downloads, verifies sha256, strips quarantine, swaps the `.app`, relaunches |
| Update manifest | `docs/latest.json` (+ release asset) | `{version, url, sha256, notes, pub_date}` |
| Website | `docs/index.html` | Static landing page; reads `latest.json` live |
| Release pipeline | `RELEASE.command` + `_release_helper.py` | Build → publish |

## Notes & gotchas
- **Gatekeeper:** builds are ad-hoc signed (not notarized), so first launch needs
  **right-click → Open** (the site says so). The updater strips the download quarantine
  automatically, so *updates* don't re-prompt. To remove the warning entirely, notarize with an
  Apple Developer ID and add `xcrun notarytool` to `RELEASE.command`.
- **Bundle zipping must use `ditto`** (not `zip`) or the `.app` signature/symlinks break — the
  script already does, and the updater extracts with `ditto -x -k`.
- **Auto-update only runs from the built `Ember.app`** (not `python3 main.py`) and only once
  `GITHUB_OWNER` is set — it's a silent no-op otherwise.
- The updater keeps a `.app.old` backup during the swap and rolls back if the new bundle fails
  to install, so a bad update can't leave the user with no app.
