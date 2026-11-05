/* memsave extension options: server URL, permissions and a health check. */
const DEFAULT_SERVER = "http://localhost:8780";
const RELEASE_BASE = "https://github.com/northis/memsave/releases/tag";

const serverInput = document.getElementById("server");
const statusEl = document.getElementById("status");
const extVersion = document.getElementById("ext-version");
const serverBuild = document.getElementById("server-build");

function setStatus(text, tone = "") {
  statusEl.className = `status ${tone}`.trim();
  statusEl.textContent = text;
}

function normalizeServer(value) {
  let server = String(value || "").trim().replace(/\/+$/, "");
  if (!server) return DEFAULT_SERVER;
  if (!/^https?:\/\//i.test(server)) server = `http://${server}`;
  return server;
}

function originOf(server) {
  try {
    return new URL(server).origin;
  } catch {
    return null;
  }
}

async function getServer() {
  const data = await chrome.storage.sync.get(["serverUrl"]);
  return normalizeServer(data.serverUrl || DEFAULT_SERVER);
}

async function requestServerAccess(server) {
  const origin = originOf(server);
  if (!origin) throw new Error("Некорректный адрес сервера");
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  if (!granted) throw new Error("Доступ к серверу не разрешён");
}

async function checkConnection(server = null) {
  const target = normalizeServer(server || serverInput.value);
  setStatus("Проверяю соединение…");
  try {
    const health = await fetch(`${target}/health`, { credentials: "include", cache: "no-store" });
    const type = health.headers.get("content-type") || "";
    if (!health.ok) throw new Error(`health HTTP ${health.status}`);
    if (!type.includes("application/json")) throw new Error("AUTH");
    const versionResponse = await fetch(`${target}/api/version`, { credentials: "include", cache: "no-store" });
    const versionType = versionResponse.headers.get("content-type") || "";
    if (!versionResponse.ok) throw new Error(`version HTTP ${versionResponse.status}`);
    if (!versionType.includes("application/json")) throw new Error("AUTH");
    const info = await versionResponse.json();
    const tag = info.tag || `v${info.version}`;
    const link = info.tag_url || `${RELEASE_BASE}/${tag}`;
    serverBuild.replaceChildren();
    serverBuild.innerHTML = `Сервер: <a href="${link}" target="_blank" rel="noopener">${tag}</a>`;
    if (info.short) {
      const commit = document.createElement("span");
      commit.textContent = ` · ${info.short}${info.dirty ? "*" : ""}`;
      if (info.commit_url) {
        const anchor = document.createElement("a");
        anchor.href = info.commit_url;
        anchor.target = "_blank";
        anchor.rel = "noopener";
        anchor.textContent = info.short + (info.dirty ? "*" : "");
        commit.replaceChildren(" · ", anchor);
      }
      serverBuild.appendChild(commit);
    }
    setStatus("Соединение работает. Контекстное меню готово.", "ok");
    return true;
  } catch (error) {
    if (String(error && error.message) === "AUTH") {
      setStatus("Нужно войти: откройте memsave в браузере и пройдите Cloudflare Access.", "error");
    } else {
      setStatus(`Нет соединения: ${error.message}`, "error");
    }
    return false;
  }
}

async function save() {
  let server;
  try {
    server = normalizeServer(serverInput.value);
    const origin = originOf(server);
    if (!origin) throw new Error("Некорректный адрес");
  } catch (error) {
    setStatus(error.message, "error");
    return;
  }
  serverInput.value = server;
  try {
    await requestServerAccess(server);
  } catch (error) {
    setStatus(error.message, "error");
    return;
  }
  await chrome.storage.sync.set({ serverUrl: server });
  await checkConnection(server);
}

async function grantAllSites() {
  try {
    const granted = await chrome.permissions.request({
      origins: ["http://*/*", "https://*/*"],
    });
    if (!granted) {
      setStatus("Доступ к сайтам картинок не разрешён.", "error");
      return;
    }
    setStatus("Доступ к сайтам картинок разрешён.", "ok");
  } catch (error) {
    setStatus(`Не удалось выдать доступ: ${error.message}`, "error");
  }
}

async function init() {
  const server = await getServer();
  serverInput.value = server;
  const version = chrome.runtime.getManifest().version;
  extVersion.href = `${RELEASE_BASE}/v${version}`;
  extVersion.textContent = `v${version}`;
  await checkConnection(server);
}

document.getElementById("save").addEventListener("click", save);
document.getElementById("test").addEventListener("click", () => checkConnection());
document.getElementById("grant").addEventListener("click", grantAllSites);
serverInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") save();
});
init();
