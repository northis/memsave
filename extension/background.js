/* memsave Chrome MV3 worker: context menu on an image -> upload bytes. */
const DEFAULT_SERVER = "http://localhost:8780";
const MENU_ID = "memsave-image";

async function getServer() {
  const data = await chrome.storage.sync.get(["serverUrl"]);
  const value = String(data.serverUrl || DEFAULT_SERVER).trim().replace(/\/+$/, "");
  return value || DEFAULT_SERVER;
}

function setBadge(text, color, title) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
  if (title) chrome.action.setTitle({ title });
  if (text) {
    setTimeout(() => {
      chrome.action.setBadgeText({ text: "" });
      chrome.action.setTitle({ title: "memsave" });
    }, 2600);
  }
}

function errorMessage(error) {
  const value = String((error && error.message) || error || "error");
  if (value === "NO_PERMISSION") return "Нет доступа к сайту картинки — откройте настройки расширения";
  if (value === "AUTH") return "Откройте memsave и войдите через Cloudflare Access";
  if (value === "BLOB") return "Картинка с временным blob-адресом — сохраните её через сайт";
  return `Не удалось отправить: ${value}`;
}

async function fetchImage(url) {
  if (url.startsWith("data:")) {
    const response = await fetch(url);
    if (!response.ok) throw new Error("data image");
    return response.blob();
  }
  if (!/^https?:\/\//i.test(url)) throw new Error("unsupported image URL");
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error("bad image URL");
  }
  const originPattern = `${parsed.origin}/*`;
  const granted = await chrome.permissions.contains({ origins: [originPattern] });
  if (!granted) {
    let requested = false;
    try {
      requested = await chrome.permissions.request({ origins: [originPattern] });
    } catch {
      requested = false;
    }
    if (!requested) throw new Error("NO_PERMISSION");
  }
  const response = await fetch(url, { credentials: "omit", cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const type = response.headers.get("content-type") || "";
  if (type && !type.startsWith("image/")) throw new Error("not an image");
  return response.blob();
}

async function uploadBytes(server, blob, info, tab) {
  const src = info.srcUrl || "";
  const isData = src.startsWith("data:");
  const filename = isData ? "image" : (src.split("/").pop().split("?")[0] || "image");
  const headers = {
    "Content-Type": blob.type || "application/octet-stream",
    "X-Memsave-Request": "1",
    "X-Memsave-Source": "extension",
    "X-Memsave-Page-URL": encodeURI(info.pageUrl || ""),
    "X-Memsave-Image-URL": isData ? "" : encodeURI(src),
    "X-Memsave-Filename": encodeURIComponent(filename),
    "X-Memsave-Title": encodeURIComponent(tab && tab.title ? tab.title : ""),
  };
  const response = await fetch(`${server}/api/images`, {
    method: "POST",
    headers,
    body: blob,
    credentials: "include",
  });
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) throw new Error("AUTH");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function saveLink(server, url, title) {
  const response = await fetch(`${server}/api/links`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Memsave-Request": "1" },
    body: JSON.stringify({ url, title }),
    credentials: "include",
  });
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) throw new Error("AUTH");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function sendImage(info, tab) {
  const server = await getServer();
  const src = info.srcUrl || "";
  if (!src) throw new Error("no image URL");
  if (src.startsWith("blob:")) throw new Error("BLOB");

  let blob = null;
  let imageError = null;
  try {
    blob = await fetchImage(src);
  } catch (error) {
    imageError = error;
  }

  if (blob) {
    try {
      return await uploadBytes(server, blob, info, tab);
    } catch (error) {
      // A hotlink or an expiry can still be resolved by the server-side preview
      // fetcher; fall through to the link endpoint.
      imageError = error;
    }
  }

  try {
    return await saveLink(server, src, tab && tab.title);
  } catch (error) {
    if (String(imageError && imageError.message) === "NO_PERMISSION") throw imageError;
    throw error;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_ID,
      title: "Отправить в memsave",
      contexts: ["image"],
    });
  });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_ID,
      title: "Отправить в memsave",
      contexts: ["image"],
    });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID) return;
  setBadge("…", "#6d28d9", "memsave: отправляю…");
  sendImage(info, tab)
    .then(() => {
      setBadge("✓", "#3f6b3a", "memsave: сохранено, AI размечает в фоне");
    })
    .catch((error) => {
      setBadge("!", "#b42318", errorMessage(error));
      if (String(error && error.message) === "AUTH") {
        getServer().then((server) => chrome.tabs.create({ url: server })).catch(() => {});
      }
    });
});

chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});
