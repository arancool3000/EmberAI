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
`version.py` defaults to this repo (`arancool3000/atlas`). For a different
account/repo, edit it — or set env vars (handy for CI) without editing the file:
```python
GITHUB_OWNER = os.environ.get("EMBER_GITHUB_OWNER", "your-github-username")
GITHUB_REPO  = os.environ.get("EMBER_GITHUB_REPO", "atlas")
```
The updater, website, and release script all read from here.

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

## Notarization (optional — zero Gatekeeper warning, like the Claude app)

By default the build is **ad-hoc signed** (free, but the first launch needs **Open
Anyway**). To make Ember open with **no warning at all** — exactly like a notarized
app such as Claude — give `RELEASE.command` an Apple Developer ID and it will sign,
notarize, and staple automatically (and notarize the `.dmg` too). This needs a paid
**Apple Developer account ($99/yr)**.

One-time setup:
1. In Xcode (or developer.apple.com) create a **Developer ID Application** certificate
   and install it in your login keychain.
2. Make an **app-specific password** at <https://appleid.apple.com> → Sign-In & Security.
3. Store notary credentials once:
   ```bash
   xcrun notarytool store-credentials ember-notary \
     --apple-id you@example.com --team-id TEAMID --password APP-SPECIFIC-PASSWORD
   ```
4. Export these before running `./RELEASE.command` (e.g. add to `~/.zshrc`):
   ```bash
   export EMBER_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
   export EMBER_NOTARY_PROFILE="ember-notary"
   ```
That's it — `RELEASE.command` (via `notarize_mac.sh`) detects the identity and produces
a notarized, stapled `Ember.app`, `Ember-macOS.zip`, and `Ember.dmg`. Without those env
vars it stays on the free ad-hoc path unchanged.

## Notes & gotchas
- **Gatekeeper:** ad-hoc builds (no `EMBER_SIGN_IDENTITY`) need **Open Anyway** on first
  launch; the updater strips the download quarantine so *updates* don't re-prompt. Set up
  notarization (above) to remove the warning entirely.
- **Bundle zipping must use `ditto`** (not `zip`) or the `.app` signature/symlinks break — the
  script already does, and the updater extracts with `ditto -x -k`.
- **Auto-update only runs from the built `Ember.app`** (not `python3 main.py`) and only once
  `GITHUB_OWNER` is set — it's a silent no-op otherwise.
- The updater keeps a `.app.old` backup during the swap and rolls back if the new bundle fails
  to install, so a bad update can't leave the user with no app.
