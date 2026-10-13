(() => {
  const link = document.getElementById("download-link");
  const status = document.getElementById("download-status");
  const config = window.DOWNLOAD_CONFIG;

  if (!link || !status || !config) return;

  const apiUrl = `https://api.github.com/repos/${encodeURIComponent(config.owner)}/${encodeURIComponent(config.repository)}/releases/latest`;

  fetch(apiUrl, { headers: { Accept: "application/vnd.github+json" } })
    .then(async (response) => {
      if (response.status === 404) return null;
      if (!response.ok) throw new Error(`GitHub API: ${response.status}`);
      return response.json();
    })
    .then((release) => {
      const asset = release?.assets?.find(
        (item) => item.name === config.assetName && item.state === "uploaded"
      );

      if (!asset?.browser_download_url) {
        status.textContent = "아직 다운로드 파일이 등록되지 않았습니다.";
        return;
      }

      const url = new URL(asset.browser_download_url);
      const expectedPath = `/${config.owner}/${config.repository}/releases/download/`;
      if (url.protocol !== "https:" || url.hostname !== "github.com" || !url.pathname.startsWith(expectedPath)) {
        throw new Error("Unexpected release asset URL");
      }

      link.href = url.href;
      link.removeAttribute("aria-disabled");
      link.removeAttribute("tabindex");
      status.textContent = `${release.name || release.tag_name} 버전을 다운로드할 수 있습니다.`;
    })
    .catch(() => {
      status.textContent = "다운로드 정보를 확인할 수 없습니다. 잠시 후 다시 방문해 주세요.";
    });
})();
