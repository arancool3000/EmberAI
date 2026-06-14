/* Ember site — OS detection, release manifest, nav. Owner/repo synced from version.py
   by the release script (sed on the next line). */
const EMBER = { owner: "arancool3000", repo: "ember" };
EMBER.repoUrl = `https://github.com/${EMBER.owner}/${EMBER.repo}`;
EMBER.releasesUrl = `${EMBER.repoUrl}/releases/latest`;

function detectOS() {
  const ua = (navigator.userAgent || "") + " " + (navigator.platform || "");
  if (/Win/i.test(ua)) return "windows";
  if (/Mac|iPhone|iPad|iPod/i.test(ua)) return "macos";
  return "macos";
}
const OS = detectOS();
const OS_LABEL = { macos: "macOS", windows: "Windows" };
const ASSET = { macos: "Ember-macOS.zip", windows: "Ember-Windows.zip" };

function dlUrl(manifest, os) {
  const d = (manifest.downloads || {})[os];
  if (d && d.url) return d.url;
  return `${EMBER.releasesUrl}/download/${ASSET[os]}`;
}

function applyDownloads(manifest) {
  const ver = manifest && manifest.version ? manifest.version : "";
  document.querySelectorAll("[data-ver]").forEach(e => e.textContent = ver ? "v" + ver : "latest");
  document.querySelectorAll("[data-pubdate]").forEach(e => e.textContent = (manifest && manifest.pub_date) || "");
  document.querySelectorAll("[data-notes]").forEach(e =>
    e.textContent = ((manifest && manifest.notes) || "").trim() || "Release notes will appear here once the first version ships.");

  document.querySelectorAll("[data-dl='primary']").forEach(b => {
    b.href = dlUrl(manifest, OS);
    const t = b.querySelector("[data-dl-text]");
    if (t) t.textContent = `Download for ${OS_LABEL[OS]}` + (ver ? ` · ${ver}` : "");
  });
  document.querySelectorAll("[data-dl='macos']").forEach(b => b.href = dlUrl(manifest, "macos"));
  document.querySelectorAll("[data-dl='windows']").forEach(b => b.href = dlUrl(manifest, "windows"));

  document.querySelectorAll("[data-oscard]").forEach(c => {
    const isPrimary = c.getAttribute("data-oscard") === OS;
    c.classList.toggle("primary", isPrimary);
    const badge = c.querySelector(".badge");
    if (badge) badge.style.display = isPrimary ? "" : "none";
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-gh]").forEach(a => a.href = EMBER.repoUrl);
  document.querySelectorAll("[data-os-label]").forEach(e => e.textContent = OS_LABEL[OS]);

  let page = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  if (page === "") page = "index.html";
  document.querySelectorAll(".navlinks a[data-page]").forEach(a => {
    if (a.getAttribute("data-page") === page) a.classList.add("active");
  });

  applyDownloads({}); // predictable URLs first, so buttons work before the fetch resolves
  fetch("./latest.json", { cache: "no-store" })
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(applyDownloads)
    .catch(() => {});
});
