/* memsave service worker: PWA shell cache and Web Share Target. */
const CACHE = "memsave-v1";
const ASSETS = [
  "/static/style.css",
  "/static/app.js",
  "/static/favicon.svg",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function absolute(path) {
  return new URL(path, self.location.origin).toString();
}

function redirect(params) {
  const query = new URLSearchParams(params).toString();
  return Response.redirect(absolute(`/share?${query}`), 303);
}

function firstUrl(text) {
  const match = String(text || "").match(/https?:\/\/[^\s<>"']+/i);
  return match ? match[0].replace(/[.,);\]}>"']+$/, "") : "";
}

async function uploadFile(file, title) {
  const response = await fetch("/api/images", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-Memsave-Request": "1",
      "X-Memsave-Source": "share",
      "X-Memsave-Filename": encodeURIComponent(file.name || "image"),
      "X-Memsave-Title": encodeURIComponent(title || ""),
    },
    body: file,
  });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error("auth");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function createLinkJob(url, title) {
  const response = await fetch("/api/links", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Memsave-Request": "1" },
    body: JSON.stringify({ url, title }),
  });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) throw new Error("auth");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data.job_id;
}

async function handleShare(request) {
  try {
    const form = await request.formData();
    const files = form.getAll("files").filter(
      (value) => value && typeof value !== "string" && (value.type || "").startsWith("image/")
    );
    const title = String(form.get("title") || "");
    const text = String(form.get("text") || "");
    const url = String(form.get("url") || "") || firstUrl(text);

    if (files.length) {
      let count = 0;
      let firstImage = null;
      for (const file of files) {
        const data = await uploadFile(file, title);
        if (data.image) {
          count += 1;
          if (!firstImage) firstImage = data.image.id;
        }
      }
      const params = { status: "saved", count: String(count) };
      if (firstImage) params.image = String(firstImage);
      return redirect(params);
    }

    if (url) {
      const jobId = await createLinkJob(url, title);
      return redirect({ status: "link", job: String(jobId) });
    }

    return redirect({ error: "empty" });
  } catch (error) {
    const reason = error && error.message === "auth" ? "auth" : "upload";
    return redirect({ error: reason });
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "POST" && url.pathname === "/share") {
    event.respondWith(handleShare(event.request));
    return;
  }
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/images/") ||
    url.pathname.startsWith("/thumbs/")
  ) {
    return;
  }
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => caches.match(event.request).then((cached) => cached || Response.error()))
    );
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match("/").then((cached) => cached || new Response("memsave недоступен", { status: 503 }))
      )
    );
  }
});
