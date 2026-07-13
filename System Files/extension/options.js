const keyEl = document.getElementById("key");
const modelEl = document.getElementById("model");
const okEl = document.getElementById("ok");

chrome.storage.sync.get(["emberKey", "emberModel"]).then(({ emberKey, emberModel }) => {
  if (emberKey) keyEl.value = emberKey;
  if (emberModel) modelEl.value = emberModel;
});

document.getElementById("save").onclick = async () => {
  await chrome.storage.sync.set({
    emberKey: keyEl.value.trim().replace(/\s+/g, ""),
    emberModel: modelEl.value.trim(),
  });
  okEl.textContent = "Saved ✓";
  setTimeout(() => (okEl.textContent = ""), 1500);
};
